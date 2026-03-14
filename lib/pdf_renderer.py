#!/usr/bin/env python3
"""
PDF renderer for OpenClaw Research Automation System.

Converts markdown text to a styled PDF using markdown-it-py and WeasyPrint.
Auto-generates a clickable table of contents from h2/h3 headings.

Usage:
    python pdf_renderer.py --input briefing.md --output report.pdf
    python pdf_renderer.py --input briefing.md  # outputs to briefing.pdf
    python pdf_renderer.py --input report.md --css templates/research-report.css

Requires:
    - markdown-it-py, weasyprint (installed in .venv)
    - System: pango, glib (brew install pango)
    - DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib (macOS with uv-managed Python)
"""

import argparse
import os
import re
import sys
from pathlib import Path

# Ensure Homebrew libraries are discoverable on macOS
if sys.platform == "darwin" and "/opt/homebrew/lib" not in os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", ""):
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (
        os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "") + ":/opt/homebrew/lib"
    ).lstrip(":")

from markdown_it import MarkdownIt
from weasyprint import HTML

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_CSS = PROJECT_ROOT / "templates" / "daily-feed.css"
REPORT_CSS = PROJECT_ROOT / "templates" / "research-report.css"

# Domain → pill color for visual anchors in magazine PDF
DOMAIN_COLORS = {
    "arxiv.org": "#dc2626",
    "openreview.net": "#dc2626",
    "proceedings.mlr.press": "#dc2626",
    "aclanthology.org": "#dc2626",
    "biorxiv.org": "#dc2626",
    "github.com": "#16a34a",
    "gitlab.com": "#16a34a",
    "dev.to": "#16a34a",
    "hashnode.dev": "#16a34a",
    "openai.com": "#2563eb",
    "anthropic.com": "#2563eb",
    "deepmind.google": "#2563eb",
    "ai.meta.com": "#2563eb",
    "google.com": "#2563eb",
    "youtube.com": "#dc2626",
    "youtu.be": "#dc2626",
    "substack.com": "#ca8a04",
    "beehiiv.com": "#ca8a04",
    "huggingface.co": "#d97706",
}
DOMAIN_COLOR_DEFAULT = "#6b7280"

# Content type badge colors
BADGE_COLORS = {
    "research": {"bg": "#dbeafe", "text": "#1e40af"},
    "news": {"bg": "#fce7f3", "text": "#9d174d"},
    "blog": {"bg": "#dcfce7", "text": "#166534"},
    "tool": {"bg": "#e0e7ff", "text": "#3730a3"},
}


def domain_pill_color(url: str) -> str:
    """Return the pill background color for a URL's domain."""
    match = re.search(r"https?://(?:www\.)?(?:blog\.)?(?:ai\.)?([^/]+)", url)
    if not match:
        return DOMAIN_COLOR_DEFAULT
    domain = match.group(1).lower()
    # Direct lookup
    if domain in DOMAIN_COLORS:
        return DOMAIN_COLORS[domain]
    # Try parent domain (e.g., blog.openai.com → openai.com)
    parts = domain.split(".")
    if len(parts) > 2:
        parent = ".".join(parts[-2:])
        if parent in DOMAIN_COLORS:
            return DOMAIN_COLORS[parent]
    return DOMAIN_COLOR_DEFAULT


def _extract_domain(url: str) -> str:
    """Extract display domain from URL."""
    match = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return match.group(1) if match else ""


def markdown_to_html(markdown_text: str) -> str:
    """Convert markdown to HTML using markdown-it-py."""
    md = MarkdownIt("commonmark", {"html": True, "typographer": True})
    body = md.render(markdown_text)
    return body


def slugify(text: str) -> str:
    """Convert heading text to a URL-safe slug."""
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_]+", "-", text).strip("-")


