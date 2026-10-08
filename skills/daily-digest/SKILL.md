# Daily Digest Skill

You are the **daily-digest** agent for the OpenClaw Research Automation System. You collect all items received today, generate a polished PDF report, create a NotebookLM podcast, and deliver both via Telegram.

## Environment

Before running any commands, source the project environment:

```bash
source /Users/kedar/projects/openclaw-research/config/env.sh
```

This auto-detects `PROJECT_ROOT` and loads all variables from `.env` (including `TELEGRAM_CHAT_ID`, `OPENAI_API_KEY`). If `PROJECT_ROOT` is not already set, `config/env.sh` detects it from its own file location.

## When to Run

- **Daily cron**: 6 PM ET, every day (Monday–Sunday)
- **On demand**: When user sends `/generate daily`

## Data Files

- **Sources**: `$PROJECT_ROOT/data/sources.json`
- **News Queue**: `$PROJECT_ROOT/data/news-queue.json`
- **Categories**: `$PROJECT_ROOT/data/categories.json`
- **Output directory**: `$PROJECT_ROOT/data/daily-feed/`

## Execution Steps

### Step 0: Fetch fresh newsletter emails

Pull any new newsletter emails that arrived since the last fetch:

```bash
$PROJECT_ROOT/.venv/bin/python3 $PROJECT_ROOT/src/newsletter_parser.py
```

This extracts links from new emails and routes them to `sources.json` (research/github/youtube) and `news-queue.json` (news/blog). Without this step, the daily feed only sees items already saved from previous runs.

### Step 1: Run the daily feed generator

```bash
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib $PROJECT_ROOT/.venv/bin/python3 $PROJECT_ROOT/src/daily_feed_generator.py --pdf-only
```

This script:
1. Collects all items from today's date across `sources.json` and `news-queue.json`
2. Classifies each into one of 4 daily sections (AI Research, Tech, Development, News) using `categories.json`
3. Generates a markdown briefing
4. Converts to a styled PDF via WeasyPrint

**Output files** (in `data/daily-feed/`):
- `YYYY-MM-DD.md` — markdown briefing
- `YYYY-MM-DD.pdf` — styled PDF report

If there are no items for today, respond: "No items received today. Nothing to digest." and stop.

For a specific date, add `--date YYYY-MM-DD`. For a dry run preview, add `--dry-run`.

### Step 1.5: Ensure NotebookLM auth

Before any `nlm` commands, verify authentication is valid:

```bash
bash $PROJECT_ROOT/scripts/nlm_ensure_auth.sh
```

If this exits with code 1, reply "NotebookLM authentication failed. Please run: ~/.local/bin/nlm login" and stop.

### Step 2: Create NotebookLM notebook

```bash
~/.local/bin/nlm notebook create "Daily Feed — YYYY-MM-DD"
~/.local/bin/nlm alias set daily-YYYY-MM-DD {notebook-id}
```

### Step 3: Add sources to notebook

Add the top articles as URL sources (prioritize AI Research first, then Tech, Development, News). Maximum 15 sources per notebook:

```bash
~/.local/bin/nlm source add daily-YYYY-MM-DD --url "{url}" --wait
```

Repeat for each top article URL.

Then add the briefing as a text source:

```bash
~/.local/bin/nlm source add daily-YYYY-MM-DD --text "{briefing_markdown}" --title "Daily Feed Briefing" --wait
```

Read the briefing from `data/daily-feed/YYYY-MM-DD.md`.

### Step 4: Generate report and podcast

Generate both a custom report and a podcast from the notebook. These can run concurrently — start both, then poll for completion. Both use custom prompts from `config/prompts/` for technical depth.

**Report** (custom prompt from `config/prompts/daily-report-prompt-v3.txt`):
```bash
bash $PROJECT_ROOT/scripts/nlm_create_report.sh daily-YYYY-MM-DD $PROJECT_ROOT/config/prompts/daily-report-prompt-v3.txt
```

**Podcast** (custom focus from `config/prompts/daily-podcast-focus.txt`):
```bash
~/.local/bin/nlm audio create daily-YYYY-MM-DD \
  --format deep_dive \
  --length long \
  --focus "$(cat $PROJECT_ROOT/config/prompts/daily-podcast-focus.txt)" \
  --confirm
```

### Step 5: Poll for completion

```bash
~/.local/bin/nlm studio status daily-YYYY-MM-DD
```

Repeat every 30 seconds until both report and podcast generation complete. Maximum wait: 15 minutes.

### Step 6: Download report and podcast

**Download report:**
```bash
~/.local/bin/nlm download report daily-YYYY-MM-DD --output $PROJECT_ROOT/data/daily-feed/YYYY-MM-DD-report.md
```

**Enhance and convert report to PDF** (replaces the deterministic PDF from Step 1):
```bash
$PROJECT_ROOT/.venv/bin/python3 $PROJECT_ROOT/src/pipeline/enhance_report.py \
  --input $PROJECT_ROOT/data/daily-feed/YYYY-MM-DD-report.md
```
```bash
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib $PROJECT_ROOT/.venv/bin/python3 $PROJECT_ROOT/src/lib/pdf_renderer.py \
  --input $PROJECT_ROOT/data/daily-feed/YYYY-MM-DD-report.md \
  --output $PROJECT_ROOT/data/daily-feed/YYYY-MM-DD-report.pdf
```

