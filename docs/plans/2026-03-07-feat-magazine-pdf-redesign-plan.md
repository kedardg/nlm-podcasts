---
title: "feat: Magazine PDF Redesign"
type: feat
status: completed
date: 2026-03-07
origin: docs/brainstorms/2026-03-07-magazine-redesign-brainstorm.md
---

# feat: Magazine PDF Redesign

## Overview

Redesign the weekly magazine PDF from a flat card-based layout into a newspaper-style publication with an executive front page, three-tier visual hierarchy, visual anchors (domain pills, badges, significance bars), dual typeface system, and RAG-friendly semantic HTML. The fix is CSS + HTML generation changes in 4 files — no architectural overhaul.

## Problem Statement / Motivation

The current magazine PDF (`render_magazine_pdf()`) has poor scanability:
- No front page or executive summary — readers must scroll to find what matters
- Flat hierarchy — featured research papers get the same visual weight as news briefs
- No visual anchors — no way to quickly identify source domains, content types, or significance
- Single typeface — no distinction between structural elements and reading content
- No semantic HTML — articles use plain `<div>` tags, blocking future RAG indexing

The redesign addresses all five issues while staying within the proven WeasyPrint + CSS pipeline (864ms render time for 1204 items).

## Proposed Solution

Transform the magazine output through 4 coordinated changes (see brainstorm: `docs/brainstorms/2026-03-07-magazine-redesign-brainstorm.md`):

1. **CSS rewrite** (`templates/daily-feed.css`) — new v2 magazine styles with front page, pills, badges, significance bars, dual typeface, section color system
2. **HTML generation rewrite** (`lib/pdf_renderer.py`) — semantic `<article>` tags with `data-*` attributes, front page with editorial brief + top stories + section navigator
3. **Magazine JSON enrichment** (`scripts/magazine_generator.py`) — GPT-4o editorial brief, top story selection, section preview text
4. **Prompt update** (`config/prompts/magazine-structure-prompt.txt`) — request editorial brief + section previews alongside existing briefs

## Technical Approach

### Phase 1: Foundation (Schema + Domain Mapping + Top Stories)

Changes to `scripts/magazine_generator.py` that produce the enriched magazine JSON.

#### 1a. Magazine JSON schema additions

Add three new top-level fields to the magazine dict built in `build_magazine_json()` (line 275):

```python
magazine = {
    "title": f"Weekly Research Digest — {date_str}",
    "date": date_str,
    "generated_at": now_iso(),
    "editorial_brief": "",          # NEW: 2-3 sentence thematic overview from GPT-4o
    "top_stories": [],              # NEW: list of top 3-5 item dicts for front page
    "sections": [
        {
            "name": "AI Research",
            "introduction": "",
            "preview": "",          # NEW: 1-sentence navigator preview text
            "items": [...]
        },
    ],
    "stats": {...},
}
```

`top_stories` contains item dicts (same shape as section items) duplicated from sections — items appear on both the front page and in their section. The front page is a preview/index, not a separate content area.

**Backward compatibility**: `render_magazine_pdf()` treats all new fields as optional with empty/absent defaults. Old magazine JSONs render without a front page (sections start on page 1).

#### 1b. Top story selection function

New function `select_top_stories(sections)` in `magazine_generator.py`:

```python
def select_top_stories(sections: list[dict], max_stories: int = 5) -> list[dict]:
    """Select 3-5 top stories for the front page.

    Priority:
    1. Featured items (research with NLM summary) — highest value
    2. If <3 featured, fill from standard research items by confidence score
    3. At most 1 non-research item (only if it has a summary)
    4. Tie-break by section diversity (prefer one item per section)
    """
```

Edge cases:
- 0 featured + 0 standard research → return empty list (front page top stories section omitted)
- All items in one section → no diversity bonus, pick by confidence
- Ties → first encountered wins (stable sort)

#### 1c. Domain-to-color mapping

New function `domain_pill_color(url)` in `lib/pdf_renderer.py`:

