#!/usr/bin/env python3
"""
Deterministic post-processor for NotebookLM research reports.

Three operations:
1. Remove per-section TL;DRs — strips any > **TL;DR** blockquotes after the first one
2. Append source links — reads data/sources.json (filtered by --status), appends ## Source Links
3. Validate headings — warns about h4+ or orphan h3s (h3 without a preceding h2)

Usage:
    python3 scripts/enhance_report.py --input report.md
    python3 scripts/enhance_report.py --input report.md --sources-file data/sources.json
    python3 scripts/enhance_report.py --input report.md --dry-run
"""

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCES = PROJECT_ROOT / "data" / "sources.json"


def remove_extra_tldrs(text: str) -> tuple[str, int]:
    """Remove all > **TL;DR** blockquotes after the first one.

    Returns:
        Tuple of (modified text, number of TL;DRs removed).
    """
    # Match blockquote lines containing TL;DR (possibly spanning multiple > lines)
    # Pattern: one or more lines starting with > that contain **TL;DR** in the first line
    pattern = re.compile(
        r"^(?:> *\*\*TL;DR\*\*.*\n(?:>.*\n)*)",
        re.MULTILINE | re.IGNORECASE,
    )

    matches = list(pattern.finditer(text))
    if len(matches) <= 1:
        return text, 0

    # Remove all matches after the first one (work backwards to preserve positions)
    removed = 0
    for match in reversed(matches[1:]):
        # Also remove a preceding blank line if present
        start = match.start()
        end = match.end()
        if start > 0 and text[start - 1] == "\n":
            start -= 1
        text = text[:start] + text[end:]
        removed += 1

    return text, removed


def build_source_links(sources_file: Path, status_filter: str = "queued") -> str | None:
    """Build a ## Source Links markdown section from sources.json.

    Args:
        sources_file: Path to sources.json.
        status_filter: Only include sources with this status.

    Returns:
        Markdown string for the source links section, or None if no sources.
    """
    if not sources_file.exists():
        print(f"  WARNING: Sources file not found: {sources_file}", file=sys.stderr)
        return None

    data = json.loads(sources_file.read_text())
    sources = data.get("sources", [])

    filtered = [s for s in sources if s.get("status") == status_filter]
    if not filtered:
        # Fall back to all sources if none match the filter
        filtered = sources

    if not filtered:
        return None

    lines = ["---", "", "## Source Links", ""]
    for src in filtered:
        title = src.get("title", src.get("url", "Untitled"))
        url = src.get("url", "")
        category = src.get("category", "")
        origin = src.get("origin", "")

        if url:
            meta_parts = []
            if category:
                meta_parts.append(category)
            if origin:
                meta_parts.append(origin)
            meta = f" *({', '.join(meta_parts)})*" if meta_parts else ""
            lines.append(f"- [{title}]({url}){meta}")

    lines.append("")
    return "\n".join(lines)


def validate_headings(text: str) -> list[str]:
    """Check for h4+ headings and orphan h3s (h3 without preceding h2).

    Returns:
        List of warning messages.
    """
    warnings = []
    seen_h2 = False

    for i, line in enumerate(text.splitlines(), 1):
        # Check for h4 or deeper
        h4_match = re.match(r"^(#{4,})\s", line)
        if h4_match:
            level = len(h4_match.group(1))
            warnings.append(f"  Line {i}: h{level} heading found (prefer h2/h3 only): {line.strip()}")

        # Track h2/h3 for orphan detection
        if re.match(r"^##\s", line) and not re.match(r"^###", line):
            seen_h2 = True
        elif re.match(r"^###\s", line) and not seen_h2:
            warnings.append(f"  Line {i}: orphan h3 (no preceding h2): {line.strip()}")

    return warnings


def enhance_report(
    input_path: Path,
    sources_file: Path | None = None,
    status_filter: str = "queued",
    dry_run: bool = False,
) -> None:
    """Run all enhancement operations on a report markdown file."""
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    text = input_path.read_text()
    original = text

    # 1. Remove extra TL;DRs
    text, removed_count = remove_extra_tldrs(text)
    if removed_count:
        print(f"  Removed {removed_count} extra TL;DR blockquote(s)")
    else:
        print("  No extra TL;DRs to remove")

    # 2. Append source links (only if not already present)
    if re.search(r"^## Source Links", text, re.MULTILINE):
        print("  Source Links section already exists — skipping")
    else:
        sf = sources_file or DEFAULT_SOURCES
        links_section = build_source_links(sf, status_filter)
        if links_section:
            text = text.rstrip() + "\n\n" + links_section
            count = links_section.count("\n- [")
            print(f"  Appended Source Links section ({count} links)")
        else:
            print("  No sources found to append")

    # 3. Validate headings
    warnings = validate_headings(text)
    if warnings:
        print("  Heading warnings:")
        for w in warnings:
            print(w)
    else:
        print("  Headings OK")

    # Write or preview
    if dry_run:
        print(f"\n  [DRY RUN] Would write {len(text)} chars to {input_path}")
        if text != original:
            print("  Changes detected — rerun without --dry-run to apply")
    else:
        input_path.write_text(text)
        print(f"  Written: {input_path}")


def main():
    parser = argparse.ArgumentParser(description="Enhance research report markdown")
    parser.add_argument("--input", required=True, help="Input markdown file")
    parser.add_argument("--sources-file", help="Path to sources.json (default: data/sources.json)")
    parser.add_argument(
        "--status",
        default="queued",
        help="Only include sources with this status (default: queued)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    args = parser.parse_args()

    input_path = Path(args.input)
    sources_file = Path(args.sources_file) if args.sources_file else None

    print(f"Enhancing: {input_path}")
    enhance_report(input_path, sources_file, args.status, args.dry_run)
    print("Done.")


if __name__ == "__main__":
    main()
