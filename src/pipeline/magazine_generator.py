#!/usr/bin/env python3
"""Generate structured magazine JSON from triaged items + NLM summaries.

Reads triage-results.json (content classifications) and optionally an NLM
report (deep summaries for research items). Uses OpenAI GPT-4o to write
briefs for news/blog items and section introductions. Outputs a magazine
JSON file for PDF rendering.

Usage:
    .venv/bin/python3 scripts/magazine_generator.py --date 2026-03-08
    .venv/bin/python3 scripts/magazine_generator.py --date 2026-03-08 --dry-run
    .venv/bin/python3 scripts/magazine_generator.py --date 2026-03-08 --no-openai
    .venv/bin/python3 scripts/magazine_generator.py \
        --triage data/triage-results.json \
        --nlm-report data/daily-feed/2026-03-08-report.md \
        --output data/daily-feed/2026-03-08-magazine.json
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lib.file_utils import load_json, save_json, now_iso
from lib.log_utils import StepTimer
from lib.url_utils import normalize_url_deep

try:
    import llm_tracer
    _LLM_TRACER = True
except ImportError:
    _LLM_TRACER = False


class _NullCtx:
    def __enter__(self): return self
    def __exit__(self, *_): pass

DATA_DIR = PROJECT_ROOT / "data"
DAILY_FEED_DIR = DATA_DIR / "daily-feed"
TRIAGE_FILE = DATA_DIR / "triage-results.json"
CATEGORIES_FILE = DATA_DIR / "categories.json"
PROMPT_FILE = PROJECT_ROOT / "config" / "prompts" / "magazine-structure-prompt.txt"

# Section order matching daily feed
SECTION_ORDER = ["AI Research", "Tech", "Development", "News"]


def load_category_sections():
    """Build slug → daily_section mapping from categories.json."""
    cats = load_json(CATEGORIES_FILE)
    mapping = {}
    for cat in cats.get("categories", []):
        mapping[cat["slug"]] = cat.get("daily_section", "News")
    return mapping


def load_nlm_report(report_path):
    """Parse NLM report and return url→summary map.

    Imports parse_nlm_report from daily_feed_generator to reuse the v3 parser.
    """
    if not report_path or not Path(report_path).exists():
        return {}

    try:
        from daily_feed_generator import parse_nlm_report
        text = Path(report_path).read_text()
        parsed = parse_nlm_report(text)
        return parsed.get("url_map", {})
    except Exception as e:
        print(f"WARNING: Could not parse NLM report: {e}")
        return {}


def group_items_by_section(triage_items, section_map):
    """Group triaged items by daily_section with cross-section URL dedup.

    First section in SECTION_ORDER wins when a URL appears in multiple sections.
    Returns dict of section_name → list of items.
    """
    sections = {s: [] for s in SECTION_ORDER}
    seen_urls = set()

    for item in triage_items:
        category = item.get("category", "general")
        section = section_map.get(category, "News")
        if section not in sections:
            section = "News"
        sections[section].append(item)

    # Dedup: iterate in SECTION_ORDER so first section wins.
    # Key on the canonical URL so arXiv abs/pdf/html variants, GitHub casing,
    # and tracking-param URLs don't slip through as separate entries.
    deduped = {s: [] for s in SECTION_ORDER}
    for section_name in SECTION_ORDER:
        for item in sections[section_name]:
            url = item.get("url", "")
            norm = normalize_url_deep(url) if url else ""
            if norm and norm in seen_urls:
                continue
            if norm:
                seen_urls.add(norm)
            deduped[section_name].append(item)

    return deduped


def select_top_stories(sections: list[dict], max_stories: int = 5) -> list[dict]:
    """Select top 3-5 stories for the front page.

    Priority:
    1. Featured items (research with NLM summary) — highest value
    2. If <3 featured, fill from standard research items by confidence score
    3. At most 1 non-research item (only if it has a summary)
    4. Tie-break by section diversity (prefer one item per section)

    Returns list of item dicts (copies).
    """
    featured = []
    standard_research = []
    non_research_with_summary = []

    for section in sections:
        section_name = section.get("name", "")
        for item in section.get("items", []):
            enriched = {**item, "section": section_name}
            if item.get("type") == "featured":
                featured.append(enriched)
            elif item.get("type") == "standard" and item.get("content_type") == "research":
                standard_research.append(enriched)
            elif item.get("type") in ("standard", "brief") and item.get("summary") and item.get("content_type") != "research":
                non_research_with_summary.append(enriched)

    # Sort by confidence descending for tie-breaking
    standard_research.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    non_research_with_summary.sort(key=lambda x: x.get("confidence", 0), reverse=True)

    top = []
    used_sections = set()

    # Diversity key: unseen-section items win, confidence breaks ties.
    # Must be re-evaluated after each pick because `used_sections` grows;
    # sorting once (as in the original implementation) lets one section
    # monopolize the front page when all its items start unseen.
    def diversity_key(item):
        section_seen = item["section"] in used_sections
        return (section_seen, -item.get("confidence", 0))

    def pick_with_diversity(pool, limit):
        """Consume `pool` one item at a time, re-ranking by diversity each round."""
        remaining = list(pool)
        while remaining and len(top) < limit:
            remaining.sort(key=diversity_key)
            choice = remaining.pop(0)
            top.append(choice)
            used_sections.add(choice["section"])

    # Step 1: Add featured (cap at max_stories)
    pick_with_diversity(featured, max_stories)

    # Step 2: If <3, fill from standard research (dedup on url)
    if len(top) < 3:
        taken_urls = {t.get("url") for t in top if t.get("url")}
        fill_pool = [i for i in standard_research if i.get("url") not in taken_urls]
        pick_with_diversity(fill_pool, max(3, len(top)))

    # Step 3: At most 1 non-research item, only if none already present
    non_research_count = sum(1 for t in top if t.get("content_type") != "research")
    if non_research_count == 0 and len(top) < max_stories:
        taken_urls = {t.get("url") for t in top if t.get("url")}
        for item in non_research_with_summary:
            if item.get("url") in taken_urls:
                continue
            top.append(item)
            break

    return top[:max_stories]


def build_magazine_items(section_items, nlm_url_map):
    """Build magazine items from triaged items + NLM data.

    Determines item type (featured/standard/brief) based on:
    - research with NLM summary → featured
    - research without NLM summary → standard (needs GPT brief)
    - news/blog → brief (needs GPT brief)

    Returns list of magazine items and list of items needing briefs.
    """
    magazine_items = []
    needs_brief = []

    for item in section_items:
        url = item.get("url", "")
        title = item.get("title", "")
        content_type = item.get("classification", {}).get("content_type", "news")
        confidence = item.get("classification", {}).get("confidence", 0.5)

        # NLM's url_map is keyed on canonical URLs (via normalize_url_deep).
        # Triage items carry the raw source URL, so we must normalize here
        # before probing — otherwise arXiv/GitHub/newsletter variants miss
        # and research items silently fall back to empty GPT briefs.
        nlm_data = nlm_url_map.get(normalize_url_deep(url) if url else "", {})
        nlm_summary = nlm_data.get("summary", "")
        nlm_significance = nlm_data.get("significance", "medium")

        mag_item = {
            "id": item.get("id", ""),
            "title": title,
            "url": url,
            "category": item.get("category", ""),
            "content_type": content_type,
            "confidence": confidence,
        }

        if content_type == "research" and nlm_summary:
            # Featured: full NLM analysis
            mag_item["type"] = "featured"
            mag_item["summary"] = nlm_summary
            mag_item["significance"] = nlm_significance
        elif content_type == "research":
            # Standard: needs a GPT summary
            mag_item["type"] = "standard"
            mag_item["summary"] = ""
            mag_item["significance"] = "medium"
            needs_brief.append(mag_item)
        else:
            # Brief: news/blog
            mag_item["type"] = "brief"
            mag_item["summary"] = nlm_summary if nlm_summary else ""
            mag_item["significance"] = "low"
            if not nlm_summary:
                needs_brief.append(mag_item)

        magazine_items.append(mag_item)

    return magazine_items, needs_brief


def generate_briefs_openai(sections_needing_briefs):
    """Call OpenAI GPT-4o to generate briefs and section intros.

    Args:
        sections_needing_briefs: dict of section_name → list of items needing briefs

    Returns:
        dict of section_name → {"introduction": str, "briefs": {item_id: summary}}
    """
    try:
        import openai
    except ImportError:
        print("WARNING: openai not installed, skipping brief generation")
        return {}

    prompt_template = PROMPT_FILE.read_text() if PROMPT_FILE.exists() else ""

    # Build the input for GPT-4o
    input_sections = []
    for section_name, items in sections_needing_briefs.items():
        if not items:
            continue
        section_data = {
            "name": section_name,
            "items": []
        }
        for item in items:
            section_data["items"].append({
                "id": item["id"],
                "title": item["title"],
                "url": item["url"],
                "content_type": item["content_type"],
                "needs_brief": not item.get("summary"),
            })
        input_sections.append(section_data)

    if not input_sections:
        return {}

    user_message = f"""Here are the items grouped by section. Write briefs and section introductions.

