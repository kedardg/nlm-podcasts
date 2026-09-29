#!/usr/bin/env python3
"""Reclaim NotebookLM slots by capturing notebooks to the archive, then deleting.

The account is capped at 500 notebooks and nothing ever deleted one, so
`notebook create` fails and the pipeline is down. This walks the frozen
allowlist, captures each notebook's artifacts and source text to
``data/archive/notebooks/<id>/``, proves the capture, and only then deletes.

Safety properties, in the order they matter:

* Deletion authority comes from a frozen, digest-verified id list — never from
  matching titles at delete time.
* Every delete is gated by :mod:`pipeline.capture_verify`; any doubt routes the
  notebook to ``needs_human`` and leaves it alive.
* A hard per-run cap bounds the blast radius of a mistaken invocation.
* ``--execute`` refuses unless a plan produced by a prior ``--plan`` pass names
  exactly the notebooks about to be destroyed.
* Resume is derived from state already on disk, so SIGKILL at any point is safe.

Ordering puts notebooks with no retrieval rows first: they are already
unreachable from `/chat`, so reclaiming them frees slots at zero cost.

Usage:
    .venv/bin/python3 src/pipeline/reclaim_notebooks.py --plan
    .venv/bin/python3 src/pipeline/reclaim_notebooks.py --no-delete --limit 5
    .venv/bin/python3 src/pipeline/reclaim_notebooks.py --execute --limit 5
"""

import argparse
import atexit
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lib import archive_db                                    # noqa: E402
from lib.file_utils import now_iso, save_json                 # noqa: E402
from lib.notebook_allowlist import (                          # noqa: E402
    AllowlistError, cap_for_run, load_allowlist,
)
from nlm import client                                        # noqa: E402
from pipeline import notebook_capture as capture_mod          # noqa: E402

DATA = PROJECT_ROOT / "data"
CONFIG = PROJECT_ROOT / "config"
PLAN_PATH = DATA / "delete-manifest.json"
AUTH_SCRIPT = PROJECT_ROOT / "scripts" / "nlm_ensure_auth.sh"

# Google rotates session cookies roughly every 10 minutes, so a long run
# re-checks well inside that window rather than waiting for a failure.
AUTH_RECHECK_EVERY = 15


