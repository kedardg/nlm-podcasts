# HTML-First Responsive Magazine

**Date:** 2026-03-28
**Status:** Brainstorm complete

## What We're Building

Shift the daily/weekly magazine from a PDF-first pipeline to an **HTML-first responsive magazine** delivered as a single self-contained `.html` file via Telegram. PDF becomes an optional archival export.

**The problem:** The current magazine is rendered as A4 portrait PDF (WeasyPrint). The primary consumption device is an iPhone 17 Pro (6.3") — reading A4 PDFs on a phone requires constant pinch-zoom and horizontal scrolling. The iPad Mini (8") is slightly better but still awkward. The 34" ultrawide wastes most of its width.

**The solution:** A responsive HTML file (~80KB) with inlined CSS + minimal JS that adapts to all three devices. Opens instantly in Safari from Telegram, works offline after download.

## Why This Approach

### HTML-first over PDF-first

- **Responsive by nature** — adapts to 6.3" phone, 8" tablet, and 34" ultrawide without any compromise
- **Smaller file size** — ~80KB HTML vs ~2MB PDF
- **Native text selection** — copy URLs, quotes, search within the page
- **Dark mode** — auto-detects system preference, with manual override
- **Better reading UX** — collapsible sections, sticky navigation, progress tracking
- **Already halfway there** — `daily_feed_generator.py:generate_magazine_html()` already builds complete HTML; it just feeds into WeasyPrint today

### Single self-contained file over hosted

- **Zero infrastructure** — no hosting, no DNS, no deployment pipeline
- **Offline reading** — works after download, perfect for commute/travel
- **Telegram-native delivery** — attach file, tap to open in Safari
- **Privacy** — no server logs, no analytics, your data stays local

### Subtle + functional whimsy over rich animations

- **Daily utility tool** — not a showcase; needs to get out of the way
- **Phone-first** — animations drain battery, slow scroll, add jank
- **Purposeful interactivity** — dark mode, collapsible sections, progress bar all serve reading efficiency
- **Minimal JS footprint** — ~3-5KB inlined, no frameworks

## Key Decisions

### 1. Responsive breakpoints (3 devices)

| Breakpoint | Device | Layout |
|------------|--------|--------|
| < 768px (default) | iPhone 17 Pro | Single column, full-width cards, collapsible sections, large tap targets |
| 768px - 1024px | iPad Mini | Two-column card grid, expanded sections, sidebar-optional |
| > 1024px | 34" ultrawide | Three-column cards, max-width container (~1200px), full article sidebar |

Mobile-first CSS: base styles target phone, `@media` queries enhance for larger screens.

### 2. Design system (ArchitectUX principles)

Port the existing section theming (CSS custom properties) from `daily-feed.css` and extend:

- **CSS custom properties** for all colors, spacing, typography (already partially exists)
- **Dark theme** via `[data-theme="dark"]` + `prefers-color-scheme` media query
- **Spacing scale** based on 4px grid (already close to this in current CSS)
- **Typography scale** optimized for phone reading:
  - Body: 16px (not 9.5pt) — readable without zoom
  - H2 sections: 20px
  - H3 articles: 17px
  - Meta text: 13px
- **Section color theming** — reuse existing `--section-accent` / `--section-bg` pattern

### 3. Interactive features (functional whimsy)

| Feature | Purpose | Implementation |
|---------|---------|----------------|
| Dark/light/system toggle | Reduce eye strain, respect system pref | CSS vars + ~20 lines JS, `localStorage` persistence |
| Collapsible sections | Scan 4 sections without scrolling past 50 items | `<details>/<summary>` (zero JS) or toggle with ~10 lines JS |
| Sticky section nav | Always know where you are, tap to jump | `position: sticky` bar with section dots/names |
| Reading progress bar | Visual progress through long digest | `scroll` event listener, CSS `scaleX` transform, ~10 lines JS |
| Smooth scroll to sections | Quick navigation from TOC/nav | `scroll-behavior: smooth` (CSS only) + anchor links |
| "Back to top" button | Quick return after deep scroll | Appears after 500px scroll, fixed bottom-right |

**Not included (YAGNI):** confetti, easter eggs, achievements, swipe gestures, bookmarking, keyboard shortcuts. Can add later if needed.

### 4. Card layout for phone

- **Featured items**: Full-width card with accent left border, full summary, domain pill + significance
- **Standard items**: Full-width on phone (stacked), 2-col on iPad, 3-col on desktop
- **Briefs**: Compact list — title + one-line TL;DR, no card chrome
- **References**: Expandable "N more items" section, collapsed by default on phone

### 5. Front page

- Date + title header
- Editorial brief (collapsible on phone)
- Top 3-5 stories as linked cards
- Section navigator with item counts (doubles as sticky nav)

### 6. PDF as optional export

- Keep `render_magazine_pdf()` working but invoke it only on `--pdf` flag
- Default pipeline output becomes `.html`
- PDF uses a print-optimized stylesheet derived from the same HTML (future: `@media print` in the HTML CSS)

### 7. File naming

