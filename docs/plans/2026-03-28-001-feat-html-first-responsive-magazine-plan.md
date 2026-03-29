---
title: "feat: HTML-First Responsive Magazine"
type: feat
status: active
date: 2026-03-28
origin: docs/brainstorms/2026-03-28-html-first-magazine-brainstorm.md
---

# feat: HTML-First Responsive Magazine

## Enhancement Summary

**Deepened on:** 2026-03-28
**Sections enhanced:** All major sections
**Agents used:** Architecture Strategist, Performance Oracle, Security Sentinel, Code Simplicity Reviewer, Python Reviewer, Frontend Races Reviewer, Pattern Recognition Specialist, Best Practices Researcher

### Key Improvements
1. **BLOCKING: HTML escaping** — Zero escaping exists in the codebase. All titles, URLs, and summaries from external sources are interpolated via f-strings. Must add `html.escape()` before shipping HTML output.
2. **Scope reduction (~40%)** — Cut progress bar, back-to-top button, theme toggle, and animated `<details>` from v1. CSS-only with `<details>/<summary>`, `prefers-color-scheme`, and sticky nav. Eliminates the JS file entirely in v1.
3. **Data normalization gap** — Two HTML generators consume different data shapes. Need explicit `_to_magazine_json()` adapter in daily_feed_generator.py to normalize `merged_data` into magazine JSON schema.
4. **Dark mode via CSS `filter`** — Use `filter: brightness(1.2) saturate(0.9)` on domain pills instead of computing 16+ dark-mode color variants in Python. Global dark palette only for v1.
5. **Architecture: separate module** — `render_magazine_html()` goes in new `lib/html_renderer.py`, not `pdf_renderer.py`. Shared helpers (`_render_magazine_card`, `_render_front_page`) stay in `pdf_renderer.py` and are imported.

### New Considerations Discovered
- Telegram delivers HTML as a downloadable file (not inline) — user opens in Safari, which is favorable for full CSS/JS
- Safari scroll restoration race: must sync scroll state immediately, not wait for DOMContentLoaded
- `localStorage.getItem()` **throws** (not returns null) in WKWebView on `file://` URLs — must wrap in try/catch
- `::details-content` pseudo-element (Baseline Sept 2025) enables CSS-only open/close animation on Safari 18+
- `animation-timeline: scroll()` not supported in Safari — IntersectionObserver remains correct for scroll-dependent UI
- `<meta name="color-scheme" content="light dark">` tells browser to render native controls in dark theme before CSS loads

---

## Overview

