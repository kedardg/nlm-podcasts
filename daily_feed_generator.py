#!/usr/bin/env python3
"""
Daily Tech Feed Generator for OpenClaw Research Automation System.

Collects all items received today from sources.json and news-queue.json,
classifies them into daily sections, generates a markdown briefing and
styled PDF report, and optionally orchestrates NotebookLM podcast creation.

Usage:
    python daily_feed_generator.py                    # Full pipeline (collect, PDF, podcast info)
    python daily_feed_generator.py --dry-run          # Preview without saving or sending
    python daily_feed_generator.py --date 2026-02-16  # Generate for a specific date
    python daily_feed_generator.py --markdown-only    # Generate markdown briefing only (no PDF)
    python daily_feed_generator.py --pdf-only         # Generate markdown + PDF only (no podcast)

Requires:
    - markdown-it-py, weasyprint (for PDF generation)
    - lib/pdf_renderer.py
    - data/categories.json with daily_section mappings
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

from lib.file_utils import load_json, save_json, now_iso
from lib.pdf_renderer import _safe_text, _safe_link, _safe_attr

try:
    import llm_tracer
    _LLM_TRACER = True
except ImportError:
    _LLM_TRACER = False


class _NullCtx:
    """No-op context manager used when llm_tracer is not available."""
    def __enter__(self): return self
    def __exit__(self, *_): pass

try:
    from lib.url_utils import normalize_url_deep
except ImportError:
    def normalize_url_deep(url):
        return url

PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
DAILY_FEED_DIR = DATA_DIR / "daily-feed"
SOURCES_FILE = DATA_DIR / "sources.json"
NEWS_QUEUE_FILE = DATA_DIR / "news-queue.json"
CATEGORIES_FILE = DATA_DIR / "categories.json"

# Maximum news/blog items included in the daily feed across all sections.
# Prevents newsletter batch-runs (which stamp all extracted articles with today's
# date) from flooding the feed. Research items from sources.json are never capped.
NEWS_DAILY_LIMIT = 40

# Ensure Homebrew libraries are discoverable on macOS
if sys.platform == "darwin" and "/opt/homebrew/lib" not in os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", ""):
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (
        os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "") + ":/opt/homebrew/lib"
    ).lstrip(":")




def load_categories():
    """Load categories and build lookup maps."""
    cats_data = load_json(CATEGORIES_FILE)
    categories = cats_data["categories"]
    section_order = cats_data.get("section_order", ["AI Research", "Tech", "Development", "News"])

    # Build slug → category lookup
    slug_map = {c["slug"]: c for c in categories}

    # Build section → [slugs] mapping
    section_slugs = {}
    for c in categories:
        section = c.get("daily_section", "News")
        section_slugs.setdefault(section, []).append(c["slug"])

    return categories, slug_map, section_order, section_slugs


def collect_today_items(target_date):
    """Collect all queued items from the last 2 days.

    Args:
        target_date: date string YYYY-MM-DD

    Returns:
        List of item dicts with unified schema:
        [{url, title, category, source_type, origin, added_at}, ...]
    """
    items = []

    # Include items from today and yesterday (2-day window)
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    valid_dates = [(dt - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(2)]

    # 1. Collect from sources.json (research, github, youtube)
    sources = load_json(SOURCES_FILE)
    for src in sources.get("sources", []):
        added = src.get("added_at", "")
        if not any(added.startswith(d) for d in valid_dates):
            continue
        if src.get("status") not in ("queued", "processing"):
            continue
        items.append({
            "url": src.get("url") or "",
            "title": src.get("title", "Untitled"),
            "category": src.get("category", "general"),
            "source_type": src.get("type", "url"),
            "origin": src.get("origin", "unknown"),
            "added_at": added,
            "data_file": "sources",
            "id": src.get("id"),
        })

    # 2. Collect from news-queue.json (news, blog, uncategorized)
    news_queue = load_json(NEWS_QUEUE_FILE)
    for article in news_queue.get("articles", []):
        extracted = article.get("extracted_at", "")
        if not any(extracted.startswith(d) for d in valid_dates):
            continue
        if article.get("status") not in ("queued",):
            continue
        items.append({
            "url": article.get("url") or article.get("canonical_url", ""),
            "title": article.get("title", "Untitled"),
            "category": article.get("category", "general"),
            "source_type": article.get("content_type", "news"),
            "origin": "newsletter",
            "added_at": extracted,
            "data_file": "news-queue",
            "extracted_at": extracted,
            "id": article.get("id"),
        })

    # Filter out sponsored/ad content that slipped through ingestion
    _SPONSOR_RE = re.compile(r"\((?:sponsor(?:ed)?|ad|promoted|partner)\)", re.IGNORECASE)
    filtered = [i for i in items if not _SPONSOR_RE.search(i.get("title", ""))]

    # Deduplicate by normalized URL
    seen_urls = set()
    deduped = []
    for item in filtered:
        url = item.get("url", "")
        if not url:
            continue
        norm = normalize_url_deep(url)
        if norm not in seen_urls:
            seen_urls.add(norm)
            deduped.append(item)

    # Semantic dedup: drop items that cover the same story as a higher-priority item.
    # sources.json items (research papers, repos) are processed first and take priority
    # over news-queue articles covering the same topic.
    deduped = _semantic_dedup(deduped)

    # Cap news-queue items to prevent newsletter batch-runs from flooding sections.
    # Research items (sources.json) are never capped.
    news_items = [i for i in deduped if i.get("data_file") == "news-queue"]
    research_items = [i for i in deduped if i.get("data_file") != "news-queue"]
    if len(news_items) > NEWS_DAILY_LIMIT:
        news_items.sort(key=lambda x: x.get("extracted_at", ""), reverse=True)
        news_items = news_items[:NEWS_DAILY_LIMIT]

    return research_items + news_items


def _load_openai_key():
    """Resolve the OpenAI API key.

    Priority: OPENAI_API_KEY env var (if real) → ~/.openclaw/.env fallback.
    """
    key = os.environ.get("OPENAI_API_KEY", "")
    if key and "your-key" not in key:
        return key
    # Fallback: read from OpenClaw's .env
    oc_env = Path.home() / ".openclaw" / ".env"
    if oc_env.exists():
        for line in oc_env.read_text().splitlines():
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def _semantic_dedup(items, threshold=0.88):
    """Remove items that cover the same story as a higher-priority item.

    Uses OpenAI text-embedding-3-small embeddings (already unit-normalised, so
    cosine similarity = dot product). Items are processed in priority order:
    sources.json items first (research papers, repos, YouTube), then news-queue
    articles. When a cluster is detected the first-seen (higher-priority) item
    is kept and later duplicates are dropped.

    Falls back to returning items unchanged if openai is unavailable or the API
    key is missing — the function is always safe to call.

    Args:
        items: list of item dicts from collect_today_items
        threshold: cosine similarity above which two titles are the same story

    Returns:
        Deduplicated list, sources.json items first.
    """
    if len(items) <= 1:
        return items

    api_key = _load_openai_key()
    if not api_key:
        return items

    try:
        import openai
    except ImportError:
        return items

    # Process sources.json items first so they always win ties.
    ordered = sorted(items, key=lambda x: (0 if x.get("data_file") == "sources" else 1))
    titles = [i.get("title", "") or "" for i in ordered]

    try:
        client = openai.OpenAI(api_key=api_key)
        with (llm_tracer.stage("semantic_dedup") if _LLM_TRACER else _NullCtx()):
            resp = client.embeddings.create(model="text-embedding-3-small", input=titles)
        # text-embedding-3-small returns unit vectors — cosine sim = dot product
        embeddings = [e.embedding for e in resp.data]
    except Exception as e:
        print(f"  [semantic-dedup] embedding call failed: {e}")
        return items

    kept_indices = []
    kept_embeddings = []

    for idx, emb in enumerate(embeddings):
        duplicate = False
        for kept_emb in kept_embeddings:
            sim = sum(a * b for a, b in zip(emb, kept_emb))
            if sim >= threshold:
                duplicate = True
                break
        if not duplicate:
            kept_indices.append(idx)
            kept_embeddings.append(emb)

    kept = [ordered[i] for i in kept_indices]
    dropped = len(ordered) - len(kept)
    if dropped:
        print(f"  [semantic-dedup] removed {dropped} topic duplicate(s) (threshold={threshold})")

    return kept


def _llm_classify_sponsors(prompt):
    """Call OpenAI to classify sponsored content.

    Uses the OpenAI API key from OpenClaw's config (~/.openclaw/.env).
    Calls gpt-4o-mini for minimal cost (~$0.15/M input tokens).
    """
    api_key = _load_openai_key()
    if not api_key:
        return None

    try:
        import openai
    except ImportError:
        print("  [sponsor-filter] openai package not installed, skipping")
        return None

    try:
        client = openai.OpenAI(api_key=api_key)
        with (llm_tracer.stage("sponsor_filter") if _LLM_TRACER else _NullCtx()):
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=200,
            )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"  [sponsor-filter] LLM call failed: {e}")
        return None


def filter_sponsored_llm(items):
    """LLM post-filter to catch sponsored/promotional items that lack explicit markers.

    Sends titles + domains to a fast model to classify as content vs ad/promotion.
    Runs once per day on the collected batch (~30-60 items), minimal cost.
    Tries Anthropic Haiku first, then OpenAI gpt-4o-mini.
    """
    if not items:
        return items

    # Build a compact list: index, title, domain
    entries = []
    for i, item in enumerate(items):
        title = item.get("title", "")
        url = item.get("url", "")
        try:
            domain = urlparse(url).netloc.lower().removeprefix("www.")
        except Exception:
            domain = ""
        entries.append(f"{i}. {title} [{domain}]")

    prompt = (
        "Below is a numbered list of article titles and domains from tech newsletters.\n"
        "Identify which ones are SPONSORED content, product advertisements, tool promotions,\n"
        "or paid placements — NOT genuine articles, research papers, or news stories.\n\n"
        "Signals of sponsored content:\n"
        "- Title reads like ad copy (imperative CTA: 'Deploy...', 'Redefine...', 'Stop...')\n"
        "- Domain is a product/tool landing page, not a publication\n"
        "- Title promotes a specific commercial product or event\n"
        "- Free trial / demo / quiz / bootcamp promotions\n\n"
        "Reply with ONLY the indices of sponsored items, comma-separated.\n"
        "If none are sponsored, reply: NONE\n\n"
        + "\n".join(entries)
    )

    answer = _llm_classify_sponsors(prompt)
    if answer is None:
        print("  [sponsor-filter] No LLM provider available, skipping")
        return items

    if answer.upper() == "NONE":
        print("  [sponsor-filter] LLM found no sponsored items")
        return items

    # Parse indices
    try:
        indices_to_remove = set()
        for part in answer.replace(" ", "").split(","):
            part = part.strip().rstrip(".")
            if part.isdigit():
                indices_to_remove.add(int(part))
    except Exception:
        print(f"  [sponsor-filter] Could not parse LLM response: {answer}")
        return items

    if indices_to_remove:
        removed = [items[i].get("title", "?") for i in indices_to_remove if i < len(items)]
        print(f"  [sponsor-filter] LLM flagged {len(indices_to_remove)} sponsored items:")
        for t in removed:
            print(f"    - {t[:80]}")

    return [item for i, item in enumerate(items) if i not in indices_to_remove]


def classify_item(item, slug_map):
    """Ensure an item has a valid category from the new system."""
    cat = item.get("category", "general")
    if cat in slug_map:
        return cat
    # Legacy mappings
    if cat == "github":
        return "oss"
    return "general"


def group_by_section(items, slug_map, section_order, section_slugs):
    """Group items into daily sections based on their category's daily_section.

    Returns:
        OrderedDict-like list of (section_name, items_list) in section_order.
    """
    sections = {s: [] for s in section_order}

    for item in items:
        cat_slug = classify_item(item, slug_map)
        cat_info = slug_map.get(cat_slug)
        section = cat_info["daily_section"] if cat_info else "News"
        item["category"] = cat_slug
        item["category_name"] = cat_info["name"] if cat_info else "General"
        sections.setdefault(section, []).append(item)

    # Return in order, skipping empty sections
    return [(s, sections[s]) for s in section_order if sections.get(s)]


def count_origins(items):
    """Count items by origin type."""
    origins = {}
    for item in items:
        o = item.get("origin", "unknown")
        origins[o] = origins.get(o, 0) + 1
    return origins


def generate_briefing_markdown(grouped_sections, target_date, items):
    """Generate the daily briefing markdown from grouped sections."""
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    weekday = dt.strftime("%A")
    month = dt.strftime("%B")
    day = dt.day
    year = dt.year

    total = len(items)
    origins = count_origins(items)

    lines = []
    lines.append(f"# Daily Tech Feed — {weekday}, {month} {day}, {year}")
    lines.append("")
    lines.append(f"> {total} items from {len(origins)} source types")
    lines.append("")

    for section_name, section_items in grouped_sections:
        lines.append("---")
        lines.append("")
        lines.append(f"## {section_name}")
        lines.append(f"*{len(section_items)} items*")
        lines.append("")

        if section_name == "AI Research":
            # Split into papers/preprints vs technical deep dives
            papers = [i for i in section_items if i["source_type"] in ("url",) and _is_paper(i)]
            deep_dives = [i for i in section_items if i not in papers]

            if papers:
                lines.append("### Papers & Preprints")
                for item in papers:
                    lines.append(f"- **{item['title']}** — *{item['category_name']}* | [Source]({item['url']})")
                lines.append("")

            if deep_dives:
                lines.append("### Technical Deep Dives")
                for item in deep_dives:
                    lines.append(f"- **{item['title']}** — *{item['category_name']}* | [Source]({item['url']})")
                lines.append("")

            if not papers and not deep_dives:
                lines.append("*No items in this section today.*")
                lines.append("")

        elif section_name == "Development":
            repos = [i for i in section_items if i["source_type"] in ("url",) and _is_repo(i)]
            tutorials = [i for i in section_items if i not in repos]

            if repos:
                lines.append("### New Repos & Tools")
                for item in repos:
                    lines.append(f"- **{item['title']}** — [GitHub]({item['url']})")
                lines.append("")

            if tutorials:
                lines.append("### Tutorials & Guides")
                for item in tutorials:
                    lines.append(f"- **{item['title']}** — [Source]({item['url']})")
                lines.append("")

        else:
            # Tech, News — simple list
            for item in section_items:
                lines.append(f"- **{item['title']}** — [Source]({item['url']})")
            lines.append("")

    # Footer
    lines.append("---")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    newsletter_count = origins.get("newsletter", 0)
    x_count = origins.get("x_scrape", 0)
    manual_count = origins.get("telegram", 0)
    lines.append(f"*Generated {timestamp} | Sources: {newsletter_count} newsletters, {x_count} X accounts, {manual_count} manual*")

    return "\n".join(lines)


def _is_paper(item):
    """Heuristic: is this item a research paper?"""
    url = item.get("url", "").lower()
    paper_domains = ["arxiv.org", "openreview.net", "papers.nips.cc",
                     "aclanthology.org", "semanticscholar.org", "proceedings.mlr.press",
                     "jmlr.org", "biorxiv.org", "doi.org", "scholar.google.com",
                     "paperswithcode.com", "huggingface.co/papers"]
    return any(d in url for d in paper_domains)


def _is_repo(item):
    """Heuristic: is this item a code repository?"""
    url = item.get("url", "").lower()
    return any(d in url for d in ["github.com", "gitlab.com", "huggingface.co"])


def mark_items_digested(items, digest_id):
    """Mark news-queue items as daily-digested. Sources stay queued for weekly."""
    news_queue = load_json(NEWS_QUEUE_FILE)
    news_ids = {i["id"] for i in items if i["data_file"] == "news-queue"}

    for article in news_queue.get("articles", []):
        if article.get("id") in news_ids:
            article["status"] = "daily-digested"
            article["digest_id"] = digest_id

    save_json(NEWS_QUEUE_FILE, news_queue)


def generate_notebooklm_commands(grouped_sections, target_date, briefing_markdown):
    """Generate the NotebookLM CLI commands for podcast creation.

    Returns a list of shell commands to execute.
    """
    commands = []
    alias = f"daily-{target_date}"

    commands.append(f'nlm notebook create "Daily Feed — {target_date}"')
    commands.append(f"# Capture notebook ID from output, then:")
    commands.append(f"nlm alias set {alias} {{notebook-id}}")
    commands.append("")

    # Collect top URLs across all sections (max 15)
    all_items = []
    for _, section_items in grouped_sections:
        all_items.extend(section_items)

    top_urls = []
    for item in all_items[:15]:
        url = item.get("url", "")
        if url:
            top_urls.append(url)

    for url in top_urls:
        commands.append(f'nlm source add {alias} --url "{url}" --wait')

    commands.append("")
    # Escape the briefing for shell
    commands.append(f'nlm source add {alias} --text "{{briefing_content}}" --title "Daily Feed Briefing" --wait')
    commands.append("")
    commands.append(
        f'nlm audio create {alias} '
        f'--format deep_dive --length long '
        f'--focus "Cover today\'s top AI research papers, tech industry news, new open source tools, '
        f'and important AI policy developments. Prioritize the most significant items and explain '
        f'their implications." --confirm'
    )
    commands.append("")
    commands.append(f"nlm studio status {alias}")

    return commands


def _to_magazine_json(merged_data, target_date):
    """Normalize daily merged_data into the magazine JSON schema.

    Maps the daily pipeline's merged_data format (with nlm_body, nlm_tldr,
    nlm_summary, significance) into the magazine JSON schema consumed by
    both lib/html_renderer and lib/pdf_renderer.

    Items with nlm_body >= 300 chars become 'featured', those with NLM
    coverage become 'standard', and unmatched items become 'brief'.
    """
    from datetime import datetime as dt
    try:
        d = dt.strptime(target_date, "%Y-%m-%d")
        title = f"Daily Tech Feed \u2014 {d.strftime('%B %-d, %Y')}"
    except (ValueError, AttributeError):
        title = f"Daily Tech Feed \u2014 {target_date}"

    sections = []
    total, n_research, n_news, n_blog = 0, 0, 0, 0

    for section in merged_data.get("sections", []):
        section_items = []
        for item in section.get("items", []):
            nlm_body = item.get("nlm_body", "")
            nlm_summary = item.get("nlm_summary", "")
            nlm_tldr = item.get("nlm_tldr", "")
            significance = item.get("significance", "low")

            # Determine item type based on NLM coverage depth
            if nlm_body and len(nlm_body) >= 300:
                item_type = "featured"
            elif nlm_summary or nlm_tldr:
                item_type = "standard"
            else:
                item_type = "brief"

            # Best available summary
            summary = nlm_tldr or nlm_summary or ""

            # Infer content_type from category or origin
            origin = item.get("origin", "")
            cat = item.get("category_name", "").lower()
            if any(k in cat for k in ("ml", "nlp", "cv", "robot")):
                content_type = "research"
                n_research += 1
            elif "news" in cat or "safety" in cat or "general" in cat:
                content_type = "news"
                n_news += 1
            else:
                content_type = "blog"
                n_blog += 1

            total += 1
            section_items.append({
                "id": item.get("id", ""),
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "type": item_type,
                "content_type": content_type,
                "summary": summary,
                "body": nlm_body,
                "significance": significance,
            })

        sections.append({
            "name": section.get("name", ""),
            "introduction": section.get("synthesis", ""),
            "items": section_items,
        })

    return {
        "title": title,
        "date": target_date,
        "editorial_brief": merged_data.get("headline", ""),
        "top_stories": [],  # daily pipeline doesn't pre-select top stories
        "sections": sections,
        "stats": {
            "total": total,
            "research": n_research,
            "news": n_news,
            "blog": n_blog,
        },
    }


def render_magazine_html_output(target_date, nlm_report_path, grouped_sections):
    """Render NLM-first magazine as a self-contained responsive HTML file.

    Args:
        target_date: Date string YYYY-MM-DD
        nlm_report_path: Path to the NLM v2 report markdown file
        grouped_sections: Output from group_by_section()

    Returns:
        Path to the generated HTML file, or None on failure.
    """
    report_text = Path(nlm_report_path).read_text()
    parsed = parse_nlm_report(report_text)

    if not parsed["sections"]:
        print("WARNING: NLM report has no parseable sections, falling back to plain layout.")
        return None

    merged = merge_nlm_with_feed(parsed, grouped_sections)
    magazine_json = _to_magazine_json(merged, target_date)

    html_path = DAILY_FEED_DIR / f"{target_date}.html"
    try:
        from lib.html_renderer import render_magazine_html
        result = render_magazine_html(magazine_json, html_path)
        print(f"Magazine HTML generated: {result}")
        return str(html_path)
    except Exception as e:
        print(f"WARNING: Magazine HTML generation failed: {e}")
        return None


def render_magazine_pdf(target_date, nlm_report_path, grouped_sections):
    """Render NLM-first magazine PDF from a v2 structured report.

    Args:
        target_date: Date string YYYY-MM-DD
        nlm_report_path: Path to the NLM v2 report markdown file
        grouped_sections: Output from group_by_section()

    Returns:
        Path to the generated PDF, or None on failure.
    """
    report_text = Path(nlm_report_path).read_text()
    parsed = parse_nlm_report(report_text)

    if not parsed["sections"]:
        print("WARNING: NLM report has no parseable sections, falling back to plain PDF.")
        return None

    merged = merge_nlm_with_feed(parsed, grouped_sections)

    html = generate_magazine_html(merged, target_date)

    pdf_path = DAILY_FEED_DIR / f"{target_date}.pdf"
    try:
        from lib.pdf_renderer import render_html_pdf
        css_path = str(PROJECT_ROOT / "templates" / "daily-feed.css")
        result = render_html_pdf(html, str(pdf_path), css_path=css_path)
        print(f"Magazine PDF generated: {result}")
        return str(pdf_path)
    except Exception as e:
        print(f"WARNING: Magazine PDF generation failed: {e}")
        return None


def run(target_date=None, dry_run=False, markdown_only=False, pdf_only=False, magazine=False, pdf=False):
    """Main daily feed pipeline.

    Args:
        target_date: Date string YYYY-MM-DD (default: today UTC)
        dry_run: Preview without saving
        markdown_only: Generate markdown briefing only
        pdf_only: Generate markdown + PDF only (no podcast commands) — DEPRECATED, use pdf=True
        magazine: DEPRECATED — HTML is always magazine layout when NLM report exists
        pdf: Also produce PDF alongside HTML (optional archival export)
    """
    if _LLM_TRACER:
        llm_tracer.init(app="openclaw-research")

    if not target_date:
        target_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print(f"Daily Tech Feed — {target_date}")
    print(f"{'(DRY RUN)' if dry_run else ''}")
    print()

    # Load categories
    categories, slug_map, section_order, section_slugs = load_categories()

    # Collect today's items
    items = collect_today_items(target_date)
    print(f"Collected {len(items)} items for {target_date}")

    if not items:
        print("No items found for today. Nothing to generate.")
        return None

    # LLM post-filter for sponsored content that slipped past hard rules
    pre_count = len(items)
    items = filter_sponsored_llm(items)
    if len(items) < pre_count:
        print(f"  Removed {pre_count - len(items)} sponsored items ({len(items)} remaining)")

    # Group by section
    grouped = group_by_section(items, slug_map, section_order, section_slugs)

    for section_name, section_items in grouped:
        print(f"  {section_name}: {len(section_items)} items")

    # Generate markdown briefing
    briefing = generate_briefing_markdown(grouped, target_date, items)

    if dry_run:
        print("\n--- BRIEFING PREVIEW ---")
        print(briefing)
        print("--- END PREVIEW ---\n")

        if not markdown_only:
            print("\nNotebookLM commands that would be executed:")
            for cmd in generate_notebooklm_commands(grouped, target_date, briefing):
                print(f"  {cmd}")
        return briefing

    # Save markdown
    DAILY_FEED_DIR.mkdir(parents=True, exist_ok=True)
    md_path = DAILY_FEED_DIR / f"{target_date}.md"
    md_path.write_text(briefing)
    print(f"\nMarkdown saved: {md_path}")

    if markdown_only:
        return str(md_path)

    # Generate HTML — try magazine layout if NLM report is available
    html_path = DAILY_FEED_DIR / f"{target_date}.html"
    pdf_path = DAILY_FEED_DIR / f"{target_date}.pdf"
    nlm_report_path = DAILY_FEED_DIR / f"{target_date}-report.md"

    if nlm_report_path.exists():
        print(f"NLM report found: {nlm_report_path}")
        html_result = render_magazine_html_output(target_date, str(nlm_report_path), grouped)
        if not html_result:
            print("Magazine HTML failed, generating plain HTML from markdown.")
            # Fallback: wrap markdown briefing in minimal HTML
            _write_plain_html(briefing, html_path, target_date)
    else:
        print(f"No NLM report at {nlm_report_path}, generating plain HTML.")
        _write_plain_html(briefing, html_path, target_date)

    # Optional PDF alongside HTML
    if pdf or pdf_only:
        if nlm_report_path.exists():
            pdf_result = render_magazine_pdf(target_date, str(nlm_report_path), grouped)
            if not pdf_result:
                try:
                    from lib.pdf_renderer import render_pdf
                    render_pdf(briefing, str(pdf_path), skip_toc=True)
                    print(f"Plain PDF generated: {pdf_path}")
                except Exception as e:
                    print(f"WARNING: PDF generation failed: {e}")
        else:
            try:
                from lib.pdf_renderer import render_pdf
                render_pdf(briefing, str(pdf_path), skip_toc=True)
                print(f"PDF generated: {pdf_path}")
            except Exception as e:
                print(f"WARNING: PDF generation failed: {e}")

    if pdf_only:
        return str(html_path)

    # Generate NotebookLM commands
    nlm_commands = generate_notebooklm_commands(grouped, target_date, briefing)

    # Save commands for the daily-digest skill to execute
    commands_path = DAILY_FEED_DIR / f"{target_date}-nlm-commands.txt"
    commands_path.write_text("\n".join(nlm_commands))
    print(f"NotebookLM commands saved: {commands_path}")

    # Mark news-queue items as daily-digested
    digest_id = f"daily-{target_date}"
    mark_items_digested(items, digest_id)
    print(f"Marked {sum(1 for i in items if i['data_file'] == 'news-queue')} news items as daily-digested")

    print(f"\nDaily feed pipeline complete for {target_date}.")
    print(f"Artifacts:")
    print(f"  Markdown: {md_path}")
    print(f"  HTML:     {html_path}")
    if (pdf or pdf_only) and pdf_path.exists():
        print(f"  PDF:      {pdf_path}")
    print(f"  NLM cmds: {commands_path}")

    return {
        "date": target_date,
        "item_count": len(items),
        "markdown_path": str(md_path),
        "html_path": str(html_path),
        "pdf_path": str(pdf_path) if pdf_path.exists() else None,
        "commands_path": str(commands_path),
        "sections": {name: len(sitems) for name, sitems in grouped},
    }


def _parse_nlm_report_v3(report_text):
    """Parse v3 markdown-format NLM report into structured data.

    v3 format uses standard markdown:
    - # Title
    - ## Section headings (AI Research, Tech Industry, Development, News)
    - ### Article titles (sometimes numbered: ### 1. Title)
    - **Source:** URL
    - **TL;DR:** summary
    - Body text after TL;DR
    - ## Key Takeaways with numbered list

    Also handles variant where articles use **Article N.M: Title** instead of ###.
    """
    result = {
        "headline": "",
        "sections": [],
        "takeaways": [],
        "url_map": {},
    }

    lines = report_text.split("\n")

    # Extract headline — first h1 content or first paragraph after h1
    for i, line in enumerate(lines):
        if line.startswith("# ") and not line.startswith("## "):
            result["headline"] = line[2:].strip()
            # Check if next non-empty line is a subtitle/headline
            for j in range(i + 1, min(i + 5, len(lines))):
                ln = lines[j].strip()
                if not ln:
                    continue
                if ln.startswith("## "):
                    break
                # Blockquote headline: > **Headline:** ...
                hm = re.match(r">\s*\*\*Headline:\*\*\s*(.*)", ln)
                if hm:
                    result["headline"] = hm.group(1).strip()
                elif not ln.startswith("#"):
                    # Plain text subtitle — use it as headline if short
                    clean = ln.lstrip("> ").strip()
                    if len(clean) < 300:
                        result["headline"] = clean
                break
            break

    # Split into sections by ## headings
    section_starts = []
    for i, line in enumerate(lines):
        if re.match(r"^## ", line):
            section_starts.append(i)

    for idx, start in enumerate(section_starts):
        section_name = lines[start][3:].strip()
        # Strip stray bold markers from section names (e.g. "**AI** Research")
        section_name = re.sub(r"\*\*([^*]+)\*\*", r"\1", section_name)
        end = section_starts[idx + 1] if idx + 1 < len(section_starts) else len(lines)
        section_lines = lines[start + 1:end]
        section_body = "\n".join(section_lines)

        # Skip Key Takeaways — parse separately
        if re.match(r"key\s+takeaways?", section_name, re.IGNORECASE):
            # Extract numbered list
            for line in section_lines:
                tm = re.match(r"\d+\.\s+(.+)", line.strip())
                if tm:
                    result["takeaways"].append(tm.group(1).strip())
            continue

        # Extract synthesis — text before first ### or **Article
        synthesis = ""
        first_article_line = None
        for i, line in enumerate(section_lines):
            if re.match(r"^###\s", line) or re.match(r"^\*\*Article\s+\d", line):
                first_article_line = i
                break
        if first_article_line is not None and first_article_line > 0:
            synth_text = "\n".join(section_lines[:first_article_line]).strip()
            # Strip italic markers, bold labels like **Overarching Synthesis**
            synth_text = re.sub(r"^\*\*[^*]+\*\*\s*\n?", "", synth_text).strip()
            synth_text = synth_text.strip("*").strip()
            synthesis = synth_text

        # Extract articles — split on ### headings or **Article N.M: Title**
        article_pattern = re.compile(
            r"^(?:###\s+(?:\d+\.\s*)?(.+)|(\*\*Article\s+\d+(?:\.\d+)?:\s*(.+?)\*\*))",
        )
        article_starts = []
        for i, line in enumerate(section_lines):
            m = article_pattern.match(line)
            if m:
                # Group 1 = ### title, Group 3 = **Article** title
                title = (m.group(1) or m.group(3) or "").strip()
                article_starts.append((i, title))

        articles = []
        items = []
        for aidx, (astart, atitle) in enumerate(article_starts):
            aend = article_starts[aidx + 1][0] if aidx + 1 < len(article_starts) else len(section_lines)
            art_lines = section_lines[astart + 1:aend]
            art_text = "\n".join(art_lines)

            # Extract Source URL(s)
            source_matches = re.findall(
                r"\*\*Source:\*\*\s*(.*?)(?:\n|$)", art_text,
            )
            art_url = ""
            if source_matches:
                # Take first URL from the Source line
                url_m = re.search(r"(https?://\S+)", source_matches[0])
                if url_m:
                    art_url = url_m.group(1).strip().rstrip(",;")

            # Extract TL;DR
            tldr_m = re.search(r"\*\*TL;DR:\*\*\s*(.+?)(?:\n|$)", art_text)
            art_tldr = tldr_m.group(1).strip() if tldr_m else ""

            # Extract body — everything after TL;DR line, excluding Source line
            body_lines = []
            past_tldr = False
            for line in art_lines:
                if re.match(r"\s*\*\*Source:\*\*", line):
                    continue
                if re.match(r"\s*\*\*TL;DR:\*\*", line):
                    past_tldr = True
                    continue
                if past_tldr:
                    body_lines.append(line)
                elif not tldr_m:
                    # No TL;DR found — include all non-Source lines as body
                    body_lines.append(line)

            # Strip leading **Analysis:** or similar labels
            art_body = "\n".join(body_lines).strip()
            art_body = re.sub(r"^\*\*(?:Analysis|Body|Details?):\*\*\s*", "", art_body).strip()
            # Strip bullet-point markers from article-style format
            art_body = re.sub(r"^\*\s+", "", art_body)

            if not art_tldr and art_body:
                first_sent = re.split(r"(?<=[.!?])\s+", art_body, maxsplit=1)
                if first_sent:
                    art_tldr = first_sent[0].strip()

            articles.append({
                "title": atitle, "url": art_url,
                "tldr": art_tldr, "body": art_body,
            })

            # Also add as item for backward compat
            significance = _infer_significance(art_tldr or art_body, section_name)
            items.append({
                "url": art_url,
                "summary": art_tldr,
                "significance": significance,
            })

            if art_url:
                norm = normalize_url_deep(art_url)
                result["url_map"][norm] = {
                    "summary": art_tldr,
                    "significance": significance,
                    "tldr": art_tldr,
                    "body": art_body,
                    "title": atitle,
                }

        result["sections"].append({
            "name": section_name,
            "synthesis": synthesis,
            "articles": articles,
            "items": items,
        })

    return result


def parse_nlm_report(report_text):
    """Parse NLM report (v3 markdown format).

    Returns:
        {
            "headline": str,
            "sections": [
                {
                    "name": str,
                    "synthesis": str,
                    "articles": [...],
                    "items": [...]
                }, ...
            ],
            "takeaways": [str, ...],
            "url_map": {url: {"summary": str, "significance": str}, ...}
        }
    """
    if not report_text or not report_text.strip():
        return {
            "headline": "",
            "sections": [],
            "takeaways": [],
            "url_map": {},
        }
    return _parse_nlm_report_v3(report_text)


def _infer_significance(summary, section_name):
    """Infer significance rating from summary content when NLM doesn't provide it."""
    lower = summary.lower()
    high_signals = [
        r"\$\d+[mb]", r"billion", r"million", r"flagship", r"breakthrough",
        r"first\s+implementation", r"state[- ]of[- ]the[- ]art", r"open[- ]weight",
    ]
    low_signals = [
        r"sponsor", r"advertisement", r"promoted", r"sign up", r"register",
    ]
    for pattern in high_signals:
        if re.search(pattern, lower):
            return "high"
    for pattern in low_signals:
        if re.search(pattern, lower):
            return "low"
    return "medium"