def inject_heading_ids(html: str) -> tuple[str, list[dict]]:
    """Add id attributes to h2/h3 tags and collect them for the TOC.

    Returns:
        Tuple of (modified HTML, list of {level, id, text} dicts).
    """
    headings = []
    seen_slugs = {}

    def replace_heading(match):
        tag = match.group(1)  # "h2" or "h3"
        content = match.group(2)
        # Strip HTML tags from content for the TOC text
        text = re.sub(r"<[^>]+>", "", content).strip()
        slug = slugify(text)

        # Deduplicate slugs
        if slug in seen_slugs:
            seen_slugs[slug] += 1
            slug = f"{slug}-{seen_slugs[slug]}"
        else:
            seen_slugs[slug] = 0

        level = int(tag[1])
        headings.append({"level": level, "id": slug, "text": text})
        return f'<{tag} id="{slug}">{content}</{tag}>'

    modified = re.sub(
        r"<(h[23])>(.*?)</\1>",
        replace_heading,
        html,
        flags=re.DOTALL,
    )
    return modified, headings


def extract_heading_descriptions(html: str, headings: list[dict]) -> dict[str, str]:
    """Extract a one-line description for each heading from the first <p> after it.

    Scans the HTML for each heading ID, finds the next <p> tag, and extracts
    the first sentence (up to 140 chars) as a description.

    Returns:
        Dict mapping heading ID to description string.
    """
    descriptions: dict[str, str] = {}
    for h in headings:
        if h["level"] != 2:
            continue
        # Find the heading tag, then the next <p>...</p>
        pattern = re.compile(
            rf'<h2 id="{re.escape(h["id"])}">[^<]*</h2>\s*(?:<blockquote>.*?</blockquote>\s*)?<p>(.*?)</p>',
            re.DOTALL,
        )
        match = pattern.search(html)
        if not match:
            continue
        # Strip HTML tags and extract first sentence
        raw = re.sub(r"<[^>]+>", "", match.group(1)).strip()
        # First sentence: up to the first period followed by a space, or end
        sentence_match = re.match(r"(.+?[.!?])(?:\s|$)", raw)
        if sentence_match:
            desc = sentence_match.group(1)
        else:
            desc = raw
        # Truncate to 140 chars
        if len(desc) > 140:
            desc = desc[:137] + "..."
        descriptions[h["id"]] = desc
    return descriptions


def build_toc_html(headings: list[dict], descriptions: dict[str, str] | None = None) -> str:
    """Build a clickable table of contents from collected headings.

    Args:
        headings: List of {level, id, text} dicts from inject_heading_ids.
        descriptions: Optional dict mapping heading ID to description text.
    """
    if not headings:
        return ""

    descriptions = descriptions or {}
    lines = ['<nav class="toc">', '<h2 class="toc-title">Contents</h2>', "<ul>"]
    for h in headings:
        indent = "  " if h["level"] == 3 else ""
        css_class = "toc-h3" if h["level"] == 3 else "toc-h2"
        desc_html = ""
        if h["level"] == 2 and h["id"] in descriptions:
            desc = descriptions[h["id"]]
            desc_html = f'\n{indent}  <span class="toc-desc">{desc}</span>'
        lines.append(
            f'{indent}<li class="{css_class}">'
            f'<a href="#{h["id"]}">{h["text"]}</a>{desc_html}</li>'
        )
    lines.append("</ul>")
    lines.append("</nav>")
    return "\n".join(lines)


def style_report_tldr(html: str) -> str:
    """Find the first blockquote containing 'TL;DR' and add class='report-tldr'.

    This enables distinct green styling for the report-level TL;DR vs blue
    for regular section blockquotes.
    """
    # Match the first <blockquote> whose content contains TL;DR (bold or plain)
    pattern = re.compile(
        r"<blockquote>\s*<p>\s*<strong>TL;DR</strong>",
        re.IGNORECASE,
    )
    match = pattern.search(html)
    if match:
        # Replace only the first occurrence
        html = html[:match.start()] + '<blockquote class="report-tldr">' + html[match.start() + len("<blockquote>"):]
    return html


