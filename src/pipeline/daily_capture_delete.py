#!/usr/bin/env python3
"""Capture today's notebook into the archive, then delete it upstream.

Runs as the last NLM step of the daily pipeline, after delivery. By this point
the report, podcast and deep dives have already been generated, downloaded and
sent — the notebook has no remaining job, and leaving it alive is what filled
the account to its 500-notebook cap in the first place.

Authority is scoped to this run: only a notebook this invocation was told it
created can be deleted here. The frozen reclaim allowlist is deliberately not
consulted — it cannot contain a notebook that did not exist when it was frozen,
and a delete path that falls back to title matching is how personal notebooks
get destroyed.

Exit codes: 0 captured and deleted, 0 captured but kept (with a reason logged),
1 capture failed. A non-fatal outcome always leaves the notebook alive.
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lib import archive_db                                   # noqa: E402
from lib.archive_paths import assert_notebook_id             # noqa: E402
from lib.file_utils import now_iso                           # noqa: E402
from lib.notebook_allowlist import DailyAuthority            # noqa: E402
from nlm import client                                       # noqa: E402
from pipeline import notebook_capture as capture_mod         # noqa: E402

DATA = PROJECT_ROOT / "data"

# Refuse to create past this, and warn well before. Without a guard, any run
# where capture-then-delete silently degrades to capture-only walks the account
# back to the cap over a few months.
CAPACITY_WARN = 400
CAPACITY_HARD = 490


def log(msg):
    print(f"  [capture] {msg}", flush=True)


def check_capacity():
    """Report (count, ok_to_create). Used by the pipeline before creating."""
    try:
        notebooks = client.notebook_list()
    except client.NlmError as exc:
        log(f"cannot read account size: {exc}")
        return None, True
    count = len(notebooks)
    if count >= CAPACITY_HARD:
        log(f"REFUSING: account holds {count} notebooks (hard limit {CAPACITY_HARD})")
        return count, False
    if count >= CAPACITY_WARN:
        log(f"WARNING: account holds {count} notebooks (warn at {CAPACITY_WARN})")
    return count, True


def wait_for_artifacts(notebook_id, kinds, timeout, interval):
    """Wait until every requested artifact kind is terminal.

    The previous shell poll asked "is anything in_progress?" and treated "no"
    as done. Seconds after `report create` the artifact is not registered yet,
    so the status mentions nothing at all — and the poll exited immediately,
    reporting completion in 0s while the report did not exist. Requiring
    positive confirmation per kind is the fix; absence of evidence was never
    evidence of completion.
    """
    import time

    deadline = time.time() + timeout
    wanted = set(kinds)
    while time.time() < deadline:
        ok, artifacts = client.studio_status(notebook_id)
        if not ok:
            log("studio status unavailable; retrying")
            time.sleep(interval)
            continue
        by_kind = {}
        for art in artifacts or []:
            kind = art.get("type") or art.get("artifact_type")
            status = (art.get("status") or "").lower()
            # 'completed' beats anything else we may have seen for this kind.
            if by_kind.get(kind) != "completed":
                by_kind[kind] = status
        done = {k for k in wanted if by_kind.get(k) == "completed"}
        failed = {k for k in wanted if by_kind.get(k) == "failed"}
        if failed:
            log(f"artifact(s) failed to generate: {', '.join(sorted(failed))}")
        if done | failed >= wanted:
            log(f"artifacts ready: {', '.join(sorted(done))}"
                + (f" (failed: {', '.join(sorted(failed))})" if failed else ""))
            return not failed
        pending = sorted(wanted - done - failed)
        log(f"waiting on {', '.join(pending)} "
            f"({int(deadline - time.time())}s left)")
        time.sleep(interval)
    log(f"timed out waiting for {', '.join(sorted(wanted))}")
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--notebook-id", help="the notebook this run created")
    ap.add_argument("--run-id", default=f"daily_{now_iso()[:10]}")
    ap.add_argument("--alias")
    ap.add_argument("--title", default="")
    ap.add_argument("--data-dir", default=str(DATA))
    ap.add_argument("--no-delete", action="store_true",
                    help="capture and verify only")
    ap.add_argument("--allow-thin-sources", action="store_true")
    ap.add_argument("--capacity-check", action="store_true",
                    help="report account size and exit; 1 means do not create")
    ap.add_argument("--wait-for", metavar="KINDS",
                    help="poll until these artifact kinds are completed "
                         "(comma-separated, e.g. report,audio), then exit 0")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--poll-interval", type=int, default=30)
    ap.add_argument("--already-done", action="store_true",
                    help="exit 0 if --run-id was already captured or deleted. "
                         "Idempotency must not key on the NLM alias, because "
                         "this step deletes it.")
    args = ap.parse_args()

    if args.capacity_check:
        count, ok = check_capacity()
        if count is not None:
            print(count)
        sys.exit(0 if ok else 1)

    if args.wait_for:
        if not args.notebook_id:
            ap.error("--wait-for requires --notebook-id")
        sys.exit(0 if wait_for_artifacts(
            args.notebook_id, [k.strip() for k in args.wait_for.split(",") if k.strip()],
            args.timeout, args.poll_interval) else 1)

    if args.already_done:
        conn = archive_db.open_db(Path(args.data_dir) / "research.db")
        row = conn.execute(
            "SELECT notebook_id, state FROM notebooks"
            " WHERE run_id=? AND state IN ('captured','delete_pending','deleted')"
            " LIMIT 1", (args.run_id,),
        ).fetchone()
        if row:
            log(f"{args.run_id} already {row['state']} ({row['notebook_id']})")
            sys.exit(0)
        sys.exit(1)

    if not args.notebook_id:
        ap.error("--notebook-id is required unless --capacity-check")
    try:
        assert_notebook_id(args.notebook_id)
    except Exception as exc:
        log(f"refusing: {exc}")
        sys.exit(1)

    data_dir = Path(args.data_dir)
    conn = archive_db.open_db(data_dir / "research.db")

    authority = DailyAuthority(args.run_id)
    authority.claim(args.notebook_id)

    meta = {"title": args.title, "alias": args.alias, "origin": "daily"}
    try:
        live = {n["id"]: n for n in client.notebook_list()}
        if args.notebook_id in live:
            meta.update(live[args.notebook_id])
            meta["origin"] = "daily"
    except client.NlmError:
        pass

    with archive_db.write_txn(conn):
        conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, kind, run_date, started_at, status)"
            " VALUES (?, 'daily', ?, ?, 'running')",
            (args.run_id, now_iso()[:10], now_iso()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO notebooks (notebook_id, run_id, title, origin, state)"
            " VALUES (?,?,?,'daily','pending')",
            (args.notebook_id, args.run_id, meta.get("title") or ""),
        )
        archive_db.set_state(conn, args.notebook_id, "capturing", run_id=args.run_id)

    result = capture_mod.capture(data_dir, args.notebook_id, meta, args.run_id)
    if not result.ok:
        log(f"capture failed: {result.error}")
        with archive_db.write_txn(conn):
            archive_db.set_state(conn, args.notebook_id, "needs_human",
                                 run_id=args.run_id, capture_error=result.error)
        sys.exit(1)

    capture_mod.promote(data_dir, args.notebook_id, args.run_id)
    capture_mod.persist(conn, args.notebook_id, result, args.run_id, meta)
    log(f"captured {len(result.artifact_rows)} artifact(s), "
        f"{len(result.source_rows)} source(s) — {result.completeness}")

    device = capture_mod.current_device(data_dir)
    deleted, verdict = capture_mod.delete_if_verified(
        conn, data_dir, args.notebook_id, authority, meta, result, device,
        args.run_id, dry_run=args.no_delete,
        allow_thin_sources=args.allow_thin_sources,
    )
    with archive_db.write_txn(conn):
        conn.execute("UPDATE runs SET completed_at=?, status='ok' WHERE run_id=?",
                     (now_iso(), args.run_id))

    if deleted:
        log("notebook deleted upstream; artifacts live in the archive")
    elif verdict.ok:
        log("verified but kept (--no-delete)")
    else:
        # Never fatal: an unverified capture leaves the notebook alive so the
        # next run can retry it.
        log("kept — verification refused:")
        for reason in verdict.reasons[:5]:
            log(f"    {reason}")
    sys.exit(0)


if __name__ == "__main__":
    main()
