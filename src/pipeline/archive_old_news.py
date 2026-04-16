#!/usr/bin/env python3
"""Archive news items older than N days from news-queue.json.

Usage:
    .venv/bin/python3 scripts/archive_old_news.py [--days 30] [--dry-run]

Moves items with status 'queued' older than threshold to data/archive/news-YYYY-MM.json.
"""

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.file_utils import load_json, save_json


def main():
    parser = argparse.ArgumentParser(description="Archive old news items")
    parser.add_argument("--days", type=int, default=30, help="Archive items older than N days (default: 30)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent
    news_path = project_root / "data" / "news-queue.json"
    archive_dir = project_root / "data" / "archive"

    news_data = load_json(news_path)
    articles = news_data.get("articles", [])
    threshold = datetime.now(timezone.utc) - timedelta(days=args.days)
    threshold_iso = threshold.isoformat()

    # Partition: old queued items vs everything else
    # News articles use "extracted_at" (from newsletter_parser), sources use "added_at"
    to_archive = []
    to_keep = []
    for article in articles:
        date_str = article.get("added_at") or article.get("extracted_at") or ""
        if article.get("status") == "queued" and date_str and date_str < threshold_iso:
            to_archive.append(article)
        else:
            to_keep.append(article)

    if not to_archive:
        print(f"No queued articles older than {args.days} days to archive.")
        return

    # Group archived items by month
    by_month = defaultdict(list)
    for article in to_archive:
        date_str = article.get("added_at") or article.get("extracted_at") or ""
        month = date_str[:7]  # YYYY-MM
        if month:
            by_month[month].append(article)
        else:
            by_month["unknown"].append(article)

    print(f"Archiving {len(to_archive)} items older than {args.days} days:")
    for month, items in sorted(by_month.items()):
        print(f"  {month}: {len(items)} items")

    if args.dry_run:
        print("\n(Dry run -- no changes saved)")
        return

    # Create archive directory
    archive_dir.mkdir(parents=True, exist_ok=True)

    # Write archive files (merge with existing)
    for month, items in by_month.items():
        archive_path = archive_dir / f"news-{month}.json"
        existing = load_json(archive_path) if archive_path.exists() else {"articles": []}
        existing.setdefault("articles", []).extend(items)
        save_json(archive_path, existing)

    # Update news-queue.json
    news_data["articles"] = to_keep
    save_json(news_path, news_data)
    print(f"\nArchived {len(to_archive)} items. {len(to_keep)} items remain in news-queue.json.")


if __name__ == "__main__":
    main()