def _acquire_lock(lock_path):
    """Take the shared pipeline lock, honouring a live holder's PID.

    Mirrors scripts/pipeline_lock.sh: the lock file holds the owner's PID, and
    a stale one is only reclaimed when that process is genuinely gone.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.is_dir():
        try:
            lock_path.rmdir()
        except OSError:
            return None
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        try:
            holder = int(lock_path.read_text().strip() or 0)
        except (ValueError, OSError):
            holder = 0
        if holder:
            try:
                os.kill(holder, 0)
                return None            # holder is alive
            except (ProcessLookupError, PermissionError) as exc:
                if isinstance(exc, PermissionError):
                    return None        # alive, owned by someone else
        log(f"reclaiming lock from dead PID {holder or 'unknown'}")
        try:
            lock_path.unlink()
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except OSError:
            return None
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    atexit.register(_release_lock, lock_path)
    return lock_path


def _release_lock(lock_path):
    """Only ever release a lock this process owns."""
    try:
        if Path(lock_path).read_text().strip() == str(os.getpid()):
            Path(lock_path).unlink()
    except OSError:
        pass


def log(msg):
    print(f"[{now_iso()}] {msg}", flush=True)


def ensure_auth():
    if not AUTH_SCRIPT.exists():
        return True
    res = subprocess.run(["bash", str(AUTH_SCRIPT)], capture_output=True,
                         text=True, timeout=300)
    return res.returncode == 0


def retrieval_notebook_ids():
    """Notebook ids that currently have rows in the vector store.

    These are the only ones `/chat` can route to, so they are reclaimed last.
    """
    db = PROJECT_ROOT / "rag" / "chroma_db" / "chroma.sqlite3"
    if not db.exists():
        return set()
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT DISTINCT string_value FROM embedding_metadata"
            " WHERE key='notebook_id' AND string_value IS NOT NULL"
        ).fetchall()
        conn.close()
        return {r[0] for r in rows}
    except sqlite3.Error:
        return set()


def build_order(authority, live_by_id, done_ids):
    """Oldest first, and retrieval-invisible notebooks before visible ones."""
    routable = retrieval_notebook_ids()
    pending = [i for i in authority.ids if i in live_by_id and i not in done_ids]
    return sorted(
        pending,
        key=lambda i: (i in routable, live_by_id[i].get("updated_at") or ""),
    )


def skip_ids(conn, retry_needs_human=False):
    """Notebooks this run should not touch.

    'needs_human' is terminal by design: the gate refused, and re-running
    re-downloads every artifact only to refuse again. Left in the automatic
    rotation it is pure cost — and it grows, so each batch gets slower.
    """
    states = ["deleted", "protected"]
    if not retry_needs_human:
        states.append("needs_human")
    placeholders = ",".join("?" * len(states))
    rows = conn.execute(
        f"SELECT notebook_id FROM notebooks WHERE state IN ({placeholders})", states
    ).fetchall()
    return {r["notebook_id"] for r in rows}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true",
                      help="write the delete manifest and exit; destroys nothing")
    mode.add_argument("--no-delete", action="store_true",
                      help="capture and verify, but never delete")
    mode.add_argument("--execute", action="store_true",
                      help="capture, verify and DELETE. Irreversible.")
    ap.add_argument("--limit", type=int, help="process at most N notebooks")
    ap.add_argument("--target-free", type=int,
                    help="stop once this many notebook slots are free")
    ap.add_argument("--account-cap", type=int, default=500)
    ap.add_argument("--allow-thin-sources", action="store_true",
                    help="accept sources whose full text is shorter than the "
                         "per-type floor (placeholder URLs, stub pages). Every "
                         "other verification condition still applies.")
    ap.add_argument("--retry-needs-human", action="store_true",
                    help="re-attempt notebooks the gate previously refused "
                         "(after fixing the cause, or with an override flag)")
    ap.add_argument("--allow-empty", action="store_true",
                    help="reclaim notebooks that hold no sources at all, when "
                         "the frozen snapshot agrees they never did.")
    ap.add_argument("--data-dir", default=str(DATA))
    args = ap.parse_args()

    if not (args.plan or args.no_delete or args.execute):
        args.no_delete = True

    data_dir = Path(args.data_dir)
    run_id = f"reclaim_{now_iso().replace(':', '').replace('-', '')}"

    # Share the pipeline lock: reclaim deletes notebooks the daily run may be
    # creating, and two reclaims could select the same notebook. --plan reads
    # only, so it does not contend.
    lock = None
    if not args.plan:
        lock = _acquire_lock(data_dir / ".pipeline.lock")
        if lock is None:
            sys.exit("another pipeline or reclaim holds the lock; try later")

    if not ensure_auth():
        sys.exit("NLM auth could not be established; refusing to start")

    try:
        live = client.notebook_list()
    except client.NlmError as exc:
        sys.exit(f"cannot enumerate notebooks: {exc}")
    live_by_id = {n["id"]: n for n in live if n.get("id")}
    log(f"account holds {len(live_by_id)} notebooks")

    try:
        authority = load_allowlist(
            data_dir / "backfill-allowlist.json",
            data_dir / "backfill-excluded.json",
            CONFIG / "backfill-allowlist.sha256",
            live_ids=live_by_id.keys(),
        )
    except AllowlistError as exc:
        sys.exit(f"allowlist refused: {exc}")
    log(f"allowlist verified: {len(authority)} deletable, digest {authority.digest[:12]}")

    conn = archive_db.open_db(data_dir / "research.db")
    with archive_db.write_txn(conn):
        conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, kind, run_date, started_at, status)"
            " VALUES (?, 'reclaim', ?, ?, 'running')",
            (run_id, now_iso()[:10], now_iso()),
        )
        # Protected notebooks get a terminal row so the delete query cannot
        # select them even if the allowlist were somehow wrong.
        for nid in authority._excluded:
            meta = live_by_id.get(nid)
            if meta:
                conn.execute(
                    "INSERT OR IGNORE INTO notebooks (notebook_id, title, origin, state)"
                    " VALUES (?,?,'protected','protected')",
                    (nid, meta.get("title") or ""),
                )

    order = build_order(authority, live_by_id,
                        skip_ids(conn, args.retry_needs_human))
    log(f"{len(order)} notebook(s) pending")

    if args.plan:
        save_json(PLAN_PATH, {
            "created_at": now_iso(), "allowlist_digest": authority.digest,
            "notebook_ids": order,
            "notebooks": [{"notebook_id": i,
                           "title": live_by_id[i].get("title"),
                           "source_count": live_by_id[i].get("source_count")}
                          for i in order],
        })
        log(f"plan written to {PLAN_PATH} ({len(order)} notebooks)")
        return

    cap = cap_for_run(args.limit)
    batch = order[:cap]
    log(f"this run will process {len(batch)} notebook(s) (hard cap {cap})")

    if args.execute:
        if not PLAN_PATH.exists():
            sys.exit("--execute requires a plan: run with --plan first")
        plan = json.loads(PLAN_PATH.read_text())
        if plan.get("allowlist_digest") != authority.digest:
            sys.exit("plan was made against a different allowlist; re-plan")
        planned = set(plan.get("notebook_ids") or [])
        unplanned = set(batch) - planned
        if unplanned:
            sys.exit(f"{len(unplanned)} notebook(s) in this batch are not in the plan")

    device = capture_mod.current_device(data_dir)
    seen_inodes = {}
    deleted = kept = failed = 0

    for index, notebook_id in enumerate(batch):
        meta = dict(live_by_id[notebook_id])
        frozen = authority.frozen(notebook_id)
        meta["origin"] = frozen.origin if frozen else "daily"
        title = (meta.get("title") or "")[:52]

        if args.target_free is not None:
            free = args.account_cap - (len(live_by_id) - deleted)
            if free >= args.target_free:
                log(f"reached --target-free {args.target_free}; stopping")
                break

        if index and index % AUTH_RECHECK_EVERY == 0:
            if not ensure_auth():
                log("auth could not be refreshed; stopping cleanly")
                break

        log(f"[{index + 1}/{len(batch)}] {title!r}")
        with archive_db.write_txn(conn):
            conn.execute(
                "INSERT OR IGNORE INTO notebooks (notebook_id, title, origin, state)"
                " VALUES (?,?,?,'pending')",
                (notebook_id, meta.get("title") or "", meta["origin"]),
            )
            archive_db.set_state(conn, notebook_id, "capturing", run_id=run_id)

        result = capture_mod.capture(data_dir, notebook_id, meta, run_id)
        if not result.ok:
            log(f"    capture failed: {result.error}")
            with archive_db.write_txn(conn):
                archive_db.set_state(conn, notebook_id, "needs_human",
                                     run_id=run_id, capture_error=result.error)
            failed += 1
            continue

        capture_mod.promote(data_dir, notebook_id, run_id)
        capture_mod.persist(conn, notebook_id, result, run_id, meta)
        log(f"    captured: {len(result.artifact_rows)} artifact(s), "
            f"{len(result.source_rows)} source(s), {result.completeness}")

        was_deleted, verdict = capture_mod.delete_if_verified(
            conn, data_dir, notebook_id, authority, meta, result, device, run_id,
            seen_inodes=seen_inodes, dry_run=not args.execute,
            allow_thin_sources=args.allow_thin_sources,
            allow_empty=args.allow_empty,
        )
        if was_deleted:
            deleted += 1
            log("    deleted")
        elif verdict.ok:
            kept += 1
            log("    verified (not deleting: --no-delete)")
        else:
            failed += 1
            for reason in verdict.reasons[:4]:
                log(f"    REFUSED: {reason}")

    with archive_db.write_txn(conn):
        conn.execute(
            "UPDATE runs SET completed_at=?, status='ok' WHERE run_id=?",
            (now_iso(), run_id),
        )
    log(f"done: {deleted} deleted, {kept} verified, {failed} need review")
    if not args.execute:
        log("nothing was deleted (use --execute after reviewing the plan)")


if __name__ == "__main__":
    main()