```python
DOMAIN_COLORS = {
    "arxiv.org": "#dc2626",            # red — research preprints
    "openreview.net": "#dc2626",       # red — research venue
    "proceedings.mlr.press": "#dc2626",# red — ML conference
    "aclanthology.org": "#dc2626",     # red — NLP papers
    "biorxiv.org": "#dc2626",          # red — bio preprints
    "github.com": "#16a34a",           # green — code
    "gitlab.com": "#16a34a",           # green — code
    "openai.com": "#2563eb",           # blue — AI lab
    "anthropic.com": "#2563eb",        # blue — AI lab
    "deepmind.google": "#2563eb",      # blue — AI lab
    "ai.meta.com": "#2563eb",          # blue — AI lab
    "youtube.com": "#dc2626",          # red — video
    "youtu.be": "#dc2626",             # red — video shortlink
    "substack.com": "#ca8a04",         # yellow — newsletter
    "beehiiv.com": "#ca8a04",          # yellow — newsletter
    "huggingface.co": "#d97706",       # amber — HF
    "dev.to": "#16a34a",              # green — dev blog
    "hashnode.dev": "#16a34a",         # green — dev blog
}
# Default: "#6b7280" (gray)
```

Matching uses the registered domain (strips `www.`, `blog.`, etc. subdomains) — e.g., `blog.openai.com` matches `openai.com`.

#### 1d. Prompt update

Update `config/prompts/magazine-structure-prompt.txt` to request:
- `editorial_brief`: "Write a 2-3 sentence editorial overview (max 80 words) identifying the single most important theme across all sections. Authoritative, concise tone."
- `sections[].preview`: "For each section, write a 1-sentence preview (max 20 words) summarizing the key theme."
- Keep existing `sections[].introduction` and `sections[].briefs[]` unchanged.

Increase `max_tokens` from 4096 to 6144 in `generate_briefs_openai()` (line 196).

#### 1e. Wire new fields into `build_magazine_json()`

After `generate_briefs_openai()` returns:
1. Extract `editorial_brief` from GPT-4o response
2. Extract `preview` per section from response
3. Call `select_top_stories(sections)` to populate `top_stories`

### Phase 2: CSS Rewrite (`templates/daily-feed.css`)

Major rewrite of magazine styles. Strategy: **add new `mag-v2-*` classes** for redesign-specific elements (front page, pills, badges, bars). Modify existing `mag-*` classes only for shared improvements (typography, page rules). This avoids breaking the daily pipeline's `generate_magazine_html()` which uses the same CSS file.

#### 2a. Page setup

```css
@page {
    size: A4 portrait;
    margin: 16mm 14mm 16mm 14mm;
    @top-left { content: string(section-name, first); font: 7pt sans-serif; color: #999; }
    @top-right { content: "Daily Tech Feed"; font: 7pt sans-serif; color: #999; }
    @bottom-center { content: counter(page); font: 7pt sans-serif; color: #999; }
}

@page :first {
    @top-left { content: none; }
    @top-right { content: none; }
    @bottom-center { content: none; }
}
```

If `string-set` does not work on WeasyPrint 68.1, fall back to static text: `@top-left { content: "Weekly Research Digest"; }`.

#### 2b. Typography system

Dual typeface:
- **Sans-serif** (structure): `-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif`
- **Serif** (reading): `Charter, Georgia, "Times New Roman", Times, serif`

Modular scale (1.2 ratio): 7pt, 8.4pt, 10pt, 12pt, 14pt, 17pt, 20pt.

Sans-serif for: headers, pills, badges, labels, metadata, stats, navigator.
Serif for: editorial brief, summaries, section introductions.

#### 2c. Front page styles

New classes (all `mag-v2-*` prefixed):
- `.mag-v2-front-page` — full first-page container with `page-break-after: always`
- `.mag-v2-editorial` — serif text, 10pt, max 80 words
- `.mag-v2-top-stories` — list of top story items
- `.mag-v2-top-story` — title + 1-line summary + source pill
- `.mag-v2-navigator` — section listing with colored dots, item counts, preview text
- `.mag-v2-nav-item` — individual section entry in navigator

#### 2d. Visual anchor styles

- `.mag-v2-pill` — source domain pill (inline-block, 6pt, white text, colored bg, 2pt border-radius, 2px 6px padding)
- `.mag-v2-badge` — content type badge (inline-block, 6pt uppercase, letter-spacing 0.5px, colored bg, 2pt radius)
- `.mag-v2-sig-bars` — significance bar container (flexbox, gap 2px, inline)
- `.mag-v2-sig-bar` — individual bar (6px wide, 10px tall, border-radius 1px)
- `.mag-v2-sig-bar--filled` — filled bar gets `background: var(--section-accent)`
- `.mag-v2-sig-bar--empty` — unfilled bar gets `background: #e5e7eb`

Badge colors:
- RESEARCH → `#dbeafe` bg / `#1e40af` text (blue)
- NEWS → `#fce7f3` bg / `#9d174d` text (pink)
- BLOG → `#dcfce7` bg / `#166534` text (green)
- TOOL → `#e0e7ff` bg / `#3730a3` text (indigo)