If NLM report download fails, fall back to the deterministic PDF from Step 1.

**Download and compress podcast:**
```bash
$PROJECT_ROOT/.venv/bin/python3 $PROJECT_ROOT/src/nlm/nlm_download_audio.py daily-YYYY-MM-DD --output $PROJECT_ROOT/data/daily-feed/YYYY-MM-DD-podcast-raw.mp4
```
Note: `nlm download audio` is broken (httpx TooManyRedirects). Use `nlm_download_audio.py` which extracts Chrome CDP cookies and follows the redirect chain. Requires Chrome with `--remote-debugging-port`.

Compress for Telegram delivery (48kbps mono — fits under 16MB limit, clear for spoken word):
```bash
/opt/homebrew/bin/ffmpeg -y -i $PROJECT_ROOT/data/daily-feed/YYYY-MM-DD-podcast-raw.mp4 -ac 1 -b:a 48k -ar 22050 $PROJECT_ROOT/data/daily-feed/YYYY-MM-DD-podcast.mp3
```

### Step 6.5: Index report summary into RAG

Index the NLM report's TL;DR for notebook routing:

```bash
$PROJECT_ROOT/.venv/bin/python3 $PROJECT_ROOT/src/pipeline/index_notebook.py \
  --notebook-id {notebook-id} \
  --report-file $PROJECT_ROOT/data/daily-feed/YYYY-MM-DD-report.md
```

If this fails, log the error but continue delivery.

### Step 7: Get share link

```bash
~/.local/bin/nlm share public {notebook-id}
```

### Step 8: Deliver via Telegram

Send three messages to the user:

**Message 1 — Summary:**
Reply with the summary text directly (OpenClaw auto-routes text replies to Telegram):
```
📰 Daily Tech Feed — {weekday}, {month} {day}

📊 {total} items across {section_count} sections
📚 Notebook: {share_link}
```

**Message 2 — PDF report:**
Copy to workspace first (media must be under `~/.openclaw/workspace/`), then send. Prefer NLM report, fall back to deterministic PDF:
```bash
cp $PROJECT_ROOT/data/daily-feed/YYYY-MM-DD-report.pdf ~/.openclaw/workspace/YYYY-MM-DD-report.pdf 2>/dev/null || cp $PROJECT_ROOT/data/daily-feed/YYYY-MM-DD.pdf ~/.openclaw/workspace/YYYY-MM-DD-report.pdf
openclaw message send --channel telegram --target $TELEGRAM_CHAT_ID --media ~/.openclaw/workspace/YYYY-MM-DD-report.pdf --message "📄 Daily Feed Report — YYYY-MM-DD"
```

**Message 3 — Podcast:**
Copy to workspace first, then send:
```bash
cp $PROJECT_ROOT/data/daily-feed/YYYY-MM-DD-podcast.mp3 ~/.openclaw/workspace/YYYY-MM-DD-podcast.mp3
openclaw message send --channel telegram --target $TELEGRAM_CHAT_ID --media ~/.openclaw/workspace/YYYY-MM-DD-podcast.mp3 --message "🎙️ Daily Feed Podcast — YYYY-MM-DD"
```

### Step 9: Mark items as processed

The `daily_feed_generator.py` script already marks news-queue items with `status: "daily-digested"`. Sources in `sources.json` remain `status: "queued"` so they are still available for the weekly deep-dive pipeline.

### Step 10: Record in notebooks.json

Add the notebook entry:
```json
{
  "id": "{notebook-id}",
  "alias": "daily-YYYY-MM-DD",
  "category": "daily",
  "category_name": "Daily Feed",
  "created_at": "{timestamp}",
  "source_count": {count},
  "source_ids": [],
  "has_podcast": true,
  "has_report": true,
  "status": "complete",
  "run_id": "daily_{YYYYMMDD}"
}
```

## Error Handling

- **No items today**: Send a brief message: "No items received today — no daily digest to generate." and stop.
- **PDF generation fails**: Continue with markdown only. Attach the `.md` file instead of the PDF.
- **NotebookLM fails**: Send the PDF report via Telegram with a note that the podcast is unavailable. Include the markdown briefing text as a message.
- **Podcast generation timeout**: After 15 minutes, send the PDF and notebook link, noting that the podcast is still generating. The user can check later.

## Daily Sections

The briefing groups items into 4 sections based on their category's `daily_section` field:

| Section | Categories | Content |
|---------|-----------|---------|
| **AI Research** | ml, nlp, cv, robotics | Papers, preprints, technical posts |
| **Tech** | tech | Industry news, products, funding |
| **Development** | oss, devx | Repos, tools, tutorials, guides |
| **News** | ai-safety, general | Policy, regulation, everything else |

## Relationship with Weekly Pipeline

- The daily feed processes ALL items (research + news + blogs).
- News-queue items are marked `daily-digested` and are NOT re-processed by the weekly news-digest.
- Sources.json items REMAIN `queued` — the weekly content-processor still picks them up for deep-dive notebooks.
- The daily feed is a curated overview; the weekly digest is a deep research dive.

Note: Use full path `~/.local/bin/nlm` for all NotebookLM CLI commands (gateway doesn't source ~/.zshrc).
