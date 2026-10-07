#!/usr/bin/env python3
"""Age out sources the daily pipeline will never pick up.

The daily feed collects from a two-day window. Weekly used to drain everything
older into its own notebooks, but weekly notebook creation is retired, so
anything that falls out of that window now sits in `sources.json` forever —
currently several hundred rows in non-terminal states, growing every day.

Expired rows are **moved**, never mutated in place: they are appended to
``data/archive/sources-expired-YYYY-MM.json`` and dropped from the live file.
`data/` is gitignored and unbacked, so an in-place status flip would be the
only record, and a bad run would be unrecoverable.

Usage:
    .venv/bin/python3 src/pipeline/expire_sources.py --dry-run
    .venv/bin/python3 src/pipeline/expire_sources.py [--days 2]
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lib.file_utils import load_json, now_iso, save_json      # noqa: E402

DATA = PROJECT_ROOT / "data"

# Only these can expire. Anything already tied to a notebook, or explicitly
# finished, is left alone.
EXPIRABLE_STATUSES = {"queued", "processing", "daily-digested"}

DATE_FIELDS = ("added_at", "extracted_at", "created_at")


def source_date(row):
    for field in DATE_FIELDS:
        value = row.get(field)
        if value:
            return str(value)[:10]
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=2,
                    help="keep sources added within this many days (default 2, "
                         "matching the daily feed's collection window)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--data-dir", default=str(DATA))
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    sources_file = data_dir / "sources.json"
    doc = load_json(sources_file, default={"sources": []})
    rows = doc.get("sources", [])

    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%d")

    keep, expired = [], []
    for row in rows:
        status = row.get("status")
        added = source_date(row)
        if status in EXPIRABLE_STATUSES and added and added < cutoff:
            if row.get("notebook_id"):
                keep.append(row)        # already tied to a notebook; leave it
                continue
            expired.append({**row, "status": "expired",
                            "expired_at": now_iso(),
                            "expired_reason": f"older than {args.days}d window"})
        else:
            keep.append(row)

    print(f"sources: {len(rows)} total, {len(expired)} older than {cutoff}, "
          f"{len(keep)} kept")
    if not expired:
        return
    by_status = {}
    for row in expired:
        key = row.get("status_before") or "expirable"
        by_status[key] = by_status.get(key, 0) + 1

    if args.dry_run:
        print("dry run — nothing written")
        for row in expired[:5]:
            print(f"    {source_date(row)}  {(row.get('title') or row.get('url') or '')[:64]}")
        return

    month = now_iso()[:7]
    archive_file = data_dir / "archive" / f"sources-expired-{month}.json"
    archive_file.parent.mkdir(parents=True, exist_ok=True)
    archived = load_json(archive_file, default={"sources": []})
    archived.setdefault("sources", []).extend(expired)
    save_json(archive_file, archived)

    doc["sources"] = keep
    save_json(sources_file, doc)
    print(f"moved {len(expired)} source(s) to {archive_file.name}")


if __name__ == "__main__":
    main()