#### 2e. Enhanced card styles

Modify existing `.mag-card` to add space for pills/badges. Add:
- `.mag-card-meta` — flex row containing pill + badge + significance bars
- Adjust `.mag-card-summary` font to serif

#### 2f. Page break rules

```css
.mag-section { page-break-before: auto; }
.mag-section-header { page-break-inside: avoid; page-break-after: avoid; }
.mag-card { page-break-inside: avoid; }
.mag-v2-front-page { page-break-after: always; }
```

#### 2g. Column rules

```css
.mag-standard-grid { columns: 2; column-gap: 12pt; column-rule: 0.5pt solid #e5e7eb; }
```

#### 2h. Keep legacy styles

Lines 412-503 (legacy markdown styles) remain unchanged.

### Phase 3: HTML Generation Rewrite (`lib/pdf_renderer.py`)

Rewrite `render_magazine_pdf()` (lines 284-409) and `_render_magazine_card()` (lines 412-441).

#### 3a. Front page generation

New function `_render_front_page(magazine)`:

```python
def _render_front_page(magazine: dict) -> str:
    """Render the executive front page. Returns empty string if no editorial_brief."""
```

HTML structure:
```html
<div class="mag-v2-front-page">
  <header class="mag-header">
    <div class="mag-date">MARCH 7, 2026</div>
    <h1 class="mag-title">{magazine["title"]}</h1>
    <div class="mag-subtitle">{stats summary}</div>
  </header>

  <div class="mag-v2-editorial">
    <p>{editorial_brief}</p>
  </div>

  <div class="mag-v2-top-stories">
    <h2>Top Stories</h2>
    {for story in top_stories:}
    <div class="mag-v2-top-story">
      <span class="mag-v2-pill" style="background:{color}">{domain}</span>
      <strong>{title}</strong> — {summary_first_sentence}
    </div>
  </div>

  <nav class="mag-v2-navigator">
    <h2>In This Issue</h2>
    {for section in sections:}
    <div class="mag-v2-nav-item">
      <span class="mag-v2-nav-dot" style="background:{accent}"></span>
      <strong>{section_name}</strong> ({item_count}) — {preview}
    </div>
  </nav>
</div>
```

Front page is omitted entirely when `editorial_brief` is empty (degradation to current behavior).

#### 3b. Section rendering with semantic HTML

Each item wrapped in `<article>` with data attributes:

```html
<article class="mag-card mag-card-featured"
         data-article-id="{id}"
         data-url="{url}"
         data-section="{section_name}"
         data-content-type="{content_type}">
  <div class="mag-card-meta">
    <span class="mag-v2-pill" style="background:{domain_color}">{domain}</span>
    <span class="mag-v2-badge mag-v2-badge--{content_type}">{CONTENT_TYPE}</span>
    <span class="mag-v2-sig-bars">
      <span class="mag-v2-sig-bar mag-v2-sig-bar--filled"></span>
      <span class="mag-v2-sig-bar mag-v2-sig-bar--filled"></span>
      <span class="mag-v2-sig-bar mag-v2-sig-bar--empty"></span>
    </span>
  </div>
  <h3 class="mag-card-title"><a href="{url}">{title}</a></h3>
  <p class="mag-card-summary">{summary}</p>
</article>
```

#### 3c. Section header with `string-set`

```html
<div class="mag-section-header" style="string-set: section-name '{section_name}';">
  <h2 style="color: var(--section-accent)">{section_name}</h2>
  <span class="mag-item-count">{count} items</span>
</div>
```

#### 3d. Three-tier rendering

- **Featured** (max 3 per section, excess demoted to standard): full-width cards with pill + badge + significance bars + 80-120 word summary
- **Standard**: 2-column grid cards with pill + badge + 30-50 word brief
- **Reference**: compact single-line items with title + domain pill only (no badge, no bars). Capped at 30 with "+ N more" overflow indicator inside the reference container.

Significance bars only on featured and standard cards.

#### 3e. Use magazine title from JSON

Replace hardcoded "Weekly Research Digest" with `magazine.get("title", "Research Digest")`.

### Phase 4: Integration & Testing

#### 4a. Test top story selection

Unit test `select_top_stories()` with edge cases:
- 0 featured items (fills from standard)
- All items in one section (no diversity bonus)
- More than 5 featured (caps at 5)
- 0 items total (returns empty)