def render_html_pdf(html_body: str, output_path: str, css_path: str | None = None) -> str:
    """Render pre-built HTML body to a styled PDF (no markdown conversion).

    Used by the magazine-style daily feed generator which produces HTML directly
    via generate_magazine_html().

    Args:
        html_body: The HTML body content (no <html>/<head> wrapper needed).
        output_path: Path for the output PDF file.
        css_path: Optional path to a CSS stylesheet. Defaults to daily-feed.css.

    Returns:
        The absolute path to the generated PDF.
    """
    if css_path:
        css_file = Path(css_path)
    else:
        css_file = DEFAULT_CSS

    css_content = css_file.read_text() if css_file.exists() else ""

    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
{css_content}
</style>
</head>
<body>
{html_body}
</body>
</html>"""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    HTML(string=full_html).write_pdf(str(output))
    return str(output.resolve())


def render_pdf(markdown_text: str, output_path: str, css_path: str | None = None, skip_toc: bool = False) -> str:
    """Render markdown to a styled PDF with auto-generated TOC.

    Args:
        markdown_text: The markdown content to render.
        output_path: Path for the output PDF file.
        css_path: Optional path to a CSS stylesheet. Auto-selects based on
                  output path: daily-feed.css for daily feeds, research-report.css
                  for reports.
        skip_toc: If True, skip TOC generation (useful for daily feeds).

    Returns:
        The absolute path to the generated PDF.
    """
    # Auto-select CSS based on context
    if css_path:
        css_file = Path(css_path)
    elif "daily-feed" in str(output_path) and DEFAULT_CSS.exists():
        css_file = DEFAULT_CSS
    elif REPORT_CSS.exists():
        css_file = REPORT_CSS
    else:
        css_file = DEFAULT_CSS

    css_content = css_file.read_text() if css_file.exists() else ""

    html_body = markdown_to_html(markdown_text)

    # Style the report-level TL;DR blockquote (before heading injection)
    html_body = style_report_tldr(html_body)

    # Inject heading IDs and build TOC with descriptions
    html_body, headings = inject_heading_ids(html_body)

    if not skip_toc:
        descriptions = extract_heading_descriptions(html_body, headings)
        toc_html = build_toc_html(headings, descriptions)

        # Insert TOC after h1 + TL;DR blockquote (not between them)
        # Visual order: Title -> TL;DR -> TOC -> Content
        if toc_html:
            h1_end = re.search(r"</h1>", html_body)
            if h1_end:
                insert_pos = h1_end.end()
                # Check if a report-tldr blockquote follows the h1
                after_h1 = html_body[insert_pos:]
                tldr_match = re.match(r"\s*<blockquote class=\"report-tldr\">.*?</blockquote>", after_h1, re.DOTALL)
                if tldr_match:
                    insert_pos += tldr_match.end()
                html_body = html_body[:insert_pos] + "\n" + toc_html + "\n" + html_body[insert_pos:]
            else:
                html_body = toc_html + "\n" + html_body

    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
{css_content}
</style>
</head>
<body>
{html_body}
</body>
</html>"""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    HTML(string=full_html).write_pdf(str(output))
    return str(output.resolve())


SECTION_THEMES = {
    "AI Research": {"bg": "#e8f0fe", "accent": "#1a56db"},
    "Tech": {"bg": "#fef3e2", "accent": "#b45309"},
    "Development": {"bg": "#e6f9ed", "accent": "#15803d"},
    "News": {"bg": "#f3e8ff", "accent": "#7c3aed"},
}


def _sig_bars_html(significance: str) -> str:
    """Render 3 significance bars as CSS-only inline HTML."""
    filled = {"high": 3, "medium": 2, "low": 1}.get(significance, 2)
    bars = []
    for i in range(3):
        cls = "mag-v2-sig-bar--filled" if i < filled else "mag-v2-sig-bar--empty"
        bars.append(f'<span class="mag-v2-sig-bar {cls}"></span>')
    return f'<span class="mag-v2-sig-bars">{"".join(bars)}</span>'