def merge_nlm_with_feed(parsed_nlm, grouped_sections):
    """Merge NLM report data with the original feed items.

    Enriches each feed item with NLM's synthesis and per-item summaries.
    Returns a structure ready for magazine template rendering.

    Args:
        parsed_nlm: Output from parse_nlm_report()
        grouped_sections: Output from group_by_section() — list of (name, items)

    Returns:
        {
            "headline": str,
            "sections": [
                {
                    "name": str,
                    "synthesis": str,
                    "items": [
                        {
                            "url": str, "title": str, "category_name": str,
                            "nlm_summary": str, "significance": str,
                            "source_type": str, "origin": str,
                        }, ...
                    ]
                }, ...
            ],
            "takeaways": [str, ...],
            "unmatched_nlm_items": [{"url": str, "summary": str, "significance": str}, ...],
        }
    """
    url_map = parsed_nlm["url_map"]
    # Map NLM section names to feed section names (NLM may use longer names)
    nlm_name_aliases = {"Tech Industry": "Tech"}
    nlm_sections_by_name = {}
    for s in parsed_nlm["sections"]:
        nlm_sections_by_name[s["name"]] = s
        alias = nlm_name_aliases.get(s["name"])
        if alias:
            nlm_sections_by_name[alias] = s
    matched_urls = set()

    # Track URLs placed in sections to avoid cross-section duplicates
    placed_urls = set()

    merged_sections = []
    for section_name, feed_items in grouped_sections:
        nlm_section = nlm_sections_by_name.get(section_name, {})
        synthesis = nlm_section.get("synthesis", "")

        merged_items = []
        for item in feed_items:
            norm_url = normalize_url_deep(item.get("url", ""))
            # Skip items already placed in an earlier section
            if norm_url in placed_urls:
                continue
            nlm_data = url_map.get(norm_url, {})
            # Items without NLM coverage get "low" significance (compact rendering)
            default_sig = "low" if not nlm_data else "medium"
            merged_items.append({
                "url": item.get("url", ""),
                "title": item.get("title", "Untitled"),
                "category_name": item.get("category_name", ""),
                "nlm_summary": nlm_data.get("summary", ""),
                "nlm_tldr": nlm_data.get("tldr", ""),
                "nlm_body": nlm_data.get("body", ""),
                "significance": nlm_data.get("significance", default_sig),
                "source_type": item.get("source_type", "url"),
                "origin": item.get("origin", "unknown"),
            })
            if norm_url:
                placed_urls.add(norm_url)
            if norm_url in url_map:
                matched_urls.add(norm_url)

        # Also add NLM articles/items not in the feed
        if nlm_section:
            nlm_extras = []
            for art in nlm_section.get("articles", []):
                nlm_extras.append({
                    "url": art.get("url", ""), "title": art.get("title", ""),
                    "summary": art.get("tldr", ""), "tldr": art.get("tldr", ""),
                    "body": art.get("body", ""),
                    "significance": "high" if art.get("body") else "medium",
                })
            for nlm_item in nlm_section.get("items", []):
                nlm_extras.append({
                    "url": nlm_item.get("url", ""),
                    "title": re.sub(r"\*\*([^*]+)\*\*", r"\1",
                                    nlm_item.get("summary", "")).split(":")[0].strip()[:80],
                    "summary": nlm_item.get("summary", ""),
                    "tldr": "", "body": "",
                    "significance": nlm_item.get("significance", "medium"),
                })
            seen = set()
            for entry in nlm_extras:
                norm = normalize_url_deep(entry["url"]) if entry["url"] else ""
                if not norm or norm in matched_urls or norm in placed_urls or norm in seen:
                    continue
                if norm in {normalize_url_deep(mi["url"]) for mi in merged_items}:
                    continue
                merged_items.append({
                    "url": entry["url"], "title": entry["title"],
                    "category_name": "",
                    "nlm_summary": entry["summary"],
                    "nlm_tldr": entry.get("tldr", ""),
                    "nlm_body": entry.get("body", ""),
                    "significance": entry["significance"],
                    "source_type": "url", "origin": "nlm",
                })
                matched_urls.add(norm)
                placed_urls.add(norm)
                seen.add(norm)

        merged_sections.append({
            "name": section_name,
            "synthesis": synthesis,
            "items": merged_items,
        })

    return {
        "headline": parsed_nlm["headline"],
        "sections": merged_sections,
        "takeaways": parsed_nlm["takeaways"],
    }


