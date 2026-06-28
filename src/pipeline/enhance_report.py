#!/usr/bin/env python3
"""
Deterministic post-processor for NotebookLM research reports.

Three operations:
1. Remove per-section TL;DRs — strips any > **TL;DR** blockquotes after the first one
2. Deterministic source links — ALWAYS strips the model-generated ## Source Links
   (URLs there are unreliable: hallucinated or omitted) and rebuilds it from ground
   truth. With --manifest (the URLs actually added to the notebook, written by the
   pipeline at source-add time) the list is exact; otherwise it falls back to
   data/sources.json filtered by --status.
3. Validate headings — warns about h4+ or orphan h3s (h3 without a preceding h2)

Usage:
    python3 scripts/enhance_report.py --input report.md --manifest data/daily-feed/2026-06-24-sources.txt
    python3 scripts/enhance_report.py --input report.md --sources-file data/sources.json
    python3 scripts/enhance_report.py --input report.md --dry-run
"""

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SOURCES = PROJECT_ROOT / "data" / "sources.json"

# Optional deep URL normalization for robust manifest↔sources.json title matching.
sys.path.insert(0, str(PROJECT_ROOT / "src"))
try:
    from lib.url_utils import normalize_url_deep
except Exception:  # pragma: no cover - fallback if lib unavailable
    def normalize_url_deep(url: str) -> str:
        return (url or "").strip()


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


def _load_source_meta(sources_file: Path) -> dict:
    """Build a {normalized_url: source_dict} lookup from sources.json."""
    if not sources_file.exists():
        return {}
    try:
        data = json.loads(sources_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    lookup = {}
    for src in data.get("sources", []):
        url = src.get("url")
        if url:
            lookup[normalize_url_deep(url)] = src
    return lookup


def build_source_links_from_manifest(
    manifest_path: Path, sources_file: Path,
) -> str | None:
    """Build a ## Source Links section from a manifest of URLs actually added.

    The manifest is the authoritative, deterministic list of source URLs that
    went into the notebook (one URL per line, written by the pipeline at
    source-add time). Titles/category/origin are looked up from sources.json by
    normalized-URL match; URLs with no match still appear (link text = URL).

    Returns:
        Markdown string for the source links section, or None if manifest empty.
    """
    if not manifest_path.exists():
        return None

    urls = [ln.strip() for ln in manifest_path.read_text().splitlines() if ln.strip()]
    if not urls:
        return None

    meta = _load_source_meta(sources_file)

    lines = ["---", "", "## Source Links", ""]
    seen = set()
    for url in urls:
        norm = normalize_url_deep(url)
        if norm in seen:
            continue
        seen.add(norm)
        src = meta.get(norm, {})
        title = src.get("title") or url
        category = src.get("category", "")
        origin = src.get("origin", "")
        meta_parts = [p for p in (category, origin) if p]
        suffix = f" *({', '.join(meta_parts)})*" if meta_parts else ""
        lines.append(f"- [{title}]({url}){suffix}")

    lines.append("")
    return "\n".join(lines)


def strip_existing_source_links(text: str) -> str:
    """Remove a trailing ## Source Links section (and its leading --- rule).

    Source Links is always the final section, so we drop from its heading to EOF,
    then trim any horizontal rule and trailing whitespace that preceded it.
    """
    m = re.search(r"^##\s+Source Links\b", text, re.MULTILINE)
    if not m:
        return text
    head = text[: m.start()]
    # Trim a preceding '---' separator and surrounding blank lines.
    head = re.sub(r"\s*(?:^---\s*$)?\s*\Z", "", head, flags=re.MULTILINE)
    return head.rstrip() + "\n"


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
    manifest_path: Path | None = None,
    dry_run: bool = False,
) -> None:
    """Run all enhancement operations on a report markdown file."""
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    text = input_path.read_text()
    original = text
    sf = sources_file or DEFAULT_SOURCES

    # 1. Remove extra TL;DRs
    text, removed_count = remove_extra_tldrs(text)
    if removed_count:
        print(f"  Removed {removed_count} extra TL;DR blockquote(s)")
    else:
        print("  No extra TL;DRs to remove")

    # 2. Source links — make them DETERMINISTIC. The model's own ## Source Links
    #    (if any) is unreliable (hallucinated/omitted URLs), so always strip it
    #    and rebuild from ground truth: the manifest of URLs actually added to the
    #    notebook, falling back to sources.json when no manifest is supplied.
    links_section = None
    if manifest_path is not None:
        links_section = build_source_links_from_manifest(manifest_path, sf)
        if links_section is None:
            print(f"  Manifest empty/missing ({manifest_path}) — falling back to sources.json")
    if links_section is None:
        links_section = build_source_links(sf, status_filter)

    if links_section:
        stripped = strip_existing_source_links(text)
        if stripped != text:
            print("  Replaced model-generated Source Links with deterministic list")
        text = stripped.rstrip() + "\n\n" + links_section
        count = links_section.count("\n- [")
        print(f"  Source Links section: {count} deterministic links")
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
        help="Fallback filter when no --manifest: include sources with this status (default: queued)",
    )
    parser.add_argument(
        "--manifest",
        help="Path to a newline-delimited list of source URLs actually added to "
             "the notebook. When given, Source Links is built deterministically from it.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    args = parser.parse_args()

    input_path = Path(args.input)
    sources_file = Path(args.sources_file) if args.sources_file else None
    manifest_path = Path(args.manifest) if args.manifest else None

    print(f"Enhancing: {input_path}")
    enhance_report(input_path, sources_file, args.status, manifest_path, args.dry_run)
    print("Done.")


if __name__ == "__main__":
    main()