{json.dumps(input_sections, indent=2)}"""

    try:
        client = openai.OpenAI()
        with (llm_tracer.stage("editorial_briefs") if _LLM_TRACER else _NullCtx()):
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": prompt_template},
                    {"role": "user", "content": user_message},
                ],
                response_format={"type": "json_object"},
                max_tokens=6144,
                temperature=0.3,
            )
        result_text = response.choices[0].message.content
        result = json.loads(result_text)
    except Exception as e:
        print(f"WARNING: OpenAI call failed: {e}")
        return {}

    # Parse result into section → {introduction, preview, briefs}
    output = {"_editorial_brief": result.get("editorial_brief", "")}
    for section in result.get("sections", []):
        name = section.get("name", "")
        briefs_map = {}
        for brief in section.get("briefs", []):
            briefs_map[brief["id"]] = brief.get("summary", "")
        output[name] = {
            "introduction": section.get("introduction", ""),
            "preview": section.get("preview", ""),
            "briefs": briefs_map,
        }
    return output


def build_magazine_json(date_str, grouped_sections, nlm_url_map, use_openai=True):
    """Build the complete magazine JSON.

    Args:
        date_str: Date string YYYY-MM-DD
        grouped_sections: dict of section_name → list of triaged items
        nlm_url_map: URL → NLM summary data
        use_openai: Whether to call OpenAI for briefs

    Returns:
        Magazine JSON dict
    """
    sections = []
    all_needs_brief = {}
    stats = {"total": 0, "research": 0, "news": 0, "blog": 0}

    for section_name in SECTION_ORDER:
        items = grouped_sections.get(section_name, [])
        if not items:
            continue

        mag_items, needs_brief = build_magazine_items(items, nlm_url_map)

        if needs_brief:
            all_needs_brief[section_name] = needs_brief

        # Count stats
        for item in mag_items:
            stats["total"] += 1
            ct = item.get("content_type", "news")
            if ct in stats:
                stats[ct] += 1

        sections.append({
            "name": section_name,
            "introduction": "",
            "preview": "",
            "items": mag_items,
        })

    # Generate briefs via OpenAI
    editorial_brief = ""
    if use_openai and all_needs_brief:
        with StepTimer("openai-briefs"):
            print(f"Generating briefs for {sum(len(v) for v in all_needs_brief.values())} items via GPT-4o...")
            briefs_result = generate_briefs_openai(all_needs_brief)

        # Extract editorial brief
        editorial_brief = briefs_result.get("_editorial_brief", "")

        # Apply briefs, introductions, and previews to sections
        for section in sections:
            section_data = briefs_result.get(section["name"], {})
            if section_data.get("introduction"):
                section["introduction"] = section_data["introduction"]
            if section_data.get("preview"):
                section["preview"] = section_data["preview"]
            briefs_map = section_data.get("briefs", {})
            for item in section["items"]:
                if item["id"] in briefs_map and not item.get("summary"):
                    item["summary"] = briefs_map[item["id"]]
    elif not use_openai:
        print("Skipping OpenAI briefs (--no-openai)")

    # Cap featured items at 3 per section; excess demoted to standard
    for section in sections:
        featured_count = 0
        for item in section["items"]:
            if item.get("type") == "featured":
                featured_count += 1
                if featured_count > 3:
                    item["type"] = "standard"

    magazine = {
        "title": f"Weekly Research Digest — {date_str}",
        "date": date_str,
        "generated_at": now_iso(),
        "editorial_brief": editorial_brief,
        "top_stories": select_top_stories(sections),
        "sections": sections,
        "stats": stats,
    }

    return magazine


def run(date_str=None, triage_path=None, nlm_report_path=None,
        output_path=None, use_openai=True, dry_run=False, render_pdf=False):
    """Main magazine generation pipeline."""
    if _LLM_TRACER:
        llm_tracer.init(app="openclaw-research")

    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print(f"Magazine Generator — {date_str}")
    print(f"  OpenAI: {'enabled' if use_openai else 'disabled'}")
    print(f"  Dry run: {dry_run}")
    print()

    # Load triage results
    triage_file = Path(triage_path) if triage_path else TRIAGE_FILE
    if not triage_file.exists():
        print(f"ERROR: Triage results not found: {triage_file}")
        print("Run scripts/hf_triage.py first.")
        sys.exit(1)

    with StepTimer("load-triage"):
        triage_data = load_json(triage_file)

    triage_items = triage_data.get("items", [])
    print(f"Loaded {len(triage_items)} triaged items")

    # Load NLM report (optional)
    if not nlm_report_path:
        nlm_report_path = DAILY_FEED_DIR / f"{date_str}-report.md"

    with StepTimer("load-nlm-report"):
        nlm_url_map = load_nlm_report(nlm_report_path)

    if nlm_url_map:
        print(f"Loaded NLM report with {len(nlm_url_map)} URL summaries")
    else:
        print("No NLM report available (briefs-only mode)")
    print()

    # Group by section
    section_map = load_category_sections()
    grouped = group_items_by_section(triage_items, section_map)

    for section_name in SECTION_ORDER:
        items = grouped.get(section_name, [])
        if items:
            print(f"  {section_name}: {len(items)} items")

    print()

    # Build magazine JSON
    with StepTimer("build-magazine"):
        magazine = build_magazine_json(date_str, grouped, nlm_url_map, use_openai=use_openai)

    # Summary
    stats = magazine["stats"]
    print(f"\nMagazine Summary:")
    print(f"  Sections: {len(magazine['sections'])}")
    print(f"  Total items: {stats['total']}")
    print(f"  Research: {stats['research']} | News: {stats['news']} | Blog: {stats['blog']}")

    featured = sum(1 for s in magazine["sections"] for i in s["items"] if i["type"] == "featured")
    standard = sum(1 for s in magazine["sections"] for i in s["items"] if i["type"] == "standard")
    brief = sum(1 for s in magazine["sections"] for i in s["items"] if i["type"] == "brief")
    print(f"  Featured: {featured} | Standard: {standard} | Brief: {brief}")

    # Save
    if not dry_run:
        out = Path(output_path) if output_path else DAILY_FEED_DIR / f"{date_str}-magazine.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        save_json(out, magazine)
        print(f"\nSaved: {out}")

        # Render HTML (default)
        from lib.html_renderer import render_magazine_html as _render_html
        html_path = out.with_suffix(".html")
        with StepTimer("render-html"):
            result = _render_html(magazine, html_path)
            print(f"HTML: {result}")

        # Render PDF (optional)
        if render_pdf:
            from lib.pdf_renderer import render_magazine_pdf as _render_pdf
            pdf_path = out.with_suffix(".pdf")
            with StepTimer("render-pdf"):
                result = _render_pdf(magazine, str(pdf_path))
                print(f"PDF: {result}")
    else:
        print("\n(Dry run — not saved)")

    return magazine


def main():
    parser = argparse.ArgumentParser(description="Magazine generator from triage + NLM")
    parser.add_argument("--date", help="Date YYYY-MM-DD (default: today)")
    parser.add_argument("--triage", help="Path to triage-results.json")
    parser.add_argument("--nlm-report", help="Path to NLM report markdown")
    parser.add_argument("--output", help="Output magazine JSON path")
    parser.add_argument("--no-openai", action="store_true",
                        help="Skip OpenAI brief generation")
    parser.add_argument("--pdf", action="store_true",
                        help="Also render PDF from magazine JSON")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without saving")
    args = parser.parse_args()

    run(
        date_str=args.date,
        triage_path=args.triage,
        nlm_report_path=args.nlm_report,
        output_path=args.output,
        use_openai=not args.no_openai,
        dry_run=args.dry_run,
        render_pdf=args.pdf,
    )


if __name__ == "__main__":
    main()
