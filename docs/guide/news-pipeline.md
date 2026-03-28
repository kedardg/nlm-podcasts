# News Pipeline — Tools & Flow

## Two News Streams

The system has two distinct news flows:

1. **Daily Feed** (`/generate daily`, cron 6 PM ET) — Collects ALL items from today, generates a PDF report + podcast
2. **Weekly News Digest** (Saturday cron) — Deep-dive podcast from accumulated news articles in `news-queue.json`

```
Newsletter emails arrive
        │
        ▼
newsletter_parser.py
        │
        ├── Research papers, GitHub repos, YouTube → data/sources.json (research stream)
        └── News articles, blog posts → data/news-queue.json (news stream)
                │
                ├── Daily: daily_feed_generator.py → PDF report → Telegram (6 PM ET)
                └── Weekly: news-digest skill → NotebookLM notebook + podcast → Telegram (Saturday)
```

## Daily Feed Pipeline

### Trigger
- Cron job: 6 PM ET daily
- On-demand: `/generate daily` via Telegram
- Router reason: `command_generate_daily`

### Flow (TOOLS.md "Daily Feed Pipeline")

```
Step 0: newsletter_parser.py        ← fetch new emails
Step 0.5: daily_feed_generator.py   ← collect today's items, generate PDF
Step 1: Copy PDF to ~/.openclaw/workspace/
Step 2: openclaw message send --media → Telegram
Step 3: Reply with summary
```

### `daily_feed_generator.py` — Code Walkthrough

The script (`daily_feed_generator.py`) does the following:

1. **Collect today's items**: Reads both `sources.json` and `news-queue.json`, filters to items added today (by `added_at` or `extracted_at` date)
2. **Classify into 4 sections** using the `daily_section` field from `categories.json`:
   - **AI Research**: ml, nlp, cv, robotics
   - **Tech**: tech
   - **Development**: oss, devx
   - **News**: ai-safety, general
3. **Generate markdown briefing**: Structured document with sections, each item with title and URL
4. **Convert to PDF**: Uses `pdf_renderer.py` (WeasyPrint + markdown-it-py) with `templates/daily-feed.css`

```bash
# Full pipeline
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python3 daily_feed_generator.py

# Dry run (preview without saving)
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python3 daily_feed_generator.py --dry-run

# Specific date
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python3 daily_feed_generator.py --date 2026-02-16

# PDF only (no NotebookLM notebook/podcast)
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python3 daily_feed_generator.py --pdf-only
```

**Output files** in `data/daily-feed/`:
- `YYYY-MM-DD.md` — Markdown briefing
- `YYYY-MM-DD.pdf` — Styled PDF (via WeasyPrint)
- `YYYY-MM-DD-podcast.mp3` — Podcast (if NotebookLM notebook created)

### Daily Feed Sections (categories.json mapping)

Each category in `categories.json` has a `daily_section` field:

| Category Slug | Daily Section |
|---|---|
| ml, nlp, cv, robotics | AI Research |
| tech | Tech |
| oss, devx | Development |
| ai-safety, general | News |

### Status Transitions

- News-queue items: `queued` → `daily-digested` (by daily_feed_generator.py)
- Sources.json items: remain `queued` — they're still eligible for the weekly deep-dive
- This means research sources appear in BOTH the daily feed AND the weekly digest

## Weekly News Digest Pipeline

### Trigger
- Saturday 9 AM ET cron (as part of the full weekly pipeline)
- On-demand: `/generate news` via Telegram (but see below for behavioral difference)

### Saturday Cron Flow (news-digest skill, `SKILL.md`)

```
Step 1: Read news-queue.json → collect articles with status "queued" or "daily-digested"
Step 2: (Optional) Fetch article content via trafilatura
Step 3: Create structured briefing note (markdown)
Step 3.5: nlm_ensure_auth.sh
Step 4: nlm notebook create "AI News - Week of YYYY-MM-DD"
Step 5: Add top ~15 articles as URL sources + briefing as text source
Step 6: nlm audio create → deep_dive podcast
Step 7: Poll nlm studio status until complete
Step 8: Mark articles as "digested" in news-queue.json + record in notebooks.json
Step 9: Hand off to digest-deliver for Telegram delivery
```

### `/generate news` Flow (TOOLS.md "News Podcast Pipeline")

This is different from the Saturday cron! It does NOT create a new notebook:

```
Step 1: fetch_latest.py --category news → find latest existing news notebook
Step 2: nlm_ensure_auth.sh
Step 3: nlm audio create <existing-notebook> → generate NEW podcast over existing sources
Step 4: Poll until complete
Step 5: Download podcast → ~/.openclaw/workspace/news-podcast.mp3
Step 6: Deliver to Telegram
```

The distinction: Saturday cron creates a new notebook with fresh sources. `/generate news` reuses the latest news notebook and just generates a new podcast episode from its existing sources.

## `data/news-queue.json` Schema

```json
{
  "articles": [
    {
      "id": "news_001",
      "url": "https://example.com/article",
      "canonical_url": "https://example.com/article",
      "title": "Article Title",
      "source_newsletter": "nl_001",
      "source_newsletter_name": "TLDR AI",
      "extracted_at": "2026-02-15T10:30:00Z",
      "category": "tech",
      "content_type": "news",
      "content_snippet": null,
      "status": "queued",
      "digest_id": null
    }
  ]
}
```

Status values:
- `queued` — New, not yet processed by any pipeline
- `daily-digested` — Included in daily feed PDF but still eligible for weekly digest
- `digested` — Included in weekly news digest notebook
- `duplicate` — Duplicate detected by data_triage.py

## Briefing Note Format (Weekly)

The news-digest skill generates a structured companion note:

```markdown
# AI News Briefing — Week of {date}

## Top Story
[Most important article, 2-3 sentence summary]
Source: [URL]

## Research Highlights
1. **[Paper Title]** — [One-line summary]. Source: [URL]

## Industry & Product News
- [Headline] — [One sentence]. Source: [URL]

## Open Source & Tools
- **[Repo/Tool]** — [What it does]. Source: [URL]

## Quick Hits
- [One-liner with source]

---
Sources: {count} articles from {count} newsletters
Period: {date range}
```

## Minimum Article Threshold

The news-digest skill requires at least 3 queued articles to generate a digest. If fewer exist:
- Response: "Not enough news articles for a digest (need at least 3, have {count})."
- The user should subscribe to more newsletters or wait for more articles to accumulate.