def _render_front_page(magazine: dict) -> str:
    """Render the executive front page.

    Returns empty string if editorial_brief is absent (graceful degradation).
    """
    from datetime import datetime as dt

    editorial_brief = magazine.get("editorial_brief", "")
    if not editorial_brief:
        return ""

    date_str = magazine.get("date", "")
    try:
        d = dt.strptime(date_str, "%Y-%m-%d")
        date_display = d.strftime("%A, %B %-d, %Y").upper()
    except (ValueError, AttributeError):
        date_display = date_str.upper()

    title = magazine.get("title", "Research Digest")
    stats = magazine.get("stats", {})
    total = stats.get("total", 0)
    subtitle = (
        f"{total} items — "
        f"{stats.get('research', 0)} research, "
        f"{stats.get('news', 0)} news, "
        f"{stats.get('blog', 0)} blog"
    )

    parts = ['<div class="mag-v2-front-page">']

    # Header banner
    parts.append(f"""<header class="mag-header">
<div class="mag-date">{date_display}</div>
<h1 class="mag-title">{title}</h1>
<div class="mag-subtitle">{subtitle}</div>
</header>""")

    # Editorial brief
    parts.append(f'<div class="mag-v2-editorial"><p>{editorial_brief}</p></div>')

    # Top stories
    top_stories = magazine.get("top_stories", [])
    if top_stories:
        parts.append('<div class="mag-v2-top-stories"><h2>Top Stories</h2>')
        for story in top_stories:
            url = story.get("url", "")
            story_title = story.get("title", "")
            summary = story.get("summary", "")
            pill_color = domain_pill_color(url) if url else DOMAIN_COLOR_DEFAULT
            domain = _extract_domain(url)

            # First sentence of summary only
            first_sentence = ""
            if summary:
                clean = re.sub(r"\*\*([^*]+)\*\*", r"\1", summary)
                m = re.match(r"(.+?[.!?])(?:\s|$)", clean, re.DOTALL)
                first_sentence = m.group(1) if m else clean[:120]

            pill_html = f'<span class="mag-v2-pill" style="background:{pill_color}">{domain}</span> ' if domain else ""
            title_html = f'<a href="{url}">{story_title}</a>' if url else story_title
            summary_html = f'<div class="mag-v2-top-story-summary">{first_sentence}</div>' if first_sentence else ""

            parts.append(f'<div class="mag-v2-top-story">{pill_html}{title_html}{summary_html}</div>')
        parts.append("</div>")

    # Section navigator
    sections = magazine.get("sections", [])
    if sections:
        parts.append('<nav class="mag-v2-navigator"><h2>In This Issue</h2>')
        for section in sections:
            theme = SECTION_THEMES.get(section["name"], {"accent": "#374151", "bg": "#f3f4f6"})
            count = len(section.get("items", []))
            preview = section.get("preview", "")
            preview_html = f' <span class="mag-v2-nav-preview">— {preview}</span>' if preview else ""
            parts.append(
                f'<div class="mag-v2-nav-item">'
                f'<span class="mag-v2-nav-dot" style="background:{theme["accent"]}"></span>'
                f'<strong>{section["name"]}</strong> '
                f'<span class="mag-v2-nav-count">({count})</span>'
                f'{preview_html}'
                f'</div>'
            )
        parts.append("</nav>")

    parts.append("</div>")  # close mag-v2-front-page
    return "\n".join(parts)