def generate_magazine_html(merged_data, target_date):
    """Generate magazine-style HTML from merged NLM + feed data.

    Args:
        merged_data: Output from merge_nlm_with_feed()
        target_date: Date string YYYY-MM-DD

    Returns:
        Complete HTML string ready for PDF rendering.
    """
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    weekday = dt.strftime("%A")
    month = dt.strftime("%B")
    day = dt.day
    year = dt.year

    total_items = sum(len(s["items"]) for s in merged_data["sections"])

    # Section color themes
    section_themes = {
        "AI Research": {"bg": "#e8f0fe", "accent": "#1a56db", "icon": "&#x1F52C;"},
        "Tech Industry": {"bg": "#fef3e2", "accent": "#b45309", "icon": "&#x1F4C8;"},
        "Tech": {"bg": "#fef3e2", "accent": "#b45309", "icon": "&#x1F4C8;"},
        "Development": {"bg": "#e6f9ed", "accent": "#15803d", "icon": "&#x1F6E0;"},
        "News": {"bg": "#f3e8ff", "accent": "#7c3aed", "icon": "&#x1F4F0;"},
    }

    parts = []

    # Header
    parts.append(f"""<header class="mag-header">
<div class="mag-date">{_safe_text(weekday)}, {_safe_text(month)} {day}, {year}</div>
<h1 class="mag-title">Daily Tech Feed</h1>
<div class="mag-subtitle">{total_items} items curated from newsletters, X, and manual sources</div>
</header>""")

    # Headline
    if merged_data["headline"]:
        headline_clean = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", _safe_text(merged_data["headline"]))
        parts.append(f'<div class="mag-headline">{headline_clean}</div>')

    # Sections
    for section in merged_data["sections"]:
        theme = section_themes.get(section["name"], {"bg": "#f3f4f6", "accent": "#374151", "icon": ""})
        item_count = len(section["items"])

        parts.append(f"""<section class="mag-section" style="--section-accent: {theme['accent']}; --section-bg: {theme['bg']};">
<div class="mag-section-header">
<h2>{_safe_text(section['name'])}</h2>
<span class="mag-item-count">{item_count} items</span>
</div>""")

        # Section synthesis
        if section["synthesis"]:
            synth_html = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", _safe_text(section["synthesis"]))
            parts.append(f'<div class="mag-synthesis">{synth_html}</div>')

        # Check if any items have substantial article content (new format)
        # Demote thin articles (< 300 chars body) to reference list
        MIN_ARTICLE_BODY = 300
        article_items = [
            i for i in section["items"]
            if i.get("nlm_body") and len(i.get("nlm_body", "")) >= MIN_ARTICLE_BODY
        ]

        if article_items:
            # Article-first layout: full write-ups + reference list
            for item in article_items:
                parts.append(_render_article(item))

            # Split remaining items: briefs (have NLM content but too short) vs bare refs
            brief_items = [
                i for i in section["items"]
                if i.get("nlm_body") and len(i.get("nlm_body", "")) < MIN_ARTICLE_BODY
            ]
            reference_items = [
                i for i in section["items"]
                if not i.get("nlm_body")
            ]

            # Briefs section — title + TL;DR for thin articles
            if brief_items:
                parts.append('<div class="mag-briefs">')
                parts.append(f'<div class="mag-briefs-label">Briefs</div>')
                for item in brief_items:
                    title = item["title"]
                    url = item["url"]
                    tldr = item.get("nlm_tldr", "") or item.get("nlm_body", "")
                    tldr = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", _safe_text(tldr))
                    link = _safe_link(url, title)
                    parts.append(f'<div class="mag-brief-item">')
                    parts.append(f'<div class="mag-brief-title">{link}</div>')
                    parts.append(f'<div class="mag-brief-text">{tldr}</div>')
                    parts.append(f'</div>')
                parts.append("</div>")

            # Bare reference list — items with no NLM coverage at all
            MAX_REFS = 20
            if reference_items:
                parts.append('<div class="mag-references">')
                parts.append(f'<div class="mag-ref-label">Additional Sources ({len(reference_items)})</div>')
                for item in reference_items[:MAX_REFS]:
                    title = item["title"]
                    url = item["url"]
                    domain = ""
                    if url:
                        dm = re.search(r"https?://(?:www\.)?([^/]+)", url)
                        if dm:
                            domain = dm.group(1)
                    link = _safe_link(url, title)
                    domain_span = f' <span class="mag-ref-domain">{_safe_text(domain)}</span>' if domain else ""
                    parts.append(f'<div class="mag-ref-item">{link}{domain_span}</div>')
                if len(reference_items) > MAX_REFS:
                    parts.append(f'<div class="mag-ref-item mag-ref-more">+ {len(reference_items) - MAX_REFS} more</div>')
                parts.append("</div>")
        else:
            # Card layout fallback (old v2 format without articles)
            featured = [i for i in section["items"] if i["significance"] == "high"]
            standard = [i for i in section["items"] if i["significance"] == "medium"]
            compact = [i for i in section["items"] if i["significance"] == "low"]

            if featured:
                parts.append('<div class="mag-featured-grid">')
                for item in featured:
                    parts.append(_render_card(item, "featured"))
                parts.append("</div>")

            if standard:
                parts.append('<div class="mag-standard-grid">')
                for item in standard:
                    parts.append(_render_card(item, "standard"))
                parts.append("</div>")

            MAX_COMPACT = 20
            if compact:
                parts.append('<div class="mag-compact-list">')
                for item in compact[:MAX_COMPACT]:
                    title = item["title"]
                    url = item["url"]
                    link = _safe_link(url, title)
                    parts.append(f'<div class="mag-compact-item">{link}</div>')
                if len(compact) > MAX_COMPACT:
                    parts.append(f'<div class="mag-compact-item mag-compact-more">+ {len(compact) - MAX_COMPACT} more items</div>')
                parts.append("</div>")

        parts.append("</section>")

    # Takeaways
    if merged_data["takeaways"]:
        parts.append('<section class="mag-takeaways">')
        parts.append('<h2>Key Takeaways</h2>')
        parts.append("<ol>")
        for tw in merged_data["takeaways"]:
            tw_html = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", _safe_text(tw))
            parts.append(f"<li>{tw_html}</li>")
        parts.append("</ol>")
        parts.append("</section>")

    # Footer
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts.append(f'<footer class="mag-footer">Generated {timestamp}</footer>')

    return "\n".join(parts)