#### 4b. Test domain pill color mapping

Unit test `domain_pill_color()` with:
- Known domains (arxiv, github, openai)
- Subdomains (blog.openai.com, www.github.com)
- Unknown domains (returns gray)

#### 4c. Test HTML structure

Unit test `render_magazine_pdf()` with a known magazine JSON dict:
- Assert front page HTML contains editorial brief text
- Assert `<article>` tags have correct `data-*` attributes
- Assert featured items have significance bars
- Assert reference overflow shows "+ N more"
- Assert old magazine JSON (no editorial_brief) renders without front page

#### 4d. End-to-end PDF render

Render a real magazine JSON through WeasyPrint and verify:
- PDF generates without errors
- Front page fits on one A4 page
- `string-set` behavior (test and apply fallback if needed)
- Page breaks don't orphan section headers

### Phase 5: Documentation

#### 5a. Update CLAUDE.md

- Update `pdf_renderer.py` exports description (add `domain_pill_color`)
- Update `magazine_generator.py` description (add editorial brief, top stories)

#### 5b. Update docs/BUGS.md

- No bug changes (this is a feature, not a fix)

#### 5c. Session log

- Create `docs/logs/2026-03-07-magazine-pdf-redesign.md` documenting design decisions

## Degradation Ladder

| Level | Condition | Front Page | Content Tiers |
|-------|-----------|------------|---------------|
| **Full** | NLM + OpenAI available | Editorial brief + top stories + navigator | Featured (NLM summaries) + Standard (GPT briefs) + Reference |
| **Partial** | OpenAI only, no NLM | Editorial brief + top stories (from standard) + navigator | Standard (GPT briefs) + Reference |
| **Minimal** | NLM only, no OpenAI | *Omitted* (no editorial brief) | Featured (NLM summaries) + Reference (no briefs) |
| **Bare** | Neither available | *Omitted* | Reference only (URL list) |

The key rule: front page is omitted when `editorial_brief` is empty. Sections render with whatever tiers are available.

## Key Decisions

1. **CSS collision avoidance**: New redesign elements use `mag-v2-*` prefix. Existing `mag-*` classes are modified only for shared improvements (typography, page rules). Daily pipeline CSS is not broken. (see brainstorm: Key Decision 7)
2. **Top stories are duplicated**: Items appear on both front page and in their section. Front page is a preview/index. (SpecFlow Q4)
3. **Featured cap**: Max 3 featured items per section; excess demoted to standard. Prevents one section from consuming multiple pages. (SpecFlow Q7)
4. **Cross-section dedup**: Add to `magazine_generator.py`. First section in `SECTION_ORDER` wins. (SpecFlow Q5)
5. **Backward compatible**: Old magazine JSONs render without front page. New fields default to empty. (SpecFlow Q11)
6. **max_tokens increase**: 4096 → 6144 for GPT-4o call. (SpecFlow Q10)
7. **Title from JSON**: `render_magazine_pdf()` uses `magazine.get("title")` instead of hardcoded string. (SpecFlow Q14)
8. **Significance bars CSS-only**: Three `<span>` elements with background colors, no SVG. (SpecFlow Q8)
9. **Serif font stack**: `Charter, Georgia, "Times New Roman", Times, serif` — available on macOS, reasonable fallback on Linux/Docker. (SpecFlow Q12)

## Acceptance Criteria

### Functional Requirements

- [x] Front page renders with editorial brief, top 3-5 stories, and section navigator
- [x] Front page omitted when `editorial_brief` is empty (graceful degradation)
- [x] Three-tier content layout: featured (1/3 page), standard (2-column), reference (single-line)
- [x] Source domain pills with correct color mapping for all listed domains
- [x] Content type badges (RESEARCH, NEWS, BLOG, TOOL) on featured and standard cards
- [x] Significance bars (3 bars, filled proportionally) on featured and standard cards
- [x] Dual typeface: sans-serif for structure, serif for reading content
- [x] Semantic `<article>` tags with `data-article-id`, `data-url`, `data-section`, `data-content-type`
- [x] Page break after front page forces sections to start on page 2
- [x] Running headers: section name (top-left), title (top-right), page number (bottom-center)
- [x] First page suppresses running headers
- [x] Reference items capped at 30 per section with "+ N more" overflow
- [x] Featured items capped at 3 per section (excess demoted to standard)
- [x] Cross-section URL deduplication (first section in SECTION_ORDER wins)
- [x] Old magazine JSONs (pre-redesign) render without errors
- [x] `--no-openai` mode produces functional PDF (no front page, reference tiers)
- [x] Magazine title sourced from JSON, not hardcoded
- [x] GPT-4o prompt requests editorial brief + section previews
- [x] `max_tokens` increased to 6144

