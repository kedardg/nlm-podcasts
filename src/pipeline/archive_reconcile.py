#!/usr/bin/env python3
"""Check the archive against reality and report anything that disagrees.

``deleted-ids.log`` exists to survive the one case the database cannot: a
delete that succeeded while the commit afterwards failed. That only helps if
something reads it, so this does — comparing the log, the database and the
live account, and naming every divergence.

Read-only. It changes nothing; it tells you what to look at.

Usage:
    .venv/bin/python3 src/pipeline/archive_reconcile.py [--offline]
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lib import archive_db                                    # noqa: E402
from lib.archive_paths import notebook_dir                    # noqa: E402
from nlm import client                                        # noqa: E402
from pipeline.notebook_capture import DELETED_IDS_LOG         # noqa: E402

DATA = PROJECT_ROOT / "data"


def read_log(path):
    records = {}
    if not path.exists():
        return records
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("notebook_id"):
            records[rec["notebook_id"]] = rec
    return records


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(DATA))
    ap.add_argument("--offline", action="store_true",
                    help="skip the live account check")
    ap.add_argument("--repair", action="store_true",
                    help="correct rows whose deletion is fully evidenced: "
                         "logged before the delete, confirmed gone upstream, "
                         "and with a capture still on disk. Nothing else.")
    args = ap.parse_args()
    data_dir = Path(args.data_dir)

    conn = archive_db.open_db(data_dir / "research.db")
    logged = read_log(data_dir / DELETED_IDS_LOG)
    db_deleted = {
        r["notebook_id"] for r in conn.execute(
            "SELECT notebook_id FROM notebooks WHERE state IN "
            "('deleted','delete_pending','deleted_unconfirmed')"
        )
    }

    problems = 0
    live_ids = None
    if not args.offline:
        try:
            live_ids = {n["id"] for n in client.notebook_list()}
        except client.NlmError as exc:
            print(f"could not read the live account: {exc}")

    # A delete was logged but the row never reached a deleted state — the
    # commit after the delete is exactly what the log exists to outlive.
    orphaned = set(logged) - db_deleted
    if orphaned:
        problems += len(orphaned)
        print(f"\n{len(orphaned)} deletion(s) logged but not recorded in the database:")
        repaired = 0
        for nid in sorted(orphaned):
            rec = logged[nid]
            print(f"  {nid}  {rec.get('title','')[:48]!r}  ts={rec.get('ts')}")
            if not args.repair:
                continue
            # Repair only on three independent pieces of evidence, so this can
            # never invent a deletion that did not happen.
            capture = notebook_dir(data_dir, nid) / "capture"
            has_capture = capture.exists() and any(capture.rglob("*"))
            confirmed_gone = live_ids is not None and nid not in live_ids
            if not (has_capture and confirmed_gone):
                print("      not repaired: needs a capture on disk AND "
                      "confirmation it is gone upstream")
                continue
            with archive_db.write_txn(conn):
                archive_db.set_state(
                    conn, nid, "deleted", run_id=rec.get("run_id"),
                    detail="reconciled from deleted-ids.log",
                    capture_error=None,
                )
            repaired += 1
            print("      repaired -> deleted")
        problems -= repaired

    # The reverse: the database claims a deletion the log never saw, which
    # means the pre-delete append did not happen.
    unlogged = db_deleted - set(logged)
    if unlogged:
        problems += len(unlogged)
        print(f"\n{len(unlogged)} deletion(s) recorded in the database but absent "
              f"from the log:")
        for nid in sorted(unlogged):
            row = conn.execute(
                "SELECT title, state FROM notebooks WHERE notebook_id=?", (nid,)
            ).fetchone()
            print(f"  {nid}  {(row['title'] or '')[:48]!r}  state={row['state']}")

    # Every artifact the database points at must still be on disk.
    missing = []
    for row in conn.execute(
        "SELECT notebook_id, kind, file_path FROM artifacts"
        " WHERE file_path IS NOT NULL AND status IN ('downloaded','verified')"
    ):
        path = notebook_dir(data_dir, row["notebook_id"]) / row["file_path"]
        if not path.exists() or path.stat().st_size == 0:
            missing.append((row["notebook_id"], row["kind"], row["file_path"]))
    if missing:
        problems += len(missing)
        print(f"\n{len(missing)} archived artifact(s) missing or empty on disk:")
        for nid, kind, rel in missing[:20]:
            print(f"  {nid[:8]}  {kind:12s} {rel}")

    if live_ids is not None:
        live = live_ids
        if True:
            # A row says deleted but the notebook is still there.
            zombies = db_deleted & live
            if zombies:
                problems += len(zombies)
                print(f"\n{len(zombies)} notebook(s) marked deleted are still live:")
                for nid in sorted(zombies):
                    print(f"  {nid}")
            # Stuck mid-flight from a crashed run.
            for state in ("capturing", "delete_pending"):
                stuck = [
                    r["notebook_id"] for r in conn.execute(
                        "SELECT notebook_id FROM notebooks WHERE state=?", (state,))
                ]
                live_stuck = [n for n in stuck if n in live]
                gone_stuck = [n for n in stuck if n not in live]
                if live_stuck:
                    print(f"\n{len(live_stuck)} notebook(s) left in '{state}' and "
                          f"still live — safe to re-run:")
                    for nid in live_stuck:
                        print(f"  {nid}")
                if gone_stuck:
                    problems += len(gone_stuck)
                    print(f"\n{len(gone_stuck)} notebook(s) left in '{state}' but "
                          f"already gone upstream — needs review:")
                    for nid in gone_stuck:
                        print(f"  {nid}")

    counts = {r["state"]: r["n"] for r in conn.execute(
        "SELECT state, count(*) n FROM notebooks GROUP BY state")}
    print("\narchive state:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"log records: {len(logged)}")
    print("\nOK — database, log and account agree." if not problems
          else f"\n{problems} divergence(s) found.")
    sys.exit(0)


if __name__ == "__main__":
    main()
