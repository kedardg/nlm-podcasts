#!/usr/bin/env python3
"""Batch triage: classify all queued items using hybrid classifier.

Reads queued items from data/sources.json and data/news-queue.json,
classifies each as research/news/blog using domain rules + local LLM,
and saves results to data/triage-results.json.

Usage:
    .venv/bin/python3 scripts/hf_triage.py                    # Full run
    .venv/bin/python3 scripts/hf_triage.py --dry-run           # Preview only
    .venv/bin/python3 scripts/hf_triage.py --no-llm            # Domain rules only
    .venv/bin/python3 scripts/hf_triage.py --batch-size 50     # Smaller batches
"""

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.file_utils import load_json, save_json, now_iso
from lib.log_utils import StepTimer
from lib.classifier import (
    classify, classify_by_domain, classify_by_title, classify_batch,
    ContentType, load_model, unload_model,
)

DATA_DIR = PROJECT_ROOT / "data"
SOURCES_FILE = DATA_DIR / "sources.json"
NEWS_QUEUE_FILE = DATA_DIR / "news-queue.json"
TRIAGE_RESULTS_FILE = DATA_DIR / "triage-results.json"


def collect_items():
    """Collect all queued items from sources and news queue."""
    sources_data = load_json(SOURCES_FILE)
    news_data = load_json(NEWS_QUEUE_FILE)

    items = []

    for src in sources_data.get("sources", []):
        if src.get("status") not in ("queued", "daily-digested"):
            continue
        items.append({
            "id": src.get("id", ""),
            "url": src.get("url", ""),
            "title": src.get("title", ""),
            "origin": "sources",
            "category": src.get("category", ""),
            "source_type": src.get("type", "url"),
        })

    for art in news_data.get("articles", []):
        if art.get("status") not in ("queued", "daily-digested"):
            continue
        items.append({
            "id": art.get("id", ""),
            "url": art.get("url", ""),
            "title": art.get("title", ""),
            "origin": "news",
            "category": art.get("category", ""),
            "source_type": art.get("content_type", "unknown"),
        })

    return items


def run_triage(batch_size=100, use_llm=True, dry_run=False):
    """Run batch triage on all queued items."""
    print(f"HF Triage — {now_iso()}")
    print(f"  LLM: {'enabled' if use_llm else 'disabled'}")
    print(f"  Batch size: {batch_size}")
    print(f"  Dry run: {dry_run}")
    print()

    # Collect items
    with StepTimer("collect-items"):
        items = collect_items()

    if not items:
        print("No queued items to classify.")
        return

    print(f"Collected {len(items)} items ({sum(1 for i in items if i['origin'] == 'sources')} sources, "
          f"{sum(1 for i in items if i['origin'] == 'news')} news)")
    print()

    # Load existing results for incremental triage
    existing_results = {}
    if TRIAGE_RESULTS_FILE.exists():
        prev = load_json(TRIAGE_RESULTS_FILE)
        for r in prev.get("items", []):
            existing_results[r.get("id", "")] = r

    # Filter out already-classified items
    new_items = [i for i in items if i["id"] not in existing_results]
    cached_count = len(items) - len(new_items)
    if cached_count > 0:
        print(f"Skipping {cached_count} already-classified items (cached)")

    if not new_items:
        print("All items already classified.")
        return

    # Classify in batches
    all_results = list(existing_results.values())  # Start with cached
    total_new = len(new_items)
    domain_count = 0
    title_count = 0
    llm_count = 0
    default_count = 0

    # Pre-load model if using LLM (avoids per-batch loading)
    needs_llm = False
    if use_llm:
        for item in new_items:
            if not classify_by_domain(item["url"]) and not classify_by_title(item["title"]):
                needs_llm = True
                break

    if needs_llm:
        with StepTimer("load-model"):
            print("Loading Qwen2.5-7B-Instruct-4bit...")
            load_model()
            print("Model loaded.")
    print()

    batch_start = time.time()
    for batch_idx in range(0, total_new, batch_size):
        batch = new_items[batch_idx:batch_idx + batch_size]
        batch_num = batch_idx // batch_size + 1
        total_batches = (total_new + batch_size - 1) // batch_size

        print(f"Batch {batch_num}/{total_batches} ({len(batch)} items)...")

        with StepTimer(f"batch-{batch_num}"):
            results = classify_batch(batch, use_llm=use_llm)

        # Count methods
        for r in results:
            method = r["classification"]["method"]
            if method == "domain_rule":
                domain_count += 1
            elif method == "title_heuristic":
                title_count += 1
            elif method == "llm":
                llm_count += 1
            else:
                default_count += 1

        all_results.extend(results)

        # Checkpoint save after each batch
        if not dry_run:
            _save_results(all_results, domain_count, title_count, llm_count, default_count)

        elapsed = time.time() - batch_start
        items_done = min(batch_idx + batch_size, total_new)
        rate = items_done / elapsed if elapsed > 0 else 0
        remaining = (total_new - items_done) / rate if rate > 0 else 0
        print(f"  Done: {items_done}/{total_new} | {rate:.1f} items/s | ETA: {remaining:.0f}s")
        print()

    # Unload model to free memory
    if needs_llm:
        unload_model()

    # Final save
    if not dry_run:
        _save_results(all_results, domain_count, title_count, llm_count, default_count)

    # Summary
    total_elapsed = time.time() - batch_start
    type_counts = {}
    for r in all_results:
        ct = r["classification"]["content_type"]
        type_counts[ct] = type_counts.get(ct, 0) + 1

    print("═══ Triage Summary ═══")
    print(f"  Total classified: {len(all_results)}")
    print(f"    New: {total_new} | Cached: {cached_count}")
    print(f"  Methods: domain={domain_count} title={title_count} llm={llm_count} default={default_count}")
    print(f"  Results: {dict(type_counts)}")
    print(f"  Time: {total_elapsed:.1f}s")
    if not dry_run:
        print(f"  Saved: {TRIAGE_RESULTS_FILE}")
    else:
        print("  (Dry run — not saved)")


def _save_results(results, domain_count, title_count, llm_count, default_count):
    """Save triage results to disk."""
    output = {
        "generated_at": now_iso(),
        "total": len(results),
        "methods": {
            "domain_rule": domain_count,
            "title_heuristic": title_count,
            "llm": llm_count,
            "default": default_count,
        },
        "items": results,
    }
    save_json(TRIAGE_RESULTS_FILE, output)


def main():
    parser = argparse.ArgumentParser(description="Batch triage classifier")
    parser.add_argument("--batch-size", type=int, default=100,
                        help="Items per batch (default: 100)")
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip LLM, use domain rules + title heuristics only")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without saving results")
    args = parser.parse_args()

    run_triage(
        batch_size=args.batch_size,
        use_llm=not args.no_llm,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
