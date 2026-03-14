#!/usr/bin/env python3
"""Content Processor — categorizes queued sources and creates companion notes.

Reads the triage plan (if fresh) or groups by category, creates companion
notes for each group, updates source statuses, and writes output for the
notebooklm-orchestrator.

Usage:
    python3 scripts/content_processor.py          # Normal run
    python3 scripts/content_processor.py --dry-run # Preview without saving
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", Path(__file__).parent.parent))
sys.path.insert(0, str(PROJECT_ROOT))
from lib.file_utils import load_json, save_json

DATA_DIR = PROJECT_ROOT / "data"
SOURCES_FILE = DATA_DIR / "sources.json"
CATEGORIES_FILE = DATA_DIR / "categories.json"
TRIAGE_FILE = DATA_DIR / "triage-plan.json"
OUTPUT_DIR = DATA_DIR / "companion-notes"


def categorize_source(src, categories):
    """Categorize a source using URL patterns then keywords."""
    url = (src.get("url") or "").lower()
    title = (src.get("title") or "").lower()

    # Already categorized (not "auto" or "general")
    existing = src.get("category", "")
    if existing and existing not in ("auto", "general", ""):
        return existing

    # URL pattern matching (highest priority)
    for cat in categories:
        for pattern in cat.get("url_patterns", []):
            if pattern.lower() in url:
                return cat["slug"]

    # Keyword matching (secondary)
    best_cat = None
    best_hits = 0
    for cat in categories:
        hits = sum(1 for kw in cat.get("keywords", []) if kw.lower() in title)
        if hits > best_hits:
            best_hits = hits
            best_cat = cat["slug"]

    return best_cat or "general"


def create_companion_note(group_name, sources, date_str):
    """Create a simple companion note for a group of sources."""
    lines = [
        f"# {group_name} Research Digest — {date_str}",
        "",
        f"## Sources in this batch ({len(sources)} items)",
        "",
    ]

    for i, src in enumerate(sources, 1):
        title = src.get("title", "Untitled")
        url = src.get("url", "")
        tags = ", ".join(src.get("tags", []))
        tag_str = f" — {tags}" if tags else ""
        lines.append(f"{i}. **{title}** — {url}{tag_str}")

    lines.append("")
    lines.append("## Themes")
    lines.append("")

    # Group by origin for basic theme detection
    origins = {}
    for src in sources:
        origin = src.get("origin", "unknown")
        origins.setdefault(origin, []).append(src.get("title", ""))

    for origin, titles in origins.items():
        if len(titles) > 1:
            lines.append(f"- From {origin}: {len(titles)} items")

    # Basic keyword clustering
    all_titles = " ".join(s.get("title", "").lower() for s in sources)
    common_terms = {}
    for word in re.findall(r"\b[a-z]{4,}\b", all_titles):
        if word not in ("this", "that", "with", "from", "have", "been", "were",
                        "more", "than", "about", "into", "your", "what"):
            common_terms[word] = common_terms.get(word, 0) + 1

    top_terms = sorted(common_terms.items(), key=lambda x: -x[1])[:5]
    if top_terms:
        lines.append(f"- Key topics: {', '.join(t[0] for t in top_terms if t[1] > 1)}")

    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(
        f"This batch contains {len(sources)} items in the {group_name} category, "
        f"collected from {len(origins)} source{'s' if len(origins) != 1 else ''}. "
    )

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Content Processor")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sources_data = load_json(SOURCES_FILE)
    categories = load_json(CATEGORIES_FILE)["categories"]
    cat_names = {c["slug"]: c["name"] for c in categories}

    # Get queued sources
    queued = [s for s in sources_data["sources"] if s.get("status") == "queued"]
    if not queued:
        print("No queued sources to process.")
        return

    print(f"Processing {len(queued)} queued sources...")

    # Check for fresh triage plan
    triage_plan = None
    if TRIAGE_FILE.exists():
        plan = load_json(TRIAGE_FILE)
        gen_at = plan.get("generated_at", "")
        if gen_at:
            try:
                gen_time = datetime.fromisoformat(gen_at.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) - gen_time < timedelta(hours=2):
                    triage_plan = plan
                    print(f"Using triage plan from {gen_at}")
            except Exception:
                pass

    # Categorize sources
    for src in queued:
        src["category"] = categorize_source(src, categories)

    # Group by category (or triage plan)
    groups = {}
    if triage_plan and "notebook_groups" in triage_plan:
        for group in triage_plan["notebook_groups"]:
            group_name = group.get("notebook_label") or group.get("name", "Unknown")
            source_ids = set(group.get("source_ids", []))
            group_sources = [s for s in queued if s.get("id") in source_ids]
            if group_sources:
                groups[group_name] = group_sources
    else:
        for src in queued:
            cat = src.get("category", "general")
            name = cat_names.get(cat, cat.title())
            groups.setdefault(name, []).append(src)

    if not groups:
        print("No groups to process.")
        return

    # Create companion notes
    date_str = datetime.now().strftime("%Y-%m-%d")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    notes = {}

    for group_name, group_sources in sorted(groups.items()):
        note_text = create_companion_note(group_name, group_sources, date_str)
        slug = re.sub(r"[^a-z0-9]+", "-", group_name.lower()).strip("-")
        note_path = OUTPUT_DIR / f"{date_str}-{slug}.md"

        if not args.dry_run:
            note_path.write_text(note_text)

        notes[group_name] = {
            "path": str(note_path),
            "text": note_text,
            "source_count": len(group_sources),
            "source_ids": [s.get("id") for s in group_sources],
        }

        print(f"  {group_name}: {len(group_sources)} sources, companion note {'created' if not args.dry_run else '(dry run)'}")

    # Update source statuses
    if not args.dry_run:
        id_set = {s.get("id") for group in groups.values() for s in group}
        for src in sources_data["sources"]:
            if src.get("id") in id_set:
                src["status"] = "processing"
        save_json(SOURCES_FILE, sources_data)

        # Rename triage plan if used
        if triage_plan:
            consumed = DATA_DIR / f"triage-plan-consumed-{date_str}.json"
            TRIAGE_FILE.rename(consumed)
            print(f"  Triage plan consumed: {consumed.name}")

    # Write output manifest for orchestrator
    manifest = {
        "date": date_str,
        "groups": {
            name: {
                "companion_note_path": info["path"],
                "source_ids": info["source_ids"],
                "source_count": info["source_count"],
            }
            for name, info in notes.items()
        },
    }

    manifest_path = DATA_DIR / "content-processor-output.json"
    if not args.dry_run:
        save_json(manifest_path, manifest)
        print(f"\nManifest saved: {manifest_path}")

    print(f"\nProcessing complete: {len(groups)} groups, {sum(len(g) for g in groups.values())} sources")


if __name__ == "__main__":
    main()
