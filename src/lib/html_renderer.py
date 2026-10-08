"""Self-contained responsive HTML renderer for magazine digests.

Produces a single .html file with inlined CSS that adapts to phone,
tablet, and desktop. Uses <details>/<summary> for collapsible sections
and prefers-color-scheme for dark mode — no JavaScript required.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from lib.pdf_renderer import (
    _render_front_page,
    _render_magazine_card,
    _safe_link,
    _safe_text,
    _safe_attr,
    _extract_domain,
    domain_pill_color,
    SECTION_THEMES,
    DOMAIN_COLOR_DEFAULT,
)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"

# Map section names to data-section slugs for CSS theming
_SECTION_SLUG = {
    "AI Research": "research",
    "Tech": "tech",
    "Development": "development",
    "News": "news",
}


def render_magazine_html(
    magazine: dict[str, Any],
    output_path: Path | str,
    css_path: Path | str | None = None,
) -> Path:
    """Render magazine JSON to a self-contained responsive HTML file.

    Args:
        magazine: Magazine dict with title, date, sections, editorial_brief, etc.
        output_path: Where to write the .html file.
        css_path: Override CSS file (default: templates/magazine-responsive.css).

    Returns:
        Path to the written .html file.
    """
    css_file = Path(css_path) if css_path else TEMPLATES_DIR / "magazine-responsive.css"
    out = Path(output_path)

    html_body = _build_html_body(magazine)
    css = css_file.read_text(encoding="utf-8")

    title = _safe_text(magazine.get("title", "Research Digest"))

    full_html = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '  <meta name="color-scheme" content="light dark">\n'
        f"  <title>{title}</title>\n"
        f"  <style>\n{css}\n  </style>\n"
        "</head>\n<body>\n"
        f"{html_body}\n"
        "</body>\n</html>\n"
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(full_html, encoding="utf-8")
    return out


def _build_html_body(magazine: dict[str, Any]) -> str:
    """Build the full HTML body from a magazine dict."""
    from datetime import datetime as dt

    parts: list[str] = []

    # ── Header ──
    date_str = magazine.get("date", "")
    try:
        d = dt.strptime(date_str, "%Y-%m-%d")
        date_display = d.strftime("%A, %B %-d, %Y")
    except (ValueError, AttributeError):
        date_display = date_str

    title = magazine.get("title", "Research Digest")
    stats = magazine.get("stats", {})
    total = stats.get("total", 0)
    subtitle = (
        f"{total} items \u2014 "
        f"{stats.get('research', 0)} research, "
        f"{stats.get('news', 0)} news, "
        f"{stats.get('blog', 0)} blog"
    )

    # Front page (editorial brief + top stories + section navigator)
    front_page = _render_front_page(magazine)
    if front_page:
        parts.append(front_page)
    else:
        parts.append(
            f'<header class="mag-header">'
            f'<div class="mag-date">{_safe_text(date_display)}</div>'
            f'<h1 class="mag-title">{_safe_text(title)}</h1>'
            f'<div class="mag-subtitle">{_safe_text(subtitle)}</div>'
            f"</header>"
        )

    # ── Sticky nav bar ──
    sections = magazine.get("sections", [])
    parts.append(_render_nav(sections))

    # ── Sections as <details>/<summary> ──
    for i, section in enumerate(sections):
        is_first = i == 0
        parts.append(_render_section(section, open_by_default=is_first))

    return "\n".join(parts)


def _render_nav(sections: list[dict[str, Any]]) -> str:
    """Render the sticky section navigation bar."""
    if not sections:
        return ""

    tabs: list[str] = []
    for section in sections:
        name = section.get("name", "")
        slug = _SECTION_SLUG.get(name, name.lower().replace(" ", "-"))
        count = len(section.get("items", []))
        theme = SECTION_THEMES.get(name, {"accent": "#374151"})

        # Abbreviated name on mobile, full name on tablet+ (handled by CSS)
        short = slug[:3].upper() if len(name) > 6 else name
        tabs.append(
            f'<a href="#section-{_safe_attr(slug)}" class="mag-nav-tab">'
            f'<span class="mag-nav-dot" style="background:{theme["accent"]}"></span>'
            f'<span class="mag-nav-name-short">{_safe_text(short)}</span>'
            f'<span class="mag-nav-name-full">{_safe_text(name)}</span>'
            f'<span class="mag-nav-count">{count}</span>'
            f"</a>"
        )

    return (
        '<nav class="mag-nav">'
        f'<div class="mag-nav-tabs">{"".join(tabs)}</div>'
        "</nav>"
    )


def _render_section(section: dict[str, Any], open_by_default: bool = False) -> str:
    """Render a magazine section as a collapsible <details> element."""
    name = section.get("name", "")
    slug = _SECTION_SLUG.get(name, name.lower().replace(" ", "-"))
    theme = SECTION_THEMES.get(name, {"bg": "#f3f4f6", "accent": "#374151"})
    items = section.get("items", [])
    count = len(items)

    open_attr = " open" if open_by_default else ""

    parts: list[str] = []
    parts.append(
        f'<details class="mag-section" data-section="{_safe_attr(slug)}" '
        f'id="section-{_safe_attr(slug)}"{open_attr}>'
    )
    parts.append(
        f'<summary class="mag-section-header">'
        f'<span class="mag-section-dot" style="background:{theme["accent"]}"></span>'
        f'{_safe_text(name)} '
        f'<span class="mag-section-count">({count})</span>'
        f"</summary>"
    )

    parts.append('<div class="mag-section-body">')

    # Section introduction
    intro = section.get("introduction", "")
    if intro:
        intro_html = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", _safe_text(intro))
        parts.append(f'<div class="mag-synthesis">{intro_html}</div>')

    # Group items by type
    featured = [i for i in items if i.get("type") == "featured"]
    standard = [i for i in items if i.get("type") == "standard"]
    briefs = [i for i in items if i.get("type") == "brief"]

    # Featured items
    if featured:
        for item in featured:
            parts.append(_render_magazine_card(item, "featured", name))

    # Standard items — grid
    if standard:
        parts.append('<div class="mag-standard-grid">')
        for item in standard:
            parts.append(_render_magazine_card(item, "standard", name))
        parts.append("</div>")

    # Brief/reference items
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
                link = _safe_link(url, brief_title)
                domain = _extract_domain(url)
                pill_color = domain_pill_color(url) if url else DOMAIN_COLOR_DEFAULT
                pill_html = (
                    f'<span class="mag-v2-pill" style="background:{pill_color}">'
                    f'{_safe_text(domain)}</span> '
                    if domain else ""
                )
                parts.append('<div class="mag-brief-item">')
                parts.append(f'<div class="mag-brief-title">{pill_html}{link}</div>')
                if summary:
                    summary_html = re.sub(
                        r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", _safe_text(summary)
                    )
                    parts.append(f'<div class="mag-brief-text">{summary_html}</div>')
                parts.append("</div>")
            if len(briefs) > MAX_REFS:
                parts.append(f'<div class="mag-ref-more">+ {len(briefs) - MAX_REFS} more</div>')
            parts.append("</div>")
        else:
            parts.append('<div class="mag-references">')
            parts.append(
                f'<div class="mag-ref-label">Additional Sources ({len(briefs)})</div>'
            )
            for item in briefs[:MAX_REFS]:
                url = item.get("url", "")
                ref_title = item.get("title", "")
                link = _safe_link(url, ref_title)
                domain = _extract_domain(url)
                pill_color = domain_pill_color(url) if url else DOMAIN_COLOR_DEFAULT
                pill_html = (
                    f'<span class="mag-v2-pill" style="background:{pill_color}">'
                    f'{_safe_text(domain)}</span> '
                    if domain else ""
                )
                parts.append(f'<div class="mag-ref-item">{pill_html}{link}</div>')
            if len(briefs) > MAX_REFS:
                parts.append(f'<div class="mag-ref-more">+ {len(briefs) - MAX_REFS} more</div>')
            parts.append("</div>")

    parts.append("</div>")  # .mag-section-body
    parts.append("</details>")

    return "\n".join(parts)