- `data/daily-feed/2026-03-28.html` (new primary output)
- `data/daily-feed/2026-03-28.pdf` (optional, with `--pdf`)
- `data/daily-feed/2026-03-28.md` (keep for backwards compat)

### 8. Delivery changes

The pipeline currently sends PDF via `openclaw message send --media`. Change to send `.html` file instead. Caption includes item count and date.

## Architecture

### New rendering path

```
magazine JSON (from magazine_generator.py or daily_feed_generator.py)
    |
    v
generate_magazine_html()  <-- already exists, needs responsive CSS swap
    |
    v
write self-contained .html file  <-- NEW: inline CSS + JS, write to data/daily-feed/
    |
    +--[--pdf flag]--> render_magazine_pdf()  <-- existing, optional
    |
    v
deliver .html via Telegram (sendDocument)
```

### Pipeline integration

#### Flag changes

| Old flag | New behavior | Notes |
|----------|-------------|-------|
| `(no flags)` | Produces `{DATE}.html` (magazine layout) | HTML is the new default |
| `--pdf` | Produces `{DATE}.html` + `{DATE}.pdf` | NEW flag, replaces `--magazine` |
| `--pdf-only` | Deprecated alias for `--pdf --skip-nlm` | Backward compat, warn on use |
| `--magazine` | Removed (HTML is always magazine layout) | No-op with deprecation warning |
| `--skip-nlm` | Same behavior (skip NLM steps) | Unchanged |
| `--date YYYY-MM-DD` | Same behavior | Unchanged |
| `--dry-run` | Same behavior | Unchanged |

#### Scope: both daily and weekly

Both `daily_pipeline.sh` and `weekly_pipeline.sh` get HTML-first treatment. The same responsive CSS/JS template works for both — the content structure (sections, cards, articles) is identical.

#### Delivery changes

**Daily pipeline** (`scripts/daily_pipeline.sh`):
- Step 8 (magazine rendering): produce `.html` instead of `.pdf` by default
- Step 9 (delivery): `tg_send_file("{DATE}.html", "Daily Feed — {DATE}")`
- If `--pdf`: also send PDF as second file
- Podcast delivery unchanged (`.mp3` via `sendAudio`)

**Weekly pipeline** (`scripts/weekly_pipeline.sh`):
- Magazine generator step: produce `.html`
- News digest step: produce `.html`
- Delivery: send `.html` files via Telegram

**Fetch & deliver** (`scripts/fetch_and_deliver.sh`):
- `daily` artifact type: look for `.html` first, fall back to `.pdf`
- `research`/`news` artifact types: unchanged (these are NLM notebook artifacts)

#### File naming (daily)

| File | Status | Purpose |
|------|--------|---------|
| `{DATE}.html` | **NEW primary** | Responsive magazine HTML |
| `{DATE}.pdf` | Optional (`--pdf`) | Archival PDF |
| `{DATE}.md` | Keep | Markdown source (backward compat, RAG indexing) |
| `{DATE}-report.md` | Keep | NLM report (input to magazine) |
| `{DATE}-report.pdf` | Optional (`--pdf`) | Rendered NLM report |
| `{DATE}-podcast-raw.mp4` | Keep | Raw NLM audio |
| `{DATE}-podcast.mp3` | Keep | Compressed podcast |

### Files to modify

| File | Change |
|------|--------|
| `templates/daily-feed.css` | Keep for PDF rendering (legacy) |
| `lib/pdf_renderer.py` | Add `render_magazine_html()` function — writes self-contained `.html` with inlined CSS+JS |
| `daily_feed_generator.py` | Default output → HTML; `--pdf` flag for optional PDF; remove `--magazine` flag; update `main()` flow |
| `scripts/daily_pipeline.sh` | Send `.html` in delivery step; update flag handling |
| `scripts/weekly_pipeline.sh` | Send `.html` in delivery step |
| `scripts/fetch_and_deliver.sh` | Look for `.html` first in daily artifact path |
| `scripts/magazine_generator.py` | Output `.html` alongside or instead of `.pdf` |

### New files

| File | Purpose |
|------|---------|
| `templates/magazine-responsive.css` | Responsive CSS with dark mode, phone-first layout, design system variables |
| `templates/magazine.js` | Minimal JS for theme toggle, collapsible sections, progress bar (~50 lines) |

## Resolved Questions

1. **Section default state on phone** — First section (AI Research) starts expanded, others collapsed. Gives immediate content on load while keeping the digest scannable.
2. **Telegram in-app browser limitations** — Telegram's iOS WKWebView supports `localStorage`. Theme toggle will persist. Fall back to system preference if `localStorage` unavailable.
3. **WeasyPrint compatibility** — Keep separate stylesheets: `magazine-responsive.css` for HTML viewing, existing `daily-feed.css` for PDF. Trying to share one stylesheet adds complexity for minimal gain. `@media print` in the responsive CSS can handle basic print from browser.

## Out of Scope

- Hosting / web server setup (future consideration)
- PWA / service worker
- Analytics or tracking
- User accounts or personalization beyond theme
- Rich animations, gamification, easter eggs
- RSS feed generation from magazines
