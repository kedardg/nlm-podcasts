#!/usr/bin/env python3
"""Update source statuses after successful pipeline completion.

Usage:
    python update_status.py                      # Mark queued -> completed
    python update_status.py --status processing  # Mark queued -> processing
    python update_status.py --dry-run            # Preview without saving
"""
import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from lib.file_utils import load_json, save_json

DATA_DIR = PROJECT_ROOT / "data"
SOURCES_FILE = DATA_DIR / "sources.json"


def main():
    parser = argparse.ArgumentParser(description="Update source statuses")
    parser.add_argument("--status", default="completed", help="Target status (default: completed)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    args = parser.parse_args()

    data = load_json(SOURCES_FILE)
    count = 0
    for s in data.get("sources", []):
        if s["status"] == "queued":
            if not args.dry_run:
                s["status"] = args.status
            count += 1

    if args.dry_run:
        print(f"Would update {count} source(s) to {args.status}")
    else:
        save_json(SOURCES_FILE, data)
        print(f"Updated {count} source(s) to {args.status}")


if __name__ == "__main__":
    main()
