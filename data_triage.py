#!/usr/bin/env python3
"""
Data triage & volume management for OpenClaw Research Automation System.

Pre-processing step before the weekly pipeline: runs cross-stream dedup,
analyzes volume per category, applies grouping rules (merge thin, split fat),
and outputs a triage plan JSON.

Usage:
    python data_triage.py --auto          # Cron mode: apply default rules, save plan
    python data_triage.py --summary       # Print summary for Telegram /triage command
    python data_triage.py --dry-run       # Preview without saving
"""

import argparse
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lib.file_utils import load_json, save_json, now_iso
from lib.url_utils import normalize_url_deep, dedup_urls

PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
SOURCES_FILE = DATA_DIR / "sources.json"
NEWS_QUEUE_FILE = DATA_DIR / "news-queue.json"
CATEGORIES_FILE = DATA_DIR / "categories.json"
TRIAGE_PLAN_FILE = DATA_DIR / "triage-plan.json"

# ── Grouping thresholds ──
MIN_SOURCES_FOR_OWN_NOTEBOOK = 3
MAX_SOURCES_PER_NOTEBOOK = 50
IDEAL_SPLIT_SIZE = 15
MAX_BEFORE_SPLIT = 30




# ── Affinity map from daily_section groupings ──

def build_affinity_map(categories_data):
    """Build section → [slugs] mapping from categories.json daily_section field."""
    section_map = {}
    for cat in categories_data.get("categories", []):
        section = cat.get("daily_section", "News")
        slug = cat.get("slug")
        if not slug:
            continue
        section_map.setdefault(section, []).append(slug)
    return section_map


def find_affinity_partner(slug, section_map, category_counts):
    """Find the best merge partner for a thin category.

    Strategy: merge with the largest sibling in the same daily_section.
    """
    # Find which section this slug belongs to
    target_section = None
    for section, slugs in section_map.items():
        if slug in slugs:
            target_section = section
            break

    if not target_section:
        return None

    # Find largest sibling (excluding self and empty categories)
    siblings = [s for s in section_map[target_section] if s != slug]
    if not siblings:
        return None

    # Pick sibling with most sources
    best = max(siblings, key=lambda s: category_counts.get(s, 0))
    return best


# ── Semantic dedup ──

def semantic_dedup(items, threshold=0.92):
    """Flag items with near-identical titles as duplicates using embeddings.

    Only runs when OPENAI_API_KEY is set. Returns list of
    (duplicate_id, original_id, reason) tuples.
    """
    if len(items) < 2:
        return []
    if not os.environ.get("OPENAI_API_KEY"):
        print("  Semantic dedup skipped (OPENAI_API_KEY not set)")
        return []

    try:
        import openai
    except ImportError:
        print("  Semantic dedup skipped (openai not installed)")
        return []

    titles = [item.get("title", "") for item in items]
    # Skip items without meaningful titles
    if not any(t.strip() for t in titles):
        return []

    try:
        client = openai.OpenAI()
        resp = client.embeddings.create(model="text-embedding-3-small", input=titles)
        embeddings = [e.embedding for e in resp.data]
    except Exception as e:
        print(f"  Semantic dedup failed: {e}")
        return []

    # Pairwise cosine similarity
    duplicates = []
    for i in range(len(embeddings)):
        for j in range(i + 1, len(embeddings)):
            sim = _cosine_similarity(embeddings[i], embeddings[j])
            if sim > threshold:
                duplicates.append((items[j]["id"], items[i]["id"], f"title_sim={sim:.3f}"))
    return duplicates


def _cosine_similarity(a, b):
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── Core triage pipeline ──

def reset_stale_processing(dry_run=False):
    """Reset items stuck in 'processing' for >24h with no notebook_id.

    These typically result from a pipeline crash or timeout mid-run.
    """
    sources_data = load_json(SOURCES_FILE)
    threshold = datetime.now(timezone.utc) - timedelta(hours=24)
    threshold_iso = threshold.isoformat()
    reset_count = 0

    for source in sources_data.get("sources", []):
        if (source.get("status") == "processing"
                and not source.get("notebook_id")
                and (source.get("updated_at") or source.get("added_at") or "") < threshold_iso):
            if not dry_run:
                source["status"] = "queued"
                source["reset_reason"] = "stale_processing_auto_reset"
            reset_count += 1

    if reset_count:
        print(f"  Auto-reset {reset_count} stale processing items to queued")
        if not dry_run:
            save_json(SOURCES_FILE, sources_data)

    return reset_count