### Testing Requirements

- [x] Unit tests for `select_top_stories()` (4+ edge cases)
- [x] Unit tests for `domain_pill_color()` (known domains, subdomains, unknown)
- [x] Unit test for `render_magazine_pdf()` HTML structure (front page, data attributes, overflow)
- [x] End-to-end render of real magazine JSON produces valid PDF
- [x] All existing 147 tests continue to pass

## System-Wide Impact

- **Daily pipeline CSS**: No breakage — new elements use `mag-v2-*` prefix; existing classes modified only for shared typography improvements
- **Magazine JSON schema**: Additive only — 3 new optional fields (`editorial_brief`, `top_stories`, section `preview`)
- **GPT-4o prompt**: Updated but backward-compatible output format (new fields added alongside existing)
- **Pipeline delivery**: Unchanged — same PDF output path, same Telegram delivery
- **RAG indexing**: No immediate impact — semantic HTML enables future `--magazine-html` mode in `index_notebook.py` (separate task)

## Files Summary

| File | Action | Lines Affected |
|------|--------|----------------|
| `scripts/magazine_generator.py` | **Edit** | `build_magazine_json()` (line 275), `generate_briefs_openai()` (lines 143-215), new `select_top_stories()` |
| `lib/pdf_renderer.py` | **Edit** | `render_magazine_pdf()` (lines 284-409), `_render_magazine_card()` (lines 412-441), new `_render_front_page()`, new `domain_pill_color()` |
| `templates/daily-feed.css` | **Edit** | Major additions (v2 classes, page rules, typography), minor modifications to existing classes |
| `config/prompts/magazine-structure-prompt.txt` | **Edit** | Add editorial brief + section preview instructions |
| `tests/test_magazine.py` | **Create** | Unit tests for top stories, domain pills, HTML structure |
| `CLAUDE.md` | **Edit** | Update `pdf_renderer.py` and `magazine_generator.py` descriptions |
| `docs/logs/2026-03-07-magazine-pdf-redesign.md` | **Create** | Session log |

## Verification

```bash
# 1. Run existing tests (ensure nothing broken)
.venv/bin/python3 -m pytest tests/ -v

# 2. Run new magazine tests
.venv/bin/python3 -m pytest tests/test_magazine.py -v

# 3. Generate magazine JSON with editorial brief (requires OpenAI)
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib \
  .venv/bin/python3 scripts/magazine_generator.py --date 2026-03-07 --pdf

# 4. Generate magazine JSON without OpenAI (degraded mode)
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib \
  .venv/bin/python3 scripts/magazine_generator.py --date 2026-03-07 --no-openai --pdf

# 5. Re-render old magazine JSON through new code
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib \
  .venv/bin/python3 -c "
from lib.pdf_renderer import render_magazine_pdf
from lib.file_utils import load_json
mag = load_json('data/daily-feed/2026-03-06-magazine.json')
render_magazine_pdf(mag, '/tmp/old-magazine-test.pdf')
print('OK')
"

# 6. Visual inspection
open data/daily-feed/2026-03-07-magazine.pdf
```

## Sources & References

### Origin

- **Brainstorm document:** [docs/brainstorms/2026-03-07-magazine-redesign-brainstorm.md](docs/brainstorms/2026-03-07-magazine-redesign-brainstorm.md) — Key decisions carried forward: three-tier content model, visual anchor system (domain pills + badges + significance bars), dual typeface hierarchy, executive front page structure, RAG deferred as follow-up.

### Internal References

- Current magazine renderer: `lib/pdf_renderer.py:284-441`
- Current magazine generator: `scripts/magazine_generator.py:218-283`
- Current CSS: `templates/daily-feed.css` (503 lines)
- Daily magazine path (separate, not touched): `daily_feed_generator.py:949-1111`
- Section color system: `lib/pdf_renderer.py:311-316`
- GPT-4o brief generation: `scripts/magazine_generator.py:143-215`
- Magazine structure prompt: `config/prompts/magazine-structure-prompt.txt`

### External References

- Newsletter front page patterns: The Batch, Morning Brew, TLDR, The Economist
- WeasyPrint CSS support: variables supported, box-shadow NOT supported, string-set partially supported
- Typography: modular type scale (1.2 ratio), dual typeface (Economist model)
