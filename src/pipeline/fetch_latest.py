#!/usr/bin/env python3
"""
Fetch the latest notebook matching criteria from notebooks.json.

Usage:
    python3 scripts/fetch_latest.py                        # Latest notebook (any category)
    python3 scripts/fetch_latest.py --category news        # Latest news notebook
    python3 scripts/fetch_latest.py --category research    # Latest non-news/non-daily notebook
    python3 scripts/fetch_latest.py --artifact report      # Only notebooks with a report
    python3 scripts/fetch_latest.py --artifact podcast     # Only notebooks with a podcast

Output: JSON on stdout with notebook_id, alias, category, has_podcast, has_report, source_ids, etc.
Exit code 1 if nothing found, with a helpful error message on stderr.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

# src/pipeline/fetch_latest.py -> project root is three levels up. Two levels
# resolved to src/data, which does not exist, so every registry lookup fell
# through to querying live notebooks.
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
NOTEBOOKS_FILE = DATA_DIR / "notebooks.json"
NLM_BIN = Path.home() / ".local" / "bin" / "nlm"


def load_notebooks_from_file():
    """Load notebooks from notebooks.json."""
    try:
        with open(NOTEBOOKS_FILE) as f:
            data = json.load(f)
        return data.get("notebooks", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def load_notebooks_from_nlm():
    """Fallback: load notebooks from nlm notebook list --json."""
    try:
        result = subprocess.run(
            [str(NLM_BIN), "notebook", "list", "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        # nlm list returns a different format — normalize to our schema
        notebooks = []
        for nb in data if isinstance(data, list) else data.get("notebooks", []):
            notebooks.append({
                "id": nb.get("id", ""),
                "alias": nb.get("alias", ""),
                "category": guess_category_from_name(nb.get("name", "")),
                "category_name": nb.get("name", ""),
                "created_at": nb.get("created_at", nb.get("createdAt", "")),
                "source_count": nb.get("source_count", nb.get("sourceCount", 0)),
                "source_ids": nb.get("source_ids", []),
                "has_podcast": nb.get("has_podcast", False),
                "has_report": nb.get("has_report", False),
                "status": nb.get("status", "complete"),
            })
        return notebooks
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return []


def guess_category_from_name(name):
    """Guess category from notebook name for nlm fallback."""
    name_lower = name.lower()
    if "news" in name_lower:
        return "news"
    if "daily" in name_lower:
        return "daily"
    return "research"


def filter_notebooks(notebooks, category=None, artifact=None):
    """Filter notebooks by category and artifact requirements."""
    filtered = []
    for nb in notebooks:
        # Only complete notebooks
        if nb.get("status") != "complete":
            continue

        # Category filter
        if category == "news":
            if nb.get("category") != "news":
                continue
        elif category == "research":
            if nb.get("category") in ("news", "daily"):
                continue
        # No category flag → accept any

        # Artifact filter
        if artifact == "report":
            if not nb.get("has_report"):
                continue
        elif artifact == "podcast":
            if not nb.get("has_podcast"):
                continue
        elif artifact == "both":
            if not nb.get("has_report") or not nb.get("has_podcast"):
                continue

        filtered.append(nb)

    return filtered


def pick_latest(notebooks):
    """Return the most recently created notebook."""
    if not notebooks:
        return None
    return max(notebooks, key=lambda nb: nb.get("created_at", ""))


def main():
    parser = argparse.ArgumentParser(description="Fetch latest notebook matching criteria")
    parser.add_argument("--category", choices=["news", "research", "daily"],
                        help="Filter by category: news, research (non-news/non-daily), or daily (latest PDF from data/daily-feed/)")
    parser.add_argument("--artifact", choices=["report", "podcast", "both"],
                        help="Only return notebooks with this artifact type")
    args = parser.parse_args()

    # Daily feed: look up latest PDF from filesystem (not notebooks.json)
    if args.category == "daily":
        daily_dir = DATA_DIR / "daily-feed"
        if not daily_dir.exists():
            print("No daily feed directory found. Run /process or wait for the daily cron.",
                  file=sys.stderr)
            sys.exit(1)
        htmls = sorted(daily_dir.glob("*.html"), reverse=True)
        pdfs = sorted(daily_dir.glob("*.pdf"), reverse=True)
        mds = sorted(daily_dir.glob("*.md"), reverse=True)
        if not htmls and not pdfs and not mds:
            print("No daily feed files found. Run /process or wait for the daily cron.",
                  file=sys.stderr)
            sys.exit(1)
        # Prefer HTML, fall back to PDF, then markdown
        latest_file = htmls[0] if htmls else (pdfs[0] if pdfs else mds[0])
        date_str = latest_file.stem  # e.g. "2026-02-16"
        # Check for companion podcast
        podcast_path = daily_dir / f"{date_str}-podcast.mp3"
        output = {
            "category": "daily",
            "date": date_str,
            "report_path": str(latest_file),
            "has_report": True,
            "has_podcast": podcast_path.exists(),
            "podcast_path": str(podcast_path) if podcast_path.exists() else None,
        }
        print(json.dumps(output, indent=2))
        return

    # Load notebooks — try file first, fall back to nlm CLI
    notebooks = load_notebooks_from_file()
    source = "notebooks.json"
    if not notebooks:
        notebooks = load_notebooks_from_nlm()
        source = "nlm notebook list"

    if not notebooks:
        print("No notebooks found. Run the weekly pipeline first to create notebooks, "
              "or use /process to trigger one now.", file=sys.stderr)
        sys.exit(1)

    # Filter
    filtered = filter_notebooks(notebooks, category=args.category, artifact=args.artifact)

    if not filtered:
        parts = []
        if args.category:
            parts.append(f"category={args.category}")
        if args.artifact:
            parts.append(f"artifact={args.artifact}")
        criteria = " with " + ", ".join(parts) if parts else ""
        print(f"No completed notebooks found{criteria}. "
              f"Checked {len(notebooks)} notebooks from {source}.", file=sys.stderr)
        sys.exit(1)

    # Pick latest
    latest = pick_latest(filtered)

    # Output
    output = {
        "notebook_id": latest.get("id", ""),
        "alias": latest.get("alias", ""),
        "category": latest.get("category", ""),
        "category_name": latest.get("category_name", ""),
        "created_at": latest.get("created_at", ""),
        "source_count": latest.get("source_count", 0),
        "source_ids": latest.get("source_ids", []),
        "has_podcast": latest.get("has_podcast", False),
        "has_report": latest.get("has_report", False),
        "status": latest.get("status", ""),
        "source": source,
        "total_checked": len(notebooks),
        "total_matched": len(filtered),
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