def run_triage(dry_run=False):
    """Execute the full triage pipeline.

    Returns the triage plan dict.
    """
    # Reset stale processing items before triage
    reset_stale_processing(dry_run=dry_run)

    sources_data = load_json(SOURCES_FILE)
    news_data = load_json(NEWS_QUEUE_FILE)
    categories_data = load_json(CATEGORIES_FILE)

    # 1. Collect eligible items (queued + daily-digested are eligible for weekly)
    eligible_sources = [
        s for s in sources_data.get("sources", [])
        if s.get("status") in ("queued", "daily-digested")
    ]
    eligible_news = [
        a for a in news_data.get("articles", [])
        if a.get("status") in ("queued", "daily-digested")
    ]

    total_before = len(eligible_sources) + len(eligible_news)

    # 2. Run cross-stream dedup
    dedup_result = dedup_urls(sources_data, news_data)

    # 3. Apply dedup removals (mark duplicates)
    sources_removed_ids = {r["id"] for r in dedup_result["sources_removed"]}
    news_removed_ids = {r["id"] for r in dedup_result["news_removed"]}

    if not dry_run:
        for src in sources_data.get("sources", []):
            if src["id"] in sources_removed_ids:
                src["status"] = "duplicate"
        for art in news_data.get("articles", []):
            if art["id"] in news_removed_ids:
                art["status"] = "duplicate"
        save_json(SOURCES_FILE, sources_data)
        save_json(NEWS_QUEUE_FILE, news_data)

    # Re-collect eligible after dedup
    eligible_sources = [
        s for s in sources_data.get("sources", [])
        if s.get("status") in ("queued", "daily-digested") and s["id"] not in sources_removed_ids
    ]
    eligible_news = [
        a for a in news_data.get("articles", [])
        if a.get("status") in ("queued", "daily-digested") and a["id"] not in news_removed_ids
    ]

    # 2b. Semantic dedup on eligible sources (title similarity)
    sem_dups = semantic_dedup(eligible_sources)
    sem_dup_ids = set()
    for dup_id, orig_id, reason in sem_dups:
        sem_dup_ids.add(dup_id)
        sources_removed_ids.add(dup_id)
        dedup_result["sources_removed"].append({
            "id": dup_id, "duplicate_of": orig_id, "reason": reason,
        })
        if not dry_run:
            for src in sources_data.get("sources", []):
                if src["id"] == dup_id:
                    src["status"] = "duplicate"
            save_json(SOURCES_FILE, sources_data)

    eligible_sources = [s for s in eligible_sources if s["id"] not in sem_dup_ids]

    total_after = len(eligible_sources) + len(eligible_news)

    # 4. Count by category (sources only — news goes to news-digest separately)
    category_counts = {}
    category_source_ids = {}
    for src in eligible_sources:
        cat = src.get("category", "general")
        src_id = src.get("id")
        if not src_id:
            continue
        category_counts[cat] = category_counts.get(cat, 0) + 1
        category_source_ids.setdefault(cat, []).append(src_id)

    # 5. Build affinity map
    section_map = build_affinity_map(categories_data)

    # 6. Build category name lookup
    warnings = []
    cat_names = {}
    for cat in categories_data.get("categories", []):
        slug = cat.get("slug")
        name = cat.get("name", slug or "unknown")
        if not slug:
            warnings.append(f"Category without slug: {cat}")
            continue
        cat_names[slug] = name

    # 7. Apply grouping rules → notebook groups
    notebook_groups = []
    merged_into = {}  # slug → target_slug for merged categories

    # First pass: identify thin categories that need merging
    seen_slugs = set()
    all_slugs = []
    for cat in categories_data.get("categories", []):
        slug = cat.get("slug")
        if not slug:
            continue
        if slug in seen_slugs:
            warnings.append(f"Duplicate category slug: {slug}")
            continue
        seen_slugs.add(slug)
        all_slugs.append(slug)
    thin_slugs = []
    normal_slugs = []
    fat_slugs = []

    for slug in all_slugs:
        count = category_counts.get(slug, 0)
        if count == 0:
            warnings.append(f"No items in category: {slug}")
        elif count < MIN_SOURCES_FOR_OWN_NOTEBOOK:
            thin_slugs.append(slug)
        elif count > MAX_BEFORE_SPLIT:
            fat_slugs.append(slug)
        else:
            normal_slugs.append(slug)

    # Merge thin categories with affinity partners
    merge_groups = {}  # target_slug → [merged_slugs]
    for slug in thin_slugs:
        partner = find_affinity_partner(slug, section_map, category_counts)
        if partner:
            merged_into[slug] = partner
            merge_groups.setdefault(partner, []).append(slug)

    # Handle thin categories without partners (standalone thin)
    orphan_thin = [s for s in thin_slugs if s not in merged_into]
    # Group orphan thin categories together if there are multiple
    if orphan_thin:
        if len(orphan_thin) == 1:
            # Single orphan — give it its own notebook
            normal_slugs.append(orphan_thin[0])
        else:
            # Multiple orphans — merge them together
            largest_orphan = max(orphan_thin, key=lambda s: category_counts.get(s, 0))
            for s in orphan_thin:
                if s != largest_orphan:
                    merged_into[s] = largest_orphan
                    merge_groups.setdefault(largest_orphan, []).append(s)
            normal_slugs.append(largest_orphan)

    # Build notebook groups for normal categories (+ their merged thin ones)
    for slug in normal_slugs:
        all_cats = [slug] + merge_groups.get(slug, [])
        all_ids = []
        for cat_slug in all_cats:
            all_ids.extend(category_source_ids.get(cat_slug, []))

        is_merged = len(all_cats) > 1
        source_count = len(all_ids)

        if source_count == 0:
            continue

        # Enforce hard limit
        if source_count > MAX_SOURCES_PER_NOTEBOOK:
            all_ids = all_ids[:MAX_SOURCES_PER_NOTEBOOK]
            source_count = MAX_SOURCES_PER_NOTEBOOK
            warnings.append(f"Truncated {slug} group to {MAX_SOURCES_PER_NOTEBOOK} sources (hard limit)")

        if is_merged:
            merged_names = [cat_names.get(s, s) for s in all_cats if s != slug]
            label = cat_names.get(slug, slug) + " + " + " + ".join(merged_names)
            nb_slug = "-".join(all_cats)
            merge_parts = ", ".join(
                f"{cat_names.get(s, s)} ({category_counts.get(s, 0)})" for s in all_cats
            )
            merge_reason = f"{merge_parts} — merged (below threshold of {MIN_SOURCES_FOR_OWN_NOTEBOOK})"
        else:
            label = cat_names.get(slug, slug)
            nb_slug = slug
            merge_reason = None

        notebook_groups.append({
            "notebook_label": label,
            "notebook_slug": nb_slug,
            "categories": all_cats,
            "source_ids": all_ids,
            "source_count": source_count,
            "is_merged": is_merged,
            "is_split": False,
            "merge_reason": merge_reason,
        })

    # Split fat categories into sub-notebooks
    for slug in fat_slugs:
        all_cats = [slug] + merge_groups.get(slug, [])
        all_ids = []
        for cat_slug in all_cats:
            all_ids.extend(category_source_ids.get(cat_slug, []))

        source_count = len(all_ids)
        num_splits = math.ceil(source_count / IDEAL_SPLIT_SIZE)

        for i in range(num_splits):
            start = i * IDEAL_SPLIT_SIZE
            end = min(start + IDEAL_SPLIT_SIZE, source_count)
            # Enforce hard limit per split
            if end - start > MAX_SOURCES_PER_NOTEBOOK:
                end = start + MAX_SOURCES_PER_NOTEBOOK

            chunk_ids = all_ids[start:end]
            part_num = i + 1

            notebook_groups.append({
                "notebook_label": f"{cat_names.get(slug, slug)} (Part {part_num}/{num_splits})",
                "notebook_slug": f"{slug}-part{part_num}",
                "categories": all_cats,
                "source_ids": chunk_ids,
                "source_count": len(chunk_ids),
                "is_merged": len(all_cats) > 1,
                "is_split": True,
                "merge_reason": None,
                "split_info": f"Part {part_num} of {num_splits} ({source_count} total sources)",
            })

    # Count merged categories
    categories_merged = len(merged_into)

    # 8. Build triage plan
    plan = {
        "version": 1,
        "generated_at": now_iso(),
        "dedup_report": {
            "total_sources_before": total_before,
            "total_sources_after": total_after,
            "sources_removed": dedup_result["sources_removed"],
            "news_removed": dedup_result["news_removed"],
        },
        "category_counts": category_counts,
        "notebook_groups": notebook_groups,
        "warnings": warnings,
        "summary": {
            "total_queued_sources": len(eligible_sources),
            "total_queued_news": len(eligible_news),
            "total_notebooks_planned": len(notebook_groups),
            "categories_merged": categories_merged,
            "duplicates_found": len(dedup_result["sources_removed"]) + len(dedup_result["news_removed"]),
        },
    }

    return plan