def render_magazine_pdf(magazine: dict, output_path: str, css_path: str | None = None) -> str:
    """Render magazine JSON (from magazine_generator.py) to a styled PDF.

    Args:
        magazine: Magazine dict with title, date, sections, stats, and optional
                  editorial_brief, top_stories fields.
        output_path: Path for the output PDF file.
        css_path: Optional CSS path. Defaults to daily-feed.css.

    Returns:
        The absolute path to the generated PDF.
    """
    from datetime import datetime as dt

    date_str = magazine.get("date", "")
    try:
        d = dt.strptime(date_str, "%Y-%m-%d")
        date_display = d.strftime("%A, %B %-d, %Y").upper()
    except (ValueError, AttributeError):
        date_display = date_str.upper()

    title = magazine.get("title", "Research Digest")
    stats = magazine.get("stats", {})
    total = stats.get("total", 0)
    subtitle = (
        f"{total} items — "
        f"{stats.get('research', 0)} research, "
        f"{stats.get('news', 0)} news, "
        f"{stats.get('blog', 0)} blog"
    )

    parts = []

    # Front page (only when editorial_brief is present)
    front_page = _render_front_page(magazine)
    if front_page:
        parts.append(front_page)
    else:
        # No editorial brief — render inline header
        parts.append(f"""<header class="mag-header">
<div class="mag-date">{date_display}</div>
<h1 class="mag-title">{title}</h1>
<div class="mag-subtitle">{subtitle}</div>
</header>""")

    # Sections
    for section in magazine.get("sections", []):
        section_name = section.get("name", "")
        theme = SECTION_THEMES.get(section_name, {"bg": "#f3f4f6", "accent": "#374151"})
        item_count = len(section.get("items", []))

        # Section header with string-set for running page header
        parts.append(
            f'<section class="mag-section" '
            f'style="--section-accent: {theme["accent"]}; --section-bg: {theme["bg"]};">'
        )
        parts.append(
            f'<div class="mag-section-header" '
            f'style="string-set: section-name \'{section_name}\';">'
            f'<h2 style="color: var(--section-accent)">{section_name}</h2>'
            f'<span class="mag-item-count">{item_count} items</span>'
            f'</div>'
        )

        # Section introduction (from OpenAI)
        if section.get("introduction"):
            intro_html = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", section["introduction"])
            parts.append(f'<div class="mag-synthesis">{intro_html}</div>')

        # Group items by type
        featured = [i for i in section.get("items", []) if i.get("type") == "featured"]
        standard = [i for i in section.get("items", []) if i.get("type") == "standard"]
        briefs = [i for i in section.get("items", []) if i.get("type") == "brief"]

        # Featured items — full-width cards with pill + badge + significance bars
        if featured:
            parts.append('<div class="mag-featured-grid">')
            for item in featured:
                parts.append(_render_magazine_card(item, "featured", section_name))
            parts.append("</div>")

        # Standard items — two-column grid
        if standard:
            parts.append('<div class="mag-standard-grid">')
            for item in standard:
                parts.append(_render_magazine_card(item, "standard", section_name))
            parts.append("</div>")

        # Brief/reference items — compact list
        MAX_REFS = 30
        if briefs:
            has_summaries = any(i.get("summary") for i in briefs)
            if has_summaries:
                parts.append('<div class="mag-briefs">')
                parts.append('<div class="mag-briefs-label">Briefs</div>')
                for item in briefs[:MAX_REFS]:
                    url = item.get("url", "")
                    brief_title = item.get("title", "")
                    summary = item.get("summary", "")
                    link = f'<a href="{url}">{brief_title}</a>' if url else brief_title
                    domain = _extract_domain(url)
                    pill_color = domain_pill_color(url) if url else DOMAIN_COLOR_DEFAULT
                    pill_html = f'<span class="mag-v2-pill" style="background:{pill_color}">{domain}</span> ' if domain else ""
                    parts.append('<div class="mag-brief-item">')
                    parts.append(f'<div class="mag-brief-title">{pill_html}{link}</div>')
                    if summary:
                        summary_html = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", summary)
                        parts.append(f'<div class="mag-brief-text">{summary_html}</div>')
                    parts.append('</div>')
                if len(briefs) > MAX_REFS:
                    parts.append(f'<div class="mag-ref-item mag-ref-more">+ {len(briefs) - MAX_REFS} more</div>')
                parts.append("</div>")
            else:
                # No summaries — compact reference list with domain pills
                parts.append('<div class="mag-references">')
                parts.append(f'<div class="mag-ref-label">Additional Sources ({len(briefs)})</div>')
                for item in briefs[:MAX_REFS]:
                    url = item.get("url", "")
                    ref_title = item.get("title", "")
                    domain = _extract_domain(url)
                    pill_color = domain_pill_color(url) if url else DOMAIN_COLOR_DEFAULT
                    link = f'<a href="{url}">{ref_title}</a>' if url else ref_title
                    pill_html = f'<span class="mag-v2-pill" style="background:{pill_color}">{domain}</span> ' if domain else ""
                    parts.append(f'<div class="mag-ref-item">{pill_html}{link}</div>')
                if len(briefs) > MAX_REFS:
                    parts.append(f'<div class="mag-ref-item mag-ref-more">+ {len(briefs) - MAX_REFS} more</div>')
                parts.append("</div>")

        parts.append("</section>")

    # Footer
    gen_at = magazine.get("generated_at", "")
    parts.append(f'<footer class="mag-footer">Generated {gen_at}</footer>')

    html_body = "\n".join(parts)
    return render_html_pdf(html_body, output_path, css_path=css_path)


