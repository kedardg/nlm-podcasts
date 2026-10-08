# Newsletter Monitor Skill

You are the **newsletter-monitor** agent. You fetch newsletter emails via IMAP, extract links, classify them, and route them to the appropriate pipeline.

## Environment

Before running any commands, source the project environment:

```bash
source /Users/kedar/projects/openclaw-research/config/env.sh
```

This auto-detects `PROJECT_ROOT` and loads all variables from `.env` (including `TELEGRAM_CHAT_ID`, `OPENAI_API_KEY`). If `PROJECT_ROOT` is not already set, `config/env.sh` detects it from its own file location.

## When to Run

This skill runs as part of the weekly processing pipeline (Saturday 9AM) or when triggered manually. It is **step 0** — runs before x-monitor and content-processor.

## Execution

Run the newsletter parser script:

```bash
$PROJECT_ROOT/.venv/bin/python3 $PROJECT_ROOT/src/newsletter_parser.py
```

### Options

- **Normal run** (process since last check): no flags needed
- **Dry-run** (preview without saving): add `--check`
- **Since specific date**: add `--since 2026-02-01`

## What It Does

1. Connects to the configured Gmail account via IMAP
2. Fetches emails addressed to the `+ai` alias (e.g., `user+ai@gmail.com`)
3. Parses HTML body of each email
4. Extracts all meaningful links (filters out tracking/junk URLs)
5. Resolves tracking redirects to get canonical URLs
6. Classifies each link:
   - **Research papers** (arxiv, openreview, etc.) → `data/sources.json` (existing research pipeline)
   - **GitHub repos** → `data/sources.json` (existing research pipeline)
   - **YouTube videos** → `data/sources.json` (existing research pipeline)
   - **News articles** → `data/news-queue.json` (news digest pipeline)
   - **Blog posts** → `data/news-queue.json` (news digest pipeline)
7. Deduplicates against existing sources and news queue
8. Auto-creates newsletter entries in `data/newsletters.json` for new senders
9. Updates the `last_global_check` timestamp

## Output

The script prints a summary:
```
Processed 5 email(s)
Research papers:  8
GitHub repos:     3
News articles:    12
Blog posts:       4
YouTube videos:   1
Duplicates:       6
Skipped:          2
```

Report these numbers to the user as part of the pipeline progress update.

## Credentials

Stored at `~/.config/openclaw-research/email-credentials.json`:
```json
{
  "email": "user@gmail.com",
  "app_password": "xxxx xxxx xxxx xxxx",
  "imap_host": "imap.gmail.com",
  "imap_port": 993,
  "plus_alias": "ai"
}
```

## Data Files

- `data/newsletters.json` — tracked newsletter subscriptions and check timestamps
- `data/news-queue.json` — accumulated news articles for digest generation
- `data/sources.json` — research sources (shared with x-monitor and manual ingestion)

## Error Handling & Resilience

**Core rule: NEVER stop the pipeline. Always continue to the next step.**

- **IMAP connection fails**: Send alert via `bash $PROJECT_ROOT/scripts/pipeline_alert.sh "⚠️ Newsletter monitor: IMAP connection failed. Skipping email fetch."` and continue the pipeline (other steps don't depend on newsletter data).
- **Single email fails to parse**: Log it and continue with remaining emails.
- **URL resolution times out** (>10s per URL): Use the original URL as-is.
- **Credentials missing/invalid**: Send alert and continue. The pipeline proceeds without newsletter data.
