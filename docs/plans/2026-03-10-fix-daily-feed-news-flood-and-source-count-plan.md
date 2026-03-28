---
title: "fix: Daily feed news flooding and source_count always zero"
type: fix
status: active
date: 2026-03-10
---

# fix: Daily Feed News Flooding and source_count Always Zero

## Overview

Two related bugs degraded today's daily feed:

1. **News flood** — `collect_today_items()` in `daily_feed_generator.py` pulled 213 newsletter articles into the daily feed (alongside 39 research sources), resulting in 237-item sections. The institutional design allows news in the daily feed, but no cap exists to prevent newsletter batch-runs from overwhelming the layout.

2. **source_count always 0** — `daily_pipeline.sh` Step 10 registers the daily notebook with `'source_count': 0` hardcoded. The shell `$ADDED` counter from Step 3 is never passed into the Python registration block, so the registry always shows 0 even when 15+ sources were successfully added (confirmed: today's notebook has 16 actual sources).

---

## Root Causes

### Bug 1 — Uncapped news in daily feed

`daily_feed_generator.py:104–121` collects from `news-queue.json` with no limit:

```python
# daily_feed_generator.py:104–121  ← bug location
for article in news_queue.get("articles", []):
    extracted = article.get("extracted_at", "")
    if not extracted.startswith(target_date):
        continue
    if article.get("status") not in ("queued",):
        continue
    items.append({...})   # no cap — all 213 articles added
```

News articles use `extracted_at` (set by `newsletter_parser.py:744,777`). When the newsletter parser ran today as Step 0, it batch-extracted emails and stamped all 213 articles with today's timestamp. No downstream cap exists in `collect_today_items()`, `group_by_section()`, or `generate_briefing_markdown()`.

**Note:** The status transition `queued → daily-digested` exists in `mark_daily_digested()` and is part of the intended design — news items belong in the daily feed but must be limited in volume.

### Bug 2 — source_count hardcoded to 0

`daily_pipeline.sh:607` always writes `'source_count': 0`:

```bash
# daily_pipeline.sh:339 — correctly accumulates
ADDED=0
...
ADDED=$((ADDED + 1))   # increments per successful nlm source add

# daily_pipeline.sh:607 — never uses $ADDED
'source_count': 0,     # ← hardcoded, $ADDED not referenced
```

The `$ADDED` variable is in scope at Step 10 (same shell script, no subshell) but is simply not interpolated into the Python `-c` string.

### Secondary cleanup — dead news filter in Step 3

`daily_pipeline.sh:353–355` attempts to collect news URLs for the NLM notebook but silently collects nothing:

```python
for a in news.get('articles', []):
    if a.get('added_at', '').startswith(today) and a.get('url'):
        # news articles have no 'added_at' — always empty string, always skipped
```

Since news should not go into the NLM research notebook, these dead lines should be removed.

---

## Acceptance Criteria

- [ ] Daily feed with 213 news articles extracted today shows ≤ `NEWS_PER_SECTION_LIMIT` (10) news items per section
- [ ] Research items from `sources.json` are never capped — all appear in the daily feed
- [ ] `notebooks.json` entry for today's daily notebook shows actual `source_count` (e.g., 16), not 0
- [ ] `daily_pipeline.sh` Step 3 no longer contains the dead `added_at` news URL filter
- [ ] Existing 184 tests continue to pass
- [ ] New tests added for `collect_today_items()` covering: research-only input, news-only input, mixed input with cap enforcement
- [ ] BUG-008 and BUG-009 logged in `docs/BUGS.md`

---

## Implementation Plan

### Phase 1 — Cap news items in `daily_feed_generator.py`

**File:** `daily_feed_generator.py`

Add a `NEWS_PER_SECTION_LIMIT = 10` constant at module level (near other constants, after imports).

In `collect_today_items()` (lines 104–121), modify the news loop to track a running count per section and break when the cap is reached. Since section assignment happens in `group_by_section()` after this function, the cap must be applied either:

- **Option A (simpler):** Cap total news items to `NEWS_PER_SECTION_LIMIT * 4` (4 sections) = 40 total, taking the most recent by `extracted_at`.
- **Option B (precise):** Apply per-section cap inside `group_by_section()` after routing items to sections.

**Recommendation: Option A** — sorting by `extracted_at` descending and slicing at 40 total is a one-liner and avoids coupling the collection phase to section logic.

```python
# After dedup loop (around line 139), before return:
NEWS_TOTAL_LIMIT = NEWS_PER_SECTION_LIMIT * 4   # ~40

news_items = [i for i in deduped if i.get("data_file") == "news-queue"]
research_items = [i for i in deduped if i.get("data_file") != "news-queue"]

if len(news_items) > NEWS_TOTAL_LIMIT:
    news_items.sort(key=lambda x: x.get("added_at", ""), reverse=True)
    news_items = news_items[:NEWS_TOTAL_LIMIT]

return research_items + news_items
```

Add `"data_file": "news-queue"` to the news article dict (line ~119) — it's already on research items (`"data_file": "sources"` at line 100).

Also add `NEWS_PER_SECTION_LIMIT = 10` constant near top of file.

### Phase 2 — Fix source_count in `daily_pipeline.sh`

**File:** `scripts/daily_pipeline.sh`

At Step 10 (line 590), the Python `-c` string needs `$ADDED` interpolated. Change the hardcoded `'source_count': 0` to use the shell variable:

```bash
# Before (line 607):
    'source_count': 0,

# After:
    'source_count': ${ADDED:-0},
```

`${ADDED:-0}` ensures safe default if Step 3 was skipped. This is a single-character change.

### Phase 3 — Remove dead news filter in Step 3

**File:** `scripts/daily_pipeline.sh`

Remove lines 353–355 (the `for a in news.get('articles')` loop inside the Step 3 inline Python):

```python
# Remove these lines (353-355) — news has no added_at field, never picks up anything:
for a in news.get('articles', []):
    if a.get('added_at', '').startswith(today) and a.get('url'):
        urls.append(a['url'])
```

Also remove the `news = json.load(...)` line (line 348) since it's only used by the dead loop — check no other code in the block references `news` before removing.

### Phase 4 — Tests

**File:** `tests/test_daily_feed.py` (create new file)

```python
# tests/test_daily_feed.py
class TestCollectTodayItems:
    def test_research_only(self, tmp_path): ...           # only sources.json items → all returned
    def test_news_only_under_limit(self, tmp_path): ...   # 5 news items → all returned
    def test_news_only_over_limit(self, tmp_path): ...    # 50 news items → capped at 40
    def test_mixed_research_uncapped(self, tmp_path): ... # 39 research + 213 news → 39 + 40 = 79
    def test_news_sorted_by_recency(self, tmp_path): ...  # when capping, keeps most recent
```

Use `monkeypatch` to override `SOURCES_FILE` and `NEWS_QUEUE_FILE` with tmp_path fixtures, similar to the pattern in `tests/test_magazine.py`.

### Phase 5 — Bug log + docs

- Add BUG-008 (`collect_today_items` uncapped news flood) to `docs/BUGS.md` — status: fixed
- Add BUG-009 (`source_count` always 0 in notebooks.json) to `docs/BUGS.md` — status: fixed
- Add session log `docs/logs/2026-03-10-fix-daily-feed-news-flood-and-source-count.md`
- Update `CLAUDE.md` if any key behavior changes warrant documentation

---

## Files to Change

| File | Change |
|------|--------|
| `daily_feed_generator.py` | Add `NEWS_PER_SECTION_LIMIT = 10` constant; cap news items in `collect_today_items()` |
| `scripts/daily_pipeline.sh` | Step 10: `'source_count': ${ADDED:-0}`; Step 3: remove dead news `added_at` filter |
| `tests/test_daily_feed.py` | New file with 5 tests for `collect_today_items()` |
| `docs/BUGS.md` | Add BUG-008 and BUG-009 |
| `docs/logs/2026-03-10-fix-daily-feed-news-flood-and-source-count.md` | Session log |

---

## Risks & Edge Cases

| Risk | Mitigation |
|------|-----------|
| `NEWS_PER_SECTION_LIMIT * 4` cap too tight for a day with many newsletters | Start at 40 total (10×4); easy to raise if feedback suggests |
| `$ADDED` is 0 if Step 3 was skipped (auth failure) | `${ADDED:-0}` safe default handles this |
| Re-runs of daily pipeline create duplicate notebook registry entries | Already guarded by `if not existing` check at line 599 |
| Removing `news` load from Step 3 Python block breaks something | Verify no other reference to `news` variable in the same Python `-c` block before deleting |

---

## Sources

- `daily_feed_generator.py:73–139` — `collect_today_items()` full body
- `daily_feed_generator.py:264–281` — `group_by_section()`
- `scripts/daily_pipeline.sh:335–384` — Step 3 source addition with `$ADDED` counter
- `scripts/daily_pipeline.sh:587–620` — Step 10 notebook registration with hardcoded `source_count: 0`
- `newsletter_parser.py:724–784` — news article creation with `extracted_at`
- `docs/guide/news-pipeline.md` — status lifecycle (`queued → daily-digested → digested`)
- `docs/BUGS.md` — existing bug log format for BUG-008, BUG-009
