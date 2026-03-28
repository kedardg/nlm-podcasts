#!/usr/bin/env python3
"""List queued sources from sources.json."""
import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

def main():
    with open(f"{DATA_DIR}/sources.json") as f:
        sources = json.load(f)
    queued = [s for s in sources["sources"] if s["status"] == "queued"]
    print(f"Queued sources: {len(queued)}")
    for s in queued:
        url = s.get("url") or "NO_URL"
        src_type = s.get("type", "unknown")
        print(f'  {s["id"]}: {s["title"]} ({s["category"]}) [{src_type}] - {url}')
    if not queued:
        sys.exit(1)

if __name__ == "__main__":
    main()
