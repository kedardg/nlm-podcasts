# Magazine PDF Redesign — Brainstorm

**Date**: 2026-03-07
**Status**: Draft
**Scope**: Redesign the magazine PDF output format for better scanability, visual hierarchy, information density, and RAG-friendly structure

---

## What We're Building

A redesigned magazine PDF layout that transforms the current flat card-based output into a polished, newspaper-style publication with:

1. **Executive front page** — a single opening page with top stories, stats ribbon, and section navigator so you can immediately scan what matters
2. **Tiered visual hierarchy** — featured research gets half-page treatment with source pills and significance bars; news/blog items become a tight reference table
3. **Visual anchors** — source domain pills (color-coded by domain: arxiv red, github green, etc.), content type badges, section accent bars, significance indicators
4. **Better information density** — generous space for the important, compact for the rest. Dual typeface: sans-serif for structure/navigation, serif for reading content
5. **RAG-friendly HTML structure** — `<article>` tags with `data-*` attributes so each item is independently extractable for ChromaDB indexing

## Why This Approach

**Magazine Redesign (over Structured JSON or Component-Based Rendering)** because:

- PDF stays the primary delivery format (Telegram). No need for HTML/email output right now
- The current WeasyPrint + CSS pipeline is proven and fast (864ms for 1204 items)
- The fix is mostly CSS + HTML generation changes in `render_magazine_pdf()` — no architectural overhaul needed
- RAG indexing comes as a free byproduct of semantic HTML (data attributes on `<article>` tags)
- Can iterate visually without changing data flow

## Key Decisions

### 1. Front Page Structure (Newspaper Model)

Follow the "three questions" hierarchy used by The Batch, Morning Brew, and The Economist:

1. **What's the single most important theme?** → Editorial brief (GPT-4o writes a 2-3 sentence lens, generated as part of the existing magazine structuring call — not a separate API call)
2. **What are the top 3-5 things I should read?** → Top Stories section with title + one-line summary + source pill
3. **What else is covered and where?** → Section navigator with colored dots, item counts, and theme preview text

