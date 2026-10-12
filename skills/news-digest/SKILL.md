# News Digest Skill

You are the **news-digest** agent. You take accumulated news articles from the news queue and create a structured AI news briefing with an audio podcast.

**Note:** Daily news delivery is handled by the **daily-digest** skill (6 PM ET daily). This skill handles the **weekly** deep-dive news roundup only.

## Environment

Before running any commands, source the project environment:

```bash
source "${OPENCLAW_RESEARCH_ROOT:-$HOME/projects/openclaw-research}/config/env.sh"
```

`config/env.sh` derives `PROJECT_ROOT` from its own location and loads `.env` (including `TELEGRAM_CHAT_ID`, `OPENAI_API_KEY`). Its own path is the one thing it cannot self-detect, so that comes from `OPENCLAW_RESEARCH_ROOT`, defaulting to the production checkout. Set that variable to run a skill against a different checkout — never edit the path here. Two copies of this repo once drifted apart because each one rewrote these literals locally.

## When to Run

- **Weekly cron** (Saturday): Full pipeline — create a new news notebook, add sources from news-queue.json, generate a deep-dive podcast, and hand off to digest-deliver. This is the primary flow.
- **On demand** (`/generate news`): Does NOT run this skill. Instead, TOOLS.md handles it via the news-podcast pipeline: finds the latest existing news notebook (using `scripts/fetch_latest.py --category news`) and generates a new podcast over its existing sources. No new notebook is created.

## Execution Steps

### Step 1: Read the news queue

Read `data/news-queue.json` and collect all articles with `status: "queued"` (not yet weekly-digested). Articles with `status: "daily-digested"` have already been included in the daily feed but should still be included in the weekly roundup — treat them as eligible.

If no queued articles, respond: "No news articles in queue. Subscribe to newsletters first with /subscribe."

### Step 2: Fetch article content (optional enrichment)

For the top articles, optionally use `trafilatura` to extract content:

```bash
$PROJECT_ROOT/.venv/bin/python3 -c "
import trafilatura
result = trafilatura.fetch_url('URL_HERE')
text = trafilatura.extract(result)
print(text[:500] if text else 'No content extracted')
"
```

This is optional — the briefing can be created from titles and URLs alone if content extraction is slow.

### Step 3: Create a structured briefing note

Using the queued articles, generate a companion note in this format:

```markdown
# AI News Briefing — Week of {date}

## Top Story
[Title of the most important article]
[2-3 sentence summary with context]
Source: [URL]

## Research Highlights
1. **[Paper Title]** — [One-line summary]. Source: [URL]
2. **[Paper Title]** — [One-line summary]. Source: [URL]

## Industry & Product News
- [Headline] — [One sentence]. Source: [URL]
- [Headline] — [One sentence]. Source: [URL]

## Open Source & Tools
- **[Repo/Tool]** — [What it does]. Source: [URL]

## Quick Hits
- [One-liner with source]
- [One-liner with source]

---
Sources: {count} articles from {count} newsletters
Period: {date range}
```

### Step 3.5: Ensure NotebookLM auth

Before any `nlm` commands, verify authentication is valid:

```bash
bash $PROJECT_ROOT/scripts/nlm_ensure_auth.sh
```

If this exits with code 1, the script has already sent a Telegram alert. **Do NOT stop the pipeline.** Skip Steps 4-7 (notebook creation, source addition, podcast generation). Still update article statuses in Step 8 to `"daily-digested"` (not `"digested"` since the notebook wasn't created) and proceed to Step 9. The weekly news digest can be retried later with `openclaw cron run weekly-research-digest`.

### Step 4: Create NotebookLM notebook

```bash
~/.local/bin/nlm notebook create "AI News - Week of {date}"
~/.local/bin/nlm alias set news-{date} {notebook-id}
```

### Step 5: Add sources to notebook

Add the top articles as URL sources (max ~15 per notebook), with retry on failure:
```bash
bash $PROJECT_ROOT/scripts/retry.sh 2 ~/.local/bin/nlm source add news-{date} --url "{article_url}" --wait
```
If a source still fails after retries, log it and continue with remaining sources.

Add the structured briefing note as a text source:
```bash
bash $PROJECT_ROOT/scripts/retry.sh 2 ~/.local/bin/nlm source add news-{date} --text "{briefing_note}" --title "News Briefing Summary" --wait
```

### Step 6: Generate podcast

For **weekly roundup** (Saturday):
```bash
~/.local/bin/nlm audio create news-{date} --format deep_dive --length default --confirm
```

For **daily brief** (optional weekday):
```bash
~/.local/bin/nlm audio create news-{date} --format brief --length short --confirm
```

### Step 7: Poll for completion

```bash
~/.local/bin/nlm studio status news-{date}
```

Repeat until generation is complete. This may take several minutes.

### Step 8: Update data files

Mark processed articles in `data/news-queue.json`:
```json
{
  "status": "digested",
  "digest_id": "news-{date}"
}
```

Record the notebook in `data/notebooks.json` with:
```json
{
  "id": "{notebook-id}",
  "alias": "news-{date}",
  "category": "news",
  "category_name": "AI News Digest",
  "created_at": "{timestamp}",
  "source_count": {count},
  "has_podcast": true,
  "has_report": false,
  "status": "complete",
  "run_id": "{run-id}"
}
```

### Step 9: Hand off to digest-deliver

The digest-deliver skill handles downloading the podcast MP3 and sending it via Telegram.

## Podcast Format

| Schedule | NotebookLM Format | Length | Style |
|----------|------------------|--------|-------|
| Weekly (Saturday) | `deep_dive` | 10-20 min | Two hosts discussing the week's AI news |

(Daily feeds are handled by the `daily-digest` skill with its own `deep_dive` / `long` podcast.)

## Data Files

- **Input**: `data/news-queue.json` — queued news articles
- **Output**: `data/notebooks.json` — notebook registry (append)

## Notes

- Prioritize articles by recency (newest first) and source diversity (spread across newsletters)
- If there are fewer than 3 queued articles, skip generation and respond: "Not enough news articles for a digest (need at least 3, have {count})."
- News notebooks are separate from research notebooks — they go under the "news" category
- The weekly news podcast and weekly research podcast are generated independently and delivered as separate Telegram messages

Note: Use full path `~/.local/bin/nlm` for all NotebookLM CLI commands (gateway doesn't source ~/.zshrc).
