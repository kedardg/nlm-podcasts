# 2026-03-10 — Fix Daily Feed News Flooding and source_count

## Problem

Two issues reported after today's pipeline run:

1. Daily feed had 237 items (expected ~39) — each section flooded with 100+ links
2. notebooks.json always showed `source_count: 0` even when sources were added

## Investigation

### News flooding (BUG-008)

- `daily_feed_generator.py::collect_today_items()` collects from both `sources.json` (research) and `news-queue.json` (news/blog)
- News articles use `extracted_at`, not `added_at` — stamped when newsletter parser processes emails
- Today's Step 0 ran and extracted 213 fresh newsletter articles, all with `extracted_at = 2026-03-10`
- No cap existed → all 213 appeared in the daily feed alongside 39 research sources = 252 items (237 after dedup)
- Per `docs/guide/news-pipeline.md`, news items ARE intentionally included in the daily feed (status transitions to `daily-digested`), but no volume guard existed

### source_count always 0 (BUG-009)

- `daily_pipeline.sh` Step 3 accumulates `$ADDED` counter correctly
- Step 10 writes notebook registry but hardcodes `'source_count': 0` — `$ADDED` never referenced
- Actual notebook confirmed to have 16 sources via `nlm source list daily-2026-03-10`
- Also found: both Playwright and CLI URL collection blocks had a dead `for a in news.get('articles')` loop filtering by `added_at` — news articles have no `added_at` field, so this loop silently collected nothing and was just dead code

## Fixes

### daily_feed_generator.py
- Added `NEWS_DAILY_LIMIT = 40` constant
- After dedup in `collect_today_items()`, split items into research/news, sort news by `extracted_at` desc, slice to limit
- Added `extracted_at` field to news item dicts (needed for sort key)

### daily_pipeline.sh
- Step 10: `'source_count': 0` → `'source_count': ${ADDED:-0}`
- Step 3 (CLI path, lines ~343-364): removed dead `for a in news.get('articles')` loop + `news = json.load(...)` import
- Step 3 (Playwright path, lines ~190-209): same dead loop removed

## Tests

New file `tests/test_daily_feed.py` — 6 tests:
- Research-only: 50 items → 50 returned (no cap)
- News under limit: 10 items → 10 returned
- News over limit: 100 items → capped at 40
- Mixed: 39 research + 213 news → 39 + 40 = 79
- Recency sort: cap keeps most recent by `extracted_at`
- Date filter: items from other dates excluded

Full suite: 190 tests, all passing (was 184).

## Key Decisions

- `NEWS_DAILY_LIMIT = 40` (10 per section × 4 sections) — easy to raise if needed
- Research items never capped — daily feed should always show all today's papers
- Capping at collection time (not display time) keeps downstream code simple