def _render_card(item, card_type):
    """Render a single item as an HTML card."""
    title = item["title"]
    url = item["url"]
    summary = item.get("nlm_summary", "")
    category = item.get("category_name", "")

    # Clean up bold markdown in summary
    if summary:
        summary = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", _safe_text(summary))

    # Skip summary if it's too similar to the title (NLM-only items)
    if summary and _text_similar(title, summary):
        summary = ""

    # Domain for display
    domain = ""
    if url:
        domain_match = re.search(r"https?://(?:www\.)?([^/]+)", url)
        if domain_match:
            domain = domain_match.group(1)

    css_class = f"mag-card mag-card-{card_type}"

    parts = [f'<div class="{css_class}">']
    if category and category.lower() != "general":
        parts.append(f'<div class="mag-card-tag">{_safe_text(category)}</div>')
    # Title is the link
    parts.append(f'<div class="mag-card-title">{_safe_link(url, title)}</div>')
    if summary:
        parts.append(f'<div class="mag-card-summary">{summary}</div>')
    if domain:
        parts.append(f'<div class="mag-card-source">{_safe_text(domain)}</div>')
    parts.append("</div>")

    return "\n".join(parts)


def _render_article(item):
    """Render a single item as a full article with TL;DR and body content."""
    title = item.get("title", "") or item.get("nlm_summary", "Untitled")
    url = item.get("url", "")
    tldr = item.get("nlm_tldr", "")
    body = item.get("nlm_body", "")

    # Clean bold markdown
    title = re.sub(r"\*\*([^*]+)\*\*", r"\1", title)

    # Domain for display
    domain = ""
    if url:
        dm = re.search(r"https?://(?:www\.)?([^/]+)", url)
        if dm:
            domain = dm.group(1)

    parts = ['<article class="mag-article">']

    # Title (linked) + source domain
    parts.append(f'<h3 class="mag-article-title">{_safe_link(url, title)}</h3>')
    if domain:
        parts.append(f'<div class="mag-article-source">{_safe_text(domain)}</div>')

    # TL;DR callout — skip if redundant with body (> 40% of body length)
    if tldr and body and len(tldr) < len(body) * 0.4:
        tldr_html = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", _safe_text(tldr))
        parts.append(f'<div class="mag-article-tldr"><strong>TL;DR:</strong> {tldr_html}</div>')

    # Body content — paragraphs and lists
    if body:
        body_html = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", _safe_text(body))
        paragraphs = [p.strip() for p in body_html.split("\n\n") if p.strip()]
        for p in paragraphs:
            lines = [l for l in p.split("\n") if l.strip()]
            # Check if all lines are list items
            is_list = len(lines) > 1 and all(
                re.match(r"^\d+[\.\)]\s|^[\-\*]\s", l.strip())
                for l in lines
            )
            if is_list:
                parts.append('<ul class="mag-article-list">')
                for line in lines:
                    li = re.sub(r"^\d+[\.\)]\s*|^[\-\*]\s*", "", line.strip())
                    parts.append(f"<li>{li}</li>")
                parts.append("</ul>")
            else:
                text = " ".join(l.strip() for l in lines)
                parts.append(f'<p class="mag-article-text">{text}</p>')

    parts.append("</article>")
    return "\n".join(parts)