Shift the daily and weekly magazine output from PDF-first (WeasyPrint, A4 portrait) to **HTML-first** (self-contained responsive `.html` file). The primary consumption device is an iPhone 17 Pro (6.3") where the current PDF requires constant pinch-zoom. HTML adapts natively to phone, tablet, and ultrawide. PDF becomes an optional archival export via `--pdf` flag.

**Key deliverables:**
- New responsive CSS (`templates/magazine-responsive.css`) with dark mode, mobile-first breakpoints
- New `lib/html_renderer.py` module with `render_magazine_html()` function
- HTML escaping helpers for all external-sourced content (security prerequisite)
- Updated flag semantics: no flags = HTML output; `--pdf` = also produce PDF
- Updated delivery in both daily and weekly pipelines to send `.html` via Telegram

## Problem Statement / Motivation

The magazine digest is consumed daily on three devices:

| Device | Screen | Current PDF experience |
|--------|--------|----------------------|
| iPhone 17 Pro | 6.3" | Terrible — pinch-zoom, horizontal scroll, tiny text |
| iPad Mini | 8" | Tolerable — still needs zooming for card text |
| 34" ultrawide | 21:9 | Fine but wastes 70% of screen width |

PDF is a fixed-layout print format. The daily use case is scanning 20-50 research items on a phone — a responsive web problem, not a print problem. HTML is ~80KB vs ~2MB PDF, supports dark mode, collapsible sections, and native text selection.

(see brainstorm: `docs/brainstorms/2026-03-28-html-first-magazine-brainstorm.md`)

## Proposed Solution

### Architecture

**Two separate renderers sharing helpers, not a shared body builder.**

The simplicity review identified that extracting `_build_magazine_body()` is premature abstraction — the HTML version needs structurally different markup (`<details>/<summary>`, viewport meta, data attributes for section theming) that would immediately require format conditionals in a shared function.

Instead:
- **Shared helpers** stay in `lib/pdf_renderer.py`: `_render_magazine_card()`, `_render_front_page()`, `_sig_bars_html()`, `domain_pill_color()`, constants (`DOMAIN_COLORS`, `BADGE_COLORS`, `SECTION_THEMES`)
- **New `lib/html_renderer.py`** imports these helpers and builds responsive HTML with `<details>` sections, dark mode CSS variables, and semantic data attributes
- **Existing `lib/pdf_renderer.py`** unchanged — continues to build PDF-oriented HTML and render via WeasyPrint

### Research Insights: Architecture

- The architecture strategist confirmed this is the right module split: `pdf_renderer.py` exporting `render_magazine_html()` violates SRP and makes the module name misleading.
- The pattern recognition specialist confirmed `_build_magazine_body()` naming is consistent with existing private functions, but the function itself is unnecessary if each renderer builds its own markup.
- Re-export for discoverability is optional: `lib/html_renderer.py` is a natural import path.

### Data normalization (architecture strategist finding)

Two HTML generators consume different data shapes:

| Pipeline | Data format | Key fields |
|----------|-------------|------------|
| Daily (`daily_feed_generator.py`) | `merged_data` dict | `headline`, `takeaways`, items with `nlm_body`, `nlm_tldr`, `nlm_summary` |
| Weekly (`magazine_generator.py`) | `magazine` JSON dict | `editorial_brief`, `top_stories`, items with `summary`, `content_type`, `significance`, `type` |

**Resolution:** Add `_to_magazine_json(merged_data, target_date) -> dict` adapter in `daily_feed_generator.py` that normalizes the daily format into the magazine JSON schema. The HTML renderer accepts one schema. This also preserves the article-first layout — items with `nlm_body` >= 300 chars get mapped to `type: "featured"` with full body content.

### Rendering path

```
magazine JSON (from magazine_generator.py or daily_feed_generator.py via adapter)
    |
    v
html_renderer.render_magazine_html()  <-- NEW module
    |  Reads templates/magazine-responsive.css
    |  Inlines CSS into self-contained HTML
    |  Writes {DATE}.html
    |
    +--[--pdf flag]--> pdf_renderer.render_magazine_pdf()  <-- existing, unchanged
    |                   Uses templates/daily-feed.css
    |                   Writes {DATE}.pdf
    |
    v
deliver .html via Telegram (sendDocument)
```

### Progressive enhancement

**Telegram delivers HTML as a downloadable file** (confirmed by research). User taps to download, then opens in Safari. This means full CSS and JS execute. However, v1 is CSS-only (no JS file) per the simplicity review.

| Feature | v1 (CSS-only) | Future v2 (with JS) |
|---------|--------------|---------------------|
| Dark mode | `prefers-color-scheme` auto-detection | + Manual toggle with `localStorage` |
| Collapsible sections | `<details>/<summary>` (native HTML) | + `::details-content` animation |
| Section nav | Anchor links with `scroll-behavior: smooth` | + IntersectionObserver sticky highlight |
| Progress bar | Not included | Scroll-driven with rAF throttling |
| Back to top | Not included | Visible after 500px scroll |

### Research Insights: Progressive Enhancement

- **`::details-content`** (Baseline Sept 2025) enables CSS-only open/close animation. For iOS 15-17, animation doesn't apply but `<details>` still works. Add with `@supports`:
  ```css
  details::details-content {
    opacity: 0; block-size: 0; overflow: hidden;
    transition: opacity 300ms, block-size 300ms, content-visibility 300ms allow-discrete;
  }
  details[open]::details-content { opacity: 1; block-size: auto; }
  ```
- **`animation-timeline: scroll()`** is NOT supported in Safari as of March 2026. IntersectionObserver remains correct for scroll-dependent UI.
- **Safari scroll restoration race** (frontend races reviewer): Safari restores scroll position before DOMContentLoaded. If v2 adds JS, it must sync scroll state immediately at script execution, not wait for an event.
- **`localStorage` throws in WKWebView** on `file://` URLs. If v2 adds theme toggle, wrap all `localStorage` calls in try/catch.

## Technical Approach

### Phase 0: Security Prerequisites (BLOCKING — ~0.5 day)

**The security sentinel found zero HTML escaping in the entire codebase.** All titles, URLs, and summaries from external sources (RSS feeds, newsletters, web scraping) are interpolated into HTML via Python f-strings without escaping. This was acceptable for PDF (WeasyPrint doesn't execute JS) but becomes a real XSS vulnerability when output is opened as HTML in Safari.

#### 0.1 Add escaping helpers to `lib/pdf_renderer.py`

```python
from html import escape as _html_escape

def _safe_link(url: str, text: str) -> str:
    """Render a safe anchor tag with escaped URL and text."""
    if url and url.strip().lower().startswith(('http://', 'https://')):
        return f'<a href="{_html_escape(url, quote=True)}">{_html_escape(text)}</a>'
    return _html_escape(text)

def _safe_attr(value: str) -> str:
    """Escape a string for use in an HTML attribute value."""
    return _html_escape(str(value), quote=True)

def _safe_text(value: str) -> str:
    """Escape a string for use in HTML text content."""
    return _html_escape(str(value))
```

#### 0.2 Apply escaping across both renderers

Replace all raw f-string interpolations of external content in:
- `lib/pdf_renderer.py` — lines 417, 432, 505, 545, 567, 600, 606, 617, 622-625
- `daily_feed_generator.py` — lines 1094, 1109, 1144, 1164, 1194, 1249, 1282, 1295

For bold-markdown-to-HTML conversion: **escape first, then convert**:
```python
text = _safe_text(text)
text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
```

#### 0.3 Add URL scheme validation

```python
def _safe_url(url: str) -> str:
    """Return URL only if it uses a safe scheme."""
    if url and url.strip().lower().startswith(('http://', 'https://')):
        return url
    return ''
```

#### 0.4 Security tests

- Title containing `<script>alert(1)</script>` is escaped in output
- URL containing `"onclick="alert(1)"` is escaped in `href` attributes
- URL with `javascript:` scheme is rejected

### Phase 1: Responsive CSS (~1.5 days)

Create `templates/magazine-responsive.css` — mobile-first, testable in any browser.

#### 1.1 Design system variables

```css
:root {
  color-scheme: light dark;

  /* Typography — rem-based for accessibility */
  --text-xs: 0.8125rem;   /* 13px */
  --text-sm: 0.875rem;    /* 14px */
  --text-base: 1rem;      /* 16px — minimum for phone reading */
  --text-lg: 1.0625rem;   /* 17px */
  --text-xl: 1.25rem;     /* 20px */
  --text-2xl: 1.5rem;     /* 24px */

  /* Spacing — 4px grid */
  --space-1: 0.25rem;  --space-2: 0.5rem;  --space-3: 0.75rem;
  --space-4: 1rem;     --space-6: 1.5rem;  --space-8: 2rem;
  --space-12: 3rem;

  /* Light theme */
  --bg-primary: #ffffff;
  --bg-secondary: #f8fafc;
  --bg-card: #ffffff;
  --text-primary: #0f172a;
  --text-secondary: #475569;
  --text-muted: #94a3b8;
  --border-color: #e2e8f0;
  --link-color: #2563eb;
  --accent-soft: #dbeafe;

  /* Section themes — mapped via data-section attribute */
  --section-research-bg: #eff6ff;  --section-research-accent: #2563eb;
  --section-tech-bg: #fef3e2;     --section-tech-accent: #b45309;
  --section-dev-bg: #ecfdf5;      --section-dev-accent: #059669;
  --section-news-bg: #fdf2f8;     --section-news-accent: #7c3aed;
}

/* Dark theme — global only, no per-section variants in v1 */
@media (prefers-color-scheme: dark) {
  :root {
    --bg-primary: #0f172a;
    --bg-secondary: #1e293b;
    --bg-card: #1e293b;
    --text-primary: #f1f5f9;
    --text-secondary: #94a3b8;
    --text-muted: #64748b;
    --border-color: #334155;
    --link-color: #60a5fa;
    --accent-soft: #1e3a5f;

    --section-research-bg: #172554;  --section-research-accent: #60a5fa;
    --section-tech-bg: #2d2006;     --section-tech-accent: #f0b429;
    --section-dev-bg: #064e3b;      --section-dev-accent: #34d399;
    --section-news-bg: #2e1065;     --section-news-accent: #a78bfa;
  }
}
```

#### Research Insights: Dark Mode

- Use `<meta name="color-scheme" content="light dark">` in `<head>` — tells browser to render native controls (scrollbars, form elements) in dark theme before CSS loads.
- **Domain pill dark mode via CSS `filter`** instead of computing 16+ dark color variants in Python:
  ```css
  @media (prefers-color-scheme: dark) {
    .domain-pill { filter: brightness(1.2) saturate(0.9); }
  }
  ```
  Use `style="--pill-bg: #dc2626"` inline + `.domain-pill { background: var(--pill-bg); }` in CSS. The filter adjusts any inline color for dark backgrounds.
- **Section themes via data attributes** instead of inline style overrides:
  ```html
  <details class="mag-section" data-section="research">
  ```
  ```css
  .mag-section[data-section="research"] {
    --section-bg: var(--section-research-bg);
    --section-accent: var(--section-research-accent);
  }
  ```
  Because the CSS variables are redefined in the dark media query, section colors adapt automatically.

#### 1.2 Responsive breakpoints (two, not three)

The simplicity review correctly identified that the 1024px breakpoint serves no device the user owns. Two breakpoints suffice:

```css
/* Mobile-first: base = iPhone (< 768px) */
/* Single column, full-width cards, large tap targets */
body {
  font-size: var(--text-base);
  line-height: 1.6;
  padding: var(--space-4);
  max-width: 100%;
  -webkit-text-size-adjust: 100%;
  background: var(--bg-primary);
  color: var(--text-primary);
}

/* Tablet + Desktop (768px+): 2-3 col grid, max-width container */
@media (min-width: 48rem) {
  body {
    font-size: var(--text-lg);
    padding: var(--space-8);
    max-width: 75rem; /* 1200px */
    margin: 0 auto;
  }
  .mag-standard-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: var(--space-4);
  }
}

@media print {
  details { open: true; }
  .mag-nav, .mag-progress { display: none; }
}
```

#### Research Insights: Typography & Layout

- Body text must be 16px minimum on phone — never below. The existing PDF CSS uses 9.5pt which is unreadable on phone.
- Line length: `max-width: 72ch` or `75rem` for optimal reading.
- Touch targets: minimum 44x44px (Apple HIG). Apply to `summary`, nav tabs, and card links.
- Use CSS Grid with `minmax()` for card layout instead of `columns` — avoids orphaned small cards on wide screens.
- Sticky nav with `backdrop-filter: blur(8px)` + `-webkit-backdrop-filter` for translucent effect on iOS.

#### 1.3 Sticky section nav

```css
.mag-nav {
  position: sticky;
  top: 0;
  z-index: 100;
  background: color-mix(in srgb, var(--bg-primary) 85%, transparent);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--border-color);
  padding: var(--space-2) var(--space-4);
}

.mag-nav-tabs {
  display: flex;
  gap: var(--space-1);
  overflow-x: auto;
  scrollbar-width: none;
  -webkit-overflow-scrolling: touch;
}
.mag-nav-tabs::-webkit-scrollbar { display: none; }

.mag-nav-tab {
  flex-shrink: 0;
  padding: var(--space-2) var(--space-3);
  border-radius: 9999px;
  font-size: var(--text-sm);
  white-space: nowrap;
  text-decoration: none;
  color: var(--text-secondary);
  min-height: 44px;
  display: flex;
  align-items: center;
}
```

Mobile nav uses abbreviated names ("AI", "Tech", "Dev", "News") via anchor links. Full names on tablet+.

#### 1.4 `<details>/<summary>` collapsible sections

```css
details.mag-section { border-radius: 8px; margin-bottom: var(--space-4); }

summary.mag-section-header {
  padding: var(--space-3) var(--space-4);
  cursor: pointer;
  font-weight: 600;
  font-size: var(--text-xl);
  list-style: none;
  min-height: 44px;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
summary.mag-section-header::-webkit-details-marker { display: none; }

/* CSS-only animation via ::details-content (Safari 18+, Chrome 131+) */
@supports selector(::details-content) {
  details.mag-section::details-content {
    opacity: 0; block-size: 0; overflow: hidden;
    transition: opacity 200ms ease, block-size 200ms ease,
                content-visibility 200ms allow-discrete;
  }
  details.mag-section[open]::details-content {
    opacity: 1; block-size: auto;
  }
}
```

First section gets `open` attribute; others start collapsed.

#### 1.5 Card layout

```css
.mag-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.mag-card-featured {
  grid-column: 1 / -1;  /* Full width on grid */
  border-left: 4px solid var(--section-accent);
}

.mag-card-title {
  font-size: var(--text-base);
  font-weight: 600;
  line-height: 1.3;
}

.mag-card-meta {
  font-size: var(--text-xs);
  color: var(--text-muted);
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
}
```

**Deliverable:** Standalone CSS file testable by opening a static HTML mockup in Chrome DevTools at 375px and 768px widths.

### Phase 2: HTML Renderer (~1.5 days)

#### 2.1 New `lib/html_renderer.py`

```python
"""Self-contained responsive HTML renderer for magazine digests."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.pdf_renderer import (
    _render_front_page,
    _render_magazine_card,
    _safe_link,
    _safe_text,
    _safe_attr,
    domain_pill_color,
    SECTION_THEMES,
    DOMAIN_COLORS,
    BADGE_COLORS,
)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def render_magazine_html(
    magazine: dict[str, Any],
    output_path: Path | str,
    css_path: Path | str | None = None,
) -> Path:
    """Render magazine JSON to self-contained responsive HTML file."""
    css_file = Path(css_path) if css_path else TEMPLATES_DIR / "magazine-responsive.css"
    out = Path(output_path)

    html_body = _build_html_body(magazine)
    css = css_file.read_text(encoding="utf-8")

    full_html = (
        '<!DOCTYPE html>\n'
        '<html lang="en">\n<head>\n'
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '  <meta name="color-scheme" content="light dark">\n'
        f'  <title>{_safe_text(magazine.get("title", "Research Digest"))}</title>\n'
        f'  <style>{css}</style>\n'
        '</head>\n<body>\n'
        f'{html_body}\n'
        '</body>\n</html>'
    )

    out.write_text(full_html, encoding="utf-8")
    return out
```

#### Research Insights: Python Patterns

- **Type annotations** (Python reviewer): Use `Path | str | None = None` not `str = None`. Standardize on `pathlib.Path` throughout.
- **SECTION_THEMES structure** (Python reviewer): Consider a frozen dataclass with a `DEFAULT_THEME` fallback to prevent KeyError on new sections:
  ```python
  @dataclass(frozen=True, slots=True)
  class SectionTheme:
      bg: str; accent: str
  DEFAULT_THEME = SectionTheme(bg="#f5f5f5", accent="#333333")
  def get_section_theme(name: str) -> SectionTheme:
      return SECTION_THEMES.get(name, DEFAULT_THEME)
  ```
- **HTML generation**: Continue using `parts.append()` + `"\n".join(parts)` pattern (O(n) single allocation). Do NOT use `result += f"..."` in loops (O(n^2)).

#### 2.2 Build HTML body with `<details>` sections

The `_build_html_body()` function in `html_renderer.py` builds responsive HTML using the magazine JSON dict. It reuses `_render_magazine_card()` and `_render_front_page()` from `pdf_renderer.py` but wraps sections in `<details>/<summary>` and uses `data-section` attributes for CSS theming.

```python
def _build_html_body(magazine: dict[str, Any]) -> str:
    parts: list[str] = []

    # Front page (editorial brief, top stories, section nav)
    parts.append(_render_front_page(magazine))

    # Sticky nav bar
    parts.append(_render_nav(magazine.get("sections", [])))

    # Sections as <details>
    for i, section in enumerate(magazine.get("sections", [])):
        is_first = (i == 0)
        parts.append(_render_section(section, open_by_default=is_first))

    return "\n".join(parts)
```

#### 2.3 Data normalization adapter

In `daily_feed_generator.py`, add:

```python
def _to_magazine_json(merged_data: dict, target_date: str) -> dict[str, Any]:
    """Normalize daily merged_data into magazine JSON schema."""
    sections = []
    for section_name, items in merged_data.get("sections", {}).items():
        section_items = []
        for item in items:
            item_type = "featured" if len(item.get("nlm_body", "")) >= 300 else "standard"
            section_items.append({
                "id": item.get("id", ""),
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "type": item_type,
                "content_type": item.get("content_type", "research"),
                "summary": item.get("nlm_tldr") or item.get("nlm_summary", ""),
                "body": item.get("nlm_body", ""),
                "significance": item.get("significance", "medium"),
            })
        sections.append({"name": section_name, "items": section_items})

    return {
        "title": merged_data.get("headline", f"Daily Digest — {target_date}"),
        "date": target_date,
        "editorial_brief": merged_data.get("editorial_brief", ""),
        "top_stories": merged_data.get("top_stories", []),
        "sections": sections,
    }
```

This preserves the article-first layout — items with `nlm_body` >= 300 chars become `type: "featured"` and the HTML renderer shows their full body content.

### Phase 3: Pipeline Integration (~0.5 day)

#### 3.1 `daily_feed_generator.py` — flag changes

**Just change the flags** (simplicity review). No deprecation warnings. Single user, updating callers in same change.

```python
parser.add_argument("--pdf", action="store_true", help="Also produce PDF alongside HTML")
# Remove --magazine entirely
# Remove --pdf-only entirely (or keep as hidden alias for --pdf)
```

Update `run()` function:
1. Generate markdown briefing -> `{DATE}.md` (unchanged)
2. Build magazine JSON (merge NLM report if available, normalize via `_to_magazine_json()`)
3. Call `html_renderer.render_magazine_html()` -> `{DATE}.html` (NEW default)
4. If `--pdf`: also call `pdf_renderer.render_magazine_pdf()` -> `{DATE}.pdf`
5. Return path to `.html` file

#### 3.2 `scripts/daily_pipeline.sh` — delivery step

```bash
# Step 6.5: Magazine rendering (updated)
# Was: daily_feed_generator.py --date "$DATE" --magazine --pdf-only
# Now: daily_feed_generator.py --date "$DATE"

# Step 8: Delivery (updated)
HTML_FILE="$DAILY_DIR/$DATE.html"
if [[ -f "$HTML_FILE" ]]; then
    tg_send_file "$HTML_FILE" "Daily Research Digest — $DATE ($ITEM_COUNT items)"
fi
# Podcast delivery unchanged
```

#### Research Insights: Telegram Delivery

- Use descriptive filename: `Research-Digest-2026-03-28.html` not `2026-03-28.html`
- Add `disable_content_type_detection=true` to `sendDocument` call to prevent Telegram from mishandling HTML
- Include item count + date in caption so user knows what's inside before downloading
- Telegram file size limit is 50MB — HTML files at ~80KB are well within

#### 3.3 `scripts/weekly_pipeline.sh` — delivery step

Update Step 5.5: `magazine_generator.py --date "$DATE"` (default is now HTML).
Update Step 7: send `.html` instead of `.pdf`.

#### 3.4 `scripts/magazine_generator.py` — output format

Default output becomes `.html`. `--pdf` flag produces both.

#### 3.5 `scripts/fetch_latest.py` — HTML-first artifact lookup

```python
# Glob for .html first, fall back to .pdf
htmls = sorted(daily_dir.glob(f"{date}*.html"), reverse=True)
if htmls:
    return {"report_path": str(htmls[0]), "has_report": True}
pdfs = sorted(daily_dir.glob(f"{date}*.pdf"), reverse=True)
```

No `"format"` field needed — file extension is the format (simplicity review).

### Phase 4: Testing & Polish (~0.5 day)

#### 4.1 Responsive testing

Test in Chrome DevTools at:
- 375px (iPhone SE / smallest)
- 430px (iPhone 17 Pro)
- 768px (iPad Mini / desktop)

Verify: card layout, section collapsing, nav bar, dark mode (toggle system preference in DevTools), typography scale.

#### 4.2 Telegram delivery testing

1. Generate a real `.html` file with the pipeline
2. Send via Telegram Bot API to personal chat
3. Open on iPhone: download, open in Safari, verify rendering
4. Open on iPad: verify 2-column layout
5. Open on laptop: verify max-width container

#### 4.3 Unit tests

Three tests (simplicity review — drop tests for trivially true template strings):
- `test_render_magazine_html_produces_valid_html()` — output contains `<!DOCTYPE html>`, `<style>`, no `<script src>`
- `test_render_magazine_html_self_contained()` — no external `<link>` tags
- `test_render_magazine_html_has_sections()` — output contains `<details>` elements per section

Plus security tests from Phase 0.4.

#### 4.4 Documentation updates

Per CLAUDE.md Documentation Maintenance table:

| File | Update |
|------|--------|
| `CLAUDE.md` | Data Files table (add `.html`), Daily Feed Commands (update flags), Quick Reference, Utility Modules (add html_renderer.py) |
| `skills/daily-digest/SKILL.md` | Update pipeline steps for HTML output |
| `~/.openclaw/workspace/TOOLS.md` | Update delivery commands |
| `backup.sh` / `restore.sh` | Include `.html` files in backup globs |

### Performance Considerations

#### Research Insights: Performance

- **File size budget**: ~80KB target is well-calibrated. Breakdown: CSS ~15KB, HTML body ~55KB (75 items at ~700 bytes/card), boilerplate ~2KB. At 100 items expect ~95KB — still far smaller than ~2MB PDF.
- **Quadratic URL dedup** (performance oracle finding): `merge_nlm_with_feed()` line 1027 constructs a new set on every iteration. Pre-build set once:
  ```python
  merged_url_set = {normalize_url_deep(mi["url"]) for mi in merged_items}
  ```
- **Inline `style=` colors block dark mode** (performance oracle): Current Python code sets `style="background:#dc2626"` on pills. Use `style="--pill-bg:#dc2626"` + CSS `background: var(--pill-bg)` so dark mode `filter` can adjust.
- **CSS Grid with `minmax()`** instead of `columns` for card layouts on wide screens — prevents orphaned cards.
- **iOS Safari rendering**: ~1500-2000 DOM nodes for 100 items is well within Safari's comfort zone (<10K before jank).
- **Python HTML generation** via `parts.append()` + `"\n".join()` is O(n) — correct pattern, no change needed.

### Future v2 Features (deferred from v1)

When adding JS interactivity in a future version, incorporate these findings:

#### Frontend Race Conditions (from races reviewer)
- **Safari scroll restoration**: Sync scroll state immediately at script execution, plus DOMContentLoaded safety net
- **localStorage in WKWebView**: Wrap ALL calls in try/catch — it throws, not returns null, on `file://` URLs
- **Scroll events**: Must use `{ passive: true }` + `requestAnimationFrame` throttling
- **`<details>` animation**: Guard against double-click with `animating` flag; check `evt.target` on `transitionend` (event bubbles from children)
- **`prefers-reduced-motion`**: Skip animation entirely, let native `<details>` work
- **IntersectionObserver**: Use `rootMargin: '-20% 0px -80% 0px'` not multiple thresholds — creates narrow trigger zone, prevents two nav items highlighting simultaneously

#### JS File Location
- Pattern recognition specialist recommends `static/magazine.js` (not `templates/`) if JS is added. The `templates/` directory is presentation assets only (CSS, prompts).

## Acceptance Criteria

### Functional Requirements

- [x] HTML escaping applied to all external-sourced content in both renderers (Phase 0, BLOCKING)
- [x] `daily_feed_generator.py` produces `{DATE}.html` as default output
- [x] `daily_feed_generator.py --pdf` produces both `{DATE}.html` and `{DATE}.pdf`
- [ ] Weekly `magazine_generator.py` produces `.html` as default output
- [x] HTML file is self-contained: no external CSS, JS, images, or font references
- [x] HTML file size < 150KB for a typical 50-item digest
- [x] `daily_pipeline.sh` delivers `.html` via Telegram
- [ ] `weekly_pipeline.sh` delivers `.html` via Telegram
- [x] `fetch_and_deliver.sh` finds `.html` first, falls back to `.pdf`
- [x] Dark mode auto-detects via `prefers-color-scheme`
- [x] Sections use `<details>/<summary>` (works without JS)
- [x] First section starts expanded, others collapsed
- [x] Sticky nav with anchor links for section jumping

### Non-Functional Requirements

- [ ] Renders correctly on iPhone (375-430px width)
- [ ] Renders correctly on iPad Mini / desktop (768px+ width)
- [ ] All text readable without zoom on iPhone at default text size
- [x] Uses `rem` units (respects iOS Dynamic Type and browser zoom)
- [x] Touch targets minimum 44px height
- [x] `@media print` rules hide nav, expand sections
- [x] Existing PDF rendering path (`render_magazine_pdf()`) continues to work unchanged
- [x] No new Python dependencies required
- [x] `lib/html_renderer.py` does not import WeasyPrint

### Quality Gates

- [ ] All existing tests pass (`pytest tests/ -v`)
- [x] New HTML rendering + security tests added and passing (13 tests)
- [ ] Manual Telegram delivery test on iPhone completed

## Success Metrics

- **Primary:** Daily digest is readable on iPhone without zoom
- **Secondary:** HTML file < 100KB (vs ~2MB PDF)
- **Secondary:** Dark mode works automatically based on system preference

## Dependencies & Prerequisites

- No new Python packages needed (HTML generation is pure string building)
- WeasyPrint stays for optional PDF (`--pdf` flag) — no removal, no lazy import changes
- Telegram Bot API `sendDocument` supports `.html` files
- iOS Safari 15+ for `scroll-behavior: smooth` (~97% of active iOS devices)
- iOS Safari 18+ for `::details-content` animation (graceful degradation on older)

## Risk Analysis & Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| XSS via unescaped external content | HIGH — JS execution in Safari | Phase 0: add `html.escape()` to all interpolation points (BLOCKING) |
| `javascript:` URI in href | MEDIUM — click-to-execute | `_safe_url()` allowlists http/https schemes |
| Two HTML generators drift | Double maintenance | Shared helpers + single magazine JSON schema |
| Dark mode section colors clash | Unreadable on dark backgrounds | Global dark palette + CSS `filter` for pills |
| HTML > 150KB on large digests | Slow on poor connection | Cap references at 20/section (already exists) |

## Alternative Approaches Considered

1. **Hosted static site** — Better UX but requires hosting. Decided against for zero-infrastructure simplicity. (see brainstorm)
2. **Telegraph / Telegram Instant View** — Opens inside Telegram but limited styling. Rejected. (see brainstorm)
3. **Dual output always** — Two paths to maintain. Rejected in favor of HTML-first with optional PDF. (see brainstorm)
4. **Shared `_build_magazine_body()`** — Premature abstraction. Each renderer needs different markup structure. Rejected in favor of shared helpers + separate body builders. (simplicity review)
5. **Lazy WeasyPrint import** — Unnecessary complexity. WeasyPrint is always installed. Keep normal import. (simplicity review)
6. **JS file in v1** — Over-engineered. CSS-only achieves core goals. JS deferred to v2. (simplicity review)
7. **Three responsive breakpoints** — Middle breakpoint (1024px) serves no owned device. Two suffice. (simplicity review)

## Sources & References

### Origin

- **Brainstorm document:** [docs/brainstorms/2026-03-28-html-first-magazine-brainstorm.md](docs/brainstorms/2026-03-28-html-first-magazine-brainstorm.md) — Key decisions: HTML-first self-contained file, mobile-first responsive, subtle + functional features, first section expanded, both daily and weekly pipelines.

### Internal References

- Existing magazine PDF renderer: `lib/pdf_renderer.py:446` (`render_magazine_pdf()`)
- Existing daily HTML generator: `daily_feed_generator.py:1055` (`generate_magazine_html()`)
- Print CSS: `templates/daily-feed.css`
- Section themes: `lib/pdf_renderer.py:340` (`SECTION_THEMES`)
- Domain pill colors: `lib/pdf_renderer.py:39` (`DOMAIN_COLORS`)
- Badge colors: `lib/pdf_renderer.py:63` (`BADGE_COLORS`)
- Daily pipeline delivery: `scripts/daily_pipeline.sh:561-602`
- Weekly pipeline delivery: `scripts/weekly_pipeline.sh:377-529`
- Artifact fetcher: `scripts/fetch_latest.py:131`
- Previous magazine redesign plan: `docs/plans/2026-03-07-feat-magazine-pdf-redesign-plan.md` (superseded)

### External References

- MDN: `prefers-color-scheme` — system dark mode detection
- MDN: `::details-content` — CSS-only collapsible animation (Baseline Sept 2025)
- MDN: `IntersectionObserver` — scroll-position-dependent UI
- Apple HIG: 44px minimum touch targets
- Telegram Bot API: `sendDocument` with `disable_content_type_detection`

### Review Agents Applied

| Agent | Key Finding |
|-------|-------------|
| Architecture Strategist | Data normalization gap; article-first layout at risk; need `_to_magazine_json()` adapter |
| Performance Oracle | Quadratic URL dedup; inline styles block dark mode; use CSS Grid not columns |
| Security Sentinel | **BLOCKING**: Zero HTML escaping; stored XSS via titles/URLs/summaries; need `_safe_link()` helpers |
| Code Simplicity Reviewer | Cut ~40% scope: no JS in v1, no lazy import, no shared body builder, two breakpoints, just change flags |
| Python Reviewer | Fix type annotations (`str | None`), standardize on pathlib, dataclass for themes |
| Frontend Races Reviewer | Safari scroll restoration race, localStorage throws in WKWebView, passive scroll listeners |
| Pattern Recognition | New module `lib/html_renderer.py` not `pdf_renderer.py`; JS in `static/` not `templates/`; flag naming collision |
| Best Practices Researcher | `::details-content` for CSS-only animation; `filter: brightness()` for dark pills; `color-scheme` meta tag |
