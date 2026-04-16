#!/usr/bin/env python3
"""
Index notebook source metadata into ChromaDB for notebook-routed RAG.

Called from the pipeline after notebook creation (notebooklm-orchestrator Step 8.5).
Reads notebooks.json to get source_ids and metadata, then delegates to
rag/embeddings.py index_notebook_sources().

Usage:
    python scripts/index_notebook.py                           # Index most recent notebook
    python scripts/index_notebook.py --notebook-id <id>        # Index specific notebook
    python scripts/index_notebook.py --all                     # Re-index all notebooks
    python scripts/index_notebook.py --companion-note <path>   # Include companion note
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Add project root to path so we can import rag modules
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rag.embeddings import index_notebook_sources, index_notebook_summary  # noqa: E402

NOTEBOOKS_FILE = PROJECT_ROOT / "data" / "notebooks.json"


def load_notebooks():
    """Load all notebooks from the registry."""
    if not NOTEBOOKS_FILE.exists():
        return []
    with open(NOTEBOOKS_FILE) as f:
        data = json.load(f)
    return data.get("notebooks", [])


def index_single_notebook(notebook, companion_note_path=None):
    """Index a single notebook's sources into ChromaDB."""
    notebook_id = notebook.get("id", "")
    notebook_alias = notebook.get("alias", "")
    category = notebook.get("category", "")
    source_ids = notebook.get("source_ids", [])
    created_at = notebook.get("created_at", "")

    if not notebook_id:
        print("Error: notebook has no ID", file=sys.stderr)
        return 0

    if not source_ids:
        print(f"Warning: notebook {notebook_id} has no source_ids, skipping", file=sys.stderr)
        return 0

    companion_text = None
    if companion_note_path:
        note_path = Path(companion_note_path)
        if note_path.exists():
            companion_text = note_path.read_text()
        else:
            print(f"Warning: companion note not found: {companion_note_path}", file=sys.stderr)

    return index_notebook_sources(
        notebook_id=notebook_id,
        notebook_alias=notebook_alias,
        category_slug=category,
        source_ids=source_ids,
        companion_note_text=companion_text,
        created_at=created_at,
    )


def extract_tldr(report_text):
    """Extract the first TL;DR blockquote from a report markdown.

    Returns clean text with '> ' prefix and '**TL;DR**' marker stripped.
    Falls back to first 500 words if no TL;DR found.
    """
    pattern = re.compile(
        r"^(?:> *\*\*TL;DR\*\*.*\n(?:>.*\n)*)",
        re.MULTILINE | re.IGNORECASE,
    )
    match = pattern.search(report_text)
    if match:
        raw = match.group(0)
        # Strip > prefix and **TL;DR** marker
        lines = raw.strip().split("\n")
        cleaned = []
        for line in lines:
            line = re.sub(r"^>\s?", "", line)  # strip blockquote prefix
            line = re.sub(r"\*\*TL;DR\*\*:?\s*", "", line, flags=re.IGNORECASE)
            cleaned.append(line.strip())
        return " ".join(cleaned).strip()

    # Fallback: first 500 words
    words = report_text.split()[:500]
    return " ".join(words)


def index_report_summary(notebook, report_file_path):
    """Extract TL;DR from a report file and index it as a notebook summary."""
    report_path = Path(report_file_path)
    if not report_path.exists():
        print(f"Error: report file not found: {report_file_path}", file=sys.stderr)
        return 0

    report_text = report_path.read_text()
    if not report_text.strip():
        print(f"Warning: report file is empty: {report_file_path}", file=sys.stderr)
        return 0

    summary_text = extract_tldr(report_text)

    return index_notebook_summary(
        notebook_id=notebook.get("id", ""),
        notebook_alias=notebook.get("alias", ""),
        category_slug=notebook.get("category", ""),
        summary_text=summary_text,
        created_at=notebook.get("created_at", ""),
    )


def main():
    parser = argparse.ArgumentParser(description="Index notebook sources into ChromaDB for RAG routing")
    parser.add_argument("--notebook-id", help="Specific notebook ID to index")
    parser.add_argument("--all", action="store_true", help="Re-index all notebooks")
    parser.add_argument("--companion-note", help="Path to companion note markdown file")
    parser.add_argument("--report-file", help="Path to report markdown file (extracts TL;DR for summary indexing)")
    args = parser.parse_args()

    notebooks = load_notebooks()

    if not notebooks:
        print("No notebooks found in registry.", file=sys.stderr)
        sys.exit(1)

    if args.all:
        total = 0
        for nb in notebooks:
            count = index_single_notebook(nb)
            total += count
        print(f"\nRe-indexed all {len(notebooks)} notebooks. Total documents: {total}")
        return

    if args.notebook_id:
        target = None
        for nb in notebooks:
            if nb.get("id") == args.notebook_id:
                target = nb
                break
        if not target:
            print(f"Error: notebook {args.notebook_id} not found in registry", file=sys.stderr)
            sys.exit(1)
        if args.report_file:
            index_report_summary(target, args.report_file)
        else:
            index_single_notebook(target, companion_note_path=args.companion_note)
        return

    # Default: index the most recent notebook
    notebooks.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    most_recent = notebooks[0]
    print(f"Indexing most recent notebook: {most_recent.get('alias', most_recent.get('id'))}")
    index_single_notebook(most_recent, companion_note_path=args.companion_note)


if __name__ == "__main__":
    main()