def _text_similar(a, b):
    """Check if two strings are substantially similar (for dedup).

    Only suppresses summary when title and summary are nearly identical
    (not when title is just a prefix of a useful summary).
    """
    a_clean = re.sub(r"<[^>]+>", "", re.sub(r"\*\*([^*]+)\*\*", r"\1", a)).strip().lower()
    b_clean = re.sub(r"<[^>]+>", "", re.sub(r"\*\*([^*]+)\*\*", r"\1", b)).strip().lower()
    if not a_clean or not b_clean:
        return False
    # Only suppress if strings are very close in length AND overlap heavily
    shorter = a_clean if len(a_clean) < len(b_clean) else b_clean
    longer = b_clean if len(a_clean) < len(b_clean) else a_clean
    # If the summary is significantly longer, it has new info — don't suppress
    if len(longer) > len(shorter) * 1.5:
        return False
    return shorter in longer


def _write_plain_html(briefing_md, html_path, target_date):
    """Write a plain HTML file wrapping the markdown briefing."""
    from pathlib import Path as _P
    css_file = _P(__file__).parent / "templates" / "magazine-responsive.css"
    css = css_file.read_text(encoding="utf-8") if css_file.exists() else ""
    # Convert markdown bold to HTML
    body = _safe_text(briefing_md)
    body = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", body)
    body = body.replace("\n\n", "</p><p>").replace("\n", "<br>")
    body = f"<p>{body}</p>"
    html = (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light dark">\n'
        f"<title>Daily Feed \u2014 {_safe_text(target_date)}</title>\n"
        f"<style>{css}</style>\n"
        "</head><body>\n"
        f'<header class="mag-header"><h1 class="mag-title">Daily Tech Feed</h1>'
        f'<div class="mag-date">{_safe_text(target_date)}</div></header>\n'
        f'<div class="mag-section-body">{body}</div>\n'
        "</body></html>\n"
    )
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")
    print(f"Plain HTML generated: {html_path}")


def main():
    parser = argparse.ArgumentParser(description="Daily Tech Feed Generator")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving or sending")
    parser.add_argument("--date", type=str, help="Target date (YYYY-MM-DD, default: today)")
    parser.add_argument("--markdown-only", action="store_true", help="Generate markdown only")
    parser.add_argument("--pdf", action="store_true", help="Also produce PDF alongside HTML")
    parser.add_argument("--pdf-only", action="store_true", help="(Deprecated) Alias for --pdf")
    parser.add_argument("--magazine", action="store_true", help="(Deprecated) HTML is always magazine layout")
    args = parser.parse_args()

    run(
        target_date=args.date,
        dry_run=args.dry_run,
        markdown_only=args.markdown_only,
        pdf_only=args.pdf_only,
        pdf=args.pdf or args.pdf_only,
        magazine=args.magazine,
    )


if __name__ == "__main__":
    main()