def save_plan(plan):
    """Save the triage plan to disk."""
    save_json(TRIAGE_PLAN_FILE, plan)


def format_summary(plan):
    """Format the triage plan as a human-readable summary for Telegram."""
    s = plan["summary"]
    d = plan["dedup_report"]
    lines = [
        "Triage Summary:",
        f"  Sources: {s['total_queued_sources']} queued",
        f"  News: {s['total_queued_news']} queued",
        f"  Duplicates removed: {s['duplicates_found']}",
        f"    Sources: {len(d['sources_removed'])}",
        f"    News: {len(d['news_removed'])}",
        "",
        f"Notebook Plan ({s['total_notebooks_planned']} notebooks):",
    ]

    for nb in plan["notebook_groups"]:
        flags = []
        if nb["is_merged"]:
            flags.append("merged")
        if nb["is_split"]:
            flags.append("split")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"  {nb['notebook_label']}: {nb['source_count']} sources{flag_str}")
        if nb.get("merge_reason"):
            lines.append(f"    Reason: {nb['merge_reason']}")

    if plan["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        for w in plan["warnings"]:
            lines.append(f"  {w}")

    if s["categories_merged"] > 0:
        lines.append("")
        lines.append(f"Categories merged: {s['categories_merged']}")

    lines.append("")
    lines.append(f"Generated: {plan['generated_at']}")
    lines.append("\nDone.")

    return "\n".join(lines)


def load_existing_plan():
    """Load the most recent triage plan if it exists."""
    if TRIAGE_PLAN_FILE.exists():
        return load_json(TRIAGE_PLAN_FILE)
    return None


def is_plan_fresh(plan, max_age_hours=2):
    """Check if an existing plan is fresh enough to use."""
    if not plan:
        return False
    generated_at = plan.get("generated_at", "")
    if not generated_at:
        return False
    try:
        gen_time = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - gen_time
        return age.total_seconds() < max_age_hours * 3600
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Data triage for OpenClaw Research")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--auto", action="store_true", help="Cron mode: apply rules and save plan")
    group.add_argument("--summary", action="store_true", help="Print summary for Telegram")
    group.add_argument("--dry-run", action="store_true", help="Preview without saving")

    args = parser.parse_args()

    if args.summary:
        # Show existing plan or generate fresh one
        plan = load_existing_plan()
        if plan and is_plan_fresh(plan):
            print(format_summary(plan))
        else:
            plan = run_triage(dry_run=True)
            print(format_summary(plan))
        return

    if args.dry_run:
        plan = run_triage(dry_run=True)
        print(format_summary(plan))
        print("\n(Dry run — no changes saved)")
        return

    if args.auto:
        plan = run_triage(dry_run=False)
        save_plan(plan)
        print(format_summary(plan))
        print(f"\nTriage plan saved to {TRIAGE_PLAN_FILE}")
        return


if __name__ == "__main__":
    main()