**Top story selection**: Pick 3-5 items using this priority stack:
1. Featured items (research with NLM summary) — already the highest-value items
2. If fewer than 3 featured items, fill from standard research items by confidence score (from triage classifier)
3. At most 1 non-research item (only if it's a major industry story with GPT summary)
4. Tie-break by section diversity — prefer one item per section over clustering

Page break after the navigator forces sections to start on page 2.

### 2. Three-Tier Content Layout

| Tier | Items | Treatment | Space per Item |
|------|-------|-----------|---------------|
| **Featured** (must-read) | Research with NLM/GPT summary | Full card: source pill, significance bars, title, 80-120 word summary | ~1/3 page |
| **Standard** (worth scanning) | Research without summary, notable news | Two-column card: source pill, title, 30-50 word brief | ~1/8 page |
| **Reference** (completeness) | News/blog briefs | Compact single-line: title + domain pill | ~1 line |

Cap reference items at 30 per section with "+ N more" overflow.

### 3. Visual Anchor System

**Source domain pills** — small colored badges showing item origin:
- arxiv.org → red bg
- github.com → green bg
- openai.com / anthropic.com / deepmind → blue bg
- youtube.com → red bg
- substack / newsletters → yellow bg
- huggingface.co → amber bg
- default → neutral gray

**Content type badges** — uppercase micro-labels:
- RESEARCH → blue
- NEWS → pink
- BLOG → green
- TOOL → indigo

**Significance bars** — three small bars, filled proportionally (high=3, medium=2, low=1) in the section accent color. Subtle but scannable.

**Section color system** — CSS custom properties via `data-section` attributes:
- AI Research → blue (#1a56db)
- Tech → amber (#b45309)
- Development → green (#15803d)
- News → purple (#7c3aed)

### 4. Typography Hierarchy

Dual typeface approach (like The Economist):
- **Sans-serif** (system fonts) for all structural elements: headers, pills, badges, labels, metadata, stats
- **Serif** (Charter/Georgia) for all reading content: summaries, editorial briefs, section intros

Modular scale at 1.2 ratio: 7pt → 8.4pt → 10pt → 12pt → 14pt → 17pt → 20pt

### 5. RAG Integration (Follow-Up)

Use semantic `<article>` tags with `data-*` attributes (`data-article-id`, `data-url`, `data-section`, `data-content-type`) in the rendered HTML. This makes future RAG indexing a simple HTML parse — but the indexer itself (`--magazine-html` mode for `index_notebook.py`) is a separate task, not part of the visual redesign.

### 6. Page Layout Rules

- Running page headers: current section name (top-left), "Daily Tech Feed" (top-right), page number (bottom-center)
- First page suppresses running headers (front page has its own header)
- WeasyPrint `string-set` for dynamic section names in headers. Fallback if unsupported: static "Weekly Research Digest" in top-left instead of dynamic section name
- `page-break-inside: avoid` on all cards and section headers
- `page-break-after: avoid` on titles
- `column-rule` (thin gray line) between two-column grids

### 7. Scope — What Changes

| File | Change |
|------|--------|
| `templates/daily-feed.css` | Major rewrite — new magazine v2 styles (front page, pills, badges, section system, typography). Keep legacy markdown styles at bottom for backwards compat. |
| `lib/pdf_renderer.py` | Rewrite `render_magazine_pdf()` and `_render_magazine_card()` — semantic HTML with `<article>` tags, data attributes, front page generation, section navigator |
| `scripts/magazine_generator.py` | Add editorial brief generation (GPT-4o), top stories selection, section preview text to magazine JSON |
| `config/prompts/magazine-structure-prompt.txt` | Update prompt to request editorial brief + section themes alongside briefs |
| `scripts/index_notebook.py` | *(Follow-up)* Add `--magazine-html` mode to extract and index article chunks from rendered HTML |

### 8. What Stays the Same

- Magazine JSON schema (additive changes only — new fields for editorial, top stories)
- Daily pipeline's `generate_magazine_html()` in `daily_feed_generator.py` (separate path, not touched)
- Triage pipeline (`hf_triage.py`, `classifier.py`)
- PDF renderer's other entry points (`render_pdf()`, `render_html_pdf()`)
- Delivery pipeline (Telegram, same PDF output path)

## Resolved Questions

- **Output format?** PDF stays primary (Telegram delivery). No HTML/email needed now.
- **Which approach?** Magazine Redesign — CSS + HTML changes only, no architectural overhaul.
- **RAG scope?** Semantic HTML now (free), indexer later (separate task).
- **Top story selection?** Featured items first, then by classifier confidence, with section diversity tie-break.

## Research References

- Newsletter front page patterns: The Batch (editorial lens), Morning Brew (hero + decreasing depth), TLDR (emoji sections), The Economist (contents + editorial)
- Information density: Modular type scale (1.2 ratio), dual typeface hierarchy (sans structure + serif reading), three-tier content model (front-of-book / feature well / service journalism)
- Visual anchors: Source domain pills with domain-specific colors, significance bar indicators (3-bar fill), content type badges
- WeasyPrint: CSS variables supported, linear-gradient supported, box-shadow NOT supported, string-set partially supported, columns reliable for multi-column
- RAG chunking: `<article>` semantic tags with `data-*` attributes as chunk boundaries, 50-150 tokens per chunk for short technical summaries

## Internal References

- Current magazine renderer: `lib/pdf_renderer.py::render_magazine_pdf()` (lines 284-409)
- Current magazine generator: `scripts/magazine_generator.py`
- Current CSS: `templates/daily-feed.css` (503 lines)
- Daily magazine path (separate): `daily_feed_generator.py::generate_magazine_html()` (lines 949-1111)
- Triage pipeline plan: `docs/plans/2026-03-05-fix-pipeline-reliability-and-hf-triage-plan.md`