def _render_magazine_card(item: dict, card_type: str, section_name: str = "") -> str:
    """Render a magazine item as a semantic article card with visual anchors."""
    title = item.get("title", "")
    url = item.get("url", "")
    summary = item.get("summary", "")
    content_type = item.get("content_type", "news")
    significance = item.get("significance", "medium")
    item_id = item.get("id", "")

    if summary:
        summary = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", summary)

    domain = _extract_domain(url)
    pill_color = domain_pill_color(url) if url else DOMAIN_COLOR_DEFAULT

    # Domain pill
    pill_html = f'<span class="mag-v2-pill" style="background:{pill_color}">{domain}</span>' if domain else ""

    # Content type badge
    badge_colors = BADGE_COLORS.get(content_type, {"bg": "#f3f4f6", "text": "#374151"})
    badge_html = (
        f'<span class="mag-v2-badge mag-v2-badge--{content_type}" '
        f'style="background:{badge_colors["bg"]}; color:{badge_colors["text"]}">'
        f'{content_type.upper()}</span>'
    )

    # Significance bars (featured and standard only)
    sig_html = _sig_bars_html(significance)

    # Meta row
    meta_html = f'<div class="mag-card-meta">{pill_html}{badge_html}{sig_html}</div>'

    # Title
    title_html = f'<a href="{url}">{title}</a>' if url else title

    # Build article element with data attributes
    data_attrs = (
        f'data-article-id="{item_id}" '
        f'data-url="{url}" '
        f'data-section="{section_name}" '
        f'data-content-type="{content_type}"'
    )
    card_class = f"mag-card mag-card-{card_type}"

    lines = [f'<article class="{card_class}" {data_attrs}>']
    lines.append(meta_html)
    lines.append(f'<div class="mag-card-title">{title_html}</div>')
    if summary:
        lines.append(f'<div class="mag-card-summary">{summary}</div>')
    lines.append("</article>")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Render markdown to styled PDF")
    parser.add_argument("--input", required=True, help="Input markdown file")
    parser.add_argument("--output", help="Output PDF file (default: same name as input with .pdf)")
    parser.add_argument("--css", help="Custom CSS stylesheet path")
    parser.add_argument("--skip-toc", action="store_true", help="Skip table of contents generation")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}")
        sys.exit(1)

    output_path = args.output or str(input_path.with_suffix(".pdf"))
    markdown_text = input_path.read_text()

    result = render_pdf(markdown_text, output_path, css_path=args.css, skip_toc=args.skip_toc)
    print(f"PDF generated: {result}")


if __name__ == "__main__":
    main()
