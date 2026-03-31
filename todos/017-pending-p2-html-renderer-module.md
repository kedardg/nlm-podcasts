---
status: pending
priority: p2
issue_id: "017"
tags: [code-review, architecture, html-first-magazine]
dependencies: ["016"]
---

# Create `lib/html_renderer.py` — Responsive HTML Magazine Renderer

## Problem Statement

The plan requires a new HTML rendering function. Architecture and pattern recognition reviewers agree it should NOT go in `pdf_renderer.py` (SRP violation — module name implies PDF-only). A new `lib/html_renderer.py` module is the correct location.

## Findings

**Sources:** Architecture Strategist, Pattern Recognition Specialist, Code Simplicity Reviewer

- `pdf_renderer.py` is explicitly named and documented as a PDF renderer
- Adding `render_magazine_html()` there violates SRP and creates naming confusion
- Shared helpers (`_render_magazine_card`, `_render_front_page`, `domain_pill_color`, constants) stay in `pdf_renderer.py` and are imported
- Each renderer builds its own HTML body — no shared `_build_magazine_body()` abstraction (simplicity reviewer: premature abstraction)

## Proposed Solutions

### Solution 1: New `lib/html_renderer.py` (Recommended)
**Pros:** Clean SRP, natural import path, no naming confusion
**Cons:** New file (minimal overhead)
**Effort:** Medium (1.5 days including CSS)
**Risk:** Low

### Solution 2: Keep in `pdf_renderer.py` with comment
**Pros:** Fewer files
**Cons:** SRP violation, misleading module name, confusing imports
**Effort:** Small
**Risk:** Medium (maintenance confusion)

## Recommended Action

Solution 1. Create `lib/html_renderer.py` per the plan Phase 2.

## Technical Details

**New file:** `lib/html_renderer.py`
**New file:** `templates/magazine-responsive.css`
**Imports from:** `lib/pdf_renderer` (shared helpers and constants)

Key function signature:
```python
def render_magazine_html(
    magazine: dict[str, Any],
    output_path: Path | str,
    css_path: Path | str | None = None,
) -> Path:
```

## Acceptance Criteria

- [ ] `lib/html_renderer.py` exists with `render_magazine_html()` function
- [ ] Does not import WeasyPrint
- [ ] Produces self-contained HTML (no external CSS/JS/image references)
- [ ] HTML contains `<meta name="viewport">` and `<meta name="color-scheme">`
- [ ] Sections rendered as `<details>/<summary>` elements
- [ ] First section has `open` attribute, others don't
- [ ] Unit tests pass

## Work Log

| Date | Action | Learnings |
|------|--------|-----------|
| 2026-03-28 | Created from plan review | Architecture + pattern reviewers agree on separate module |

## Resources

- Plan: `docs/plans/2026-03-28-001-feat-html-first-responsive-magazine-plan.md` (Phase 2)
