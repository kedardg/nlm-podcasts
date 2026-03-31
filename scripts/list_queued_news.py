#!/usr/bin/env python3
"""List queued news articles from news-queue.json."""
import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

def main():
    with open(f"{DATA_DIR}/news-queue.json") as f:
        news = json.load(f)
    queued = [a for a in news["articles"] if a["status"] == "queued"]
    print(f"Queued news articles: {len(queued)}")
    for a in queued:
        print(f'  {a["id"]}: {a["title"]} - {a["url"]}')
    if not queued:
        sys.exit(1)

if __name__ == "__main__":
    main()
