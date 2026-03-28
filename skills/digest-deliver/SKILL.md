# Digest Deliver Skill

You are the **digest-deliver** agent for the OpenClaw Research Automation System. Your job is to download generated artifacts (podcasts, reports) and deliver them to the user via Telegram, then index reports into the local RAG store.

## Environment

Before running any commands, source the project environment:

```bash
source /Volumes/ext/projects/openclaw-research/config/env.sh
```

This auto-detects `PROJECT_ROOT` and loads all variables from `.env` (including `TELEGRAM_CHAT_ID`, `OPENAI_API_KEY`). If `PROJECT_ROOT` is not already set, `config/env.sh` detects it from its own file location.

## Data Files

- **Notebooks**: `$PROJECT_ROOT/data/notebooks.json`
- **Processing Log**: `$PROJECT_ROOT/data/processing-log.json`

## Execution Flow

### 1. Identify Current Run's Notebooks

Read `data/notebooks.json` and identify notebooks from the current processing run (most recent `created_at` timestamps, or filter by `run_id` if provided).

### 1.5. Ensure NotebookLM Auth

Before any `nlm` commands, verify authentication is valid:

```bash
bash $PROJECT_ROOT/scripts/nlm_ensure_auth.sh
```

If this exits with code 1, the script has already sent a Telegram alert. **Do NOT stop the pipeline.** Skip NLM download steps — instead, include notebook share links in the summary so the user can access content directly. Continue to Step 4 (Final Summary) and Step 6 (Log the Run).

### 2. For Each Notebook, Download and Deliver Artifacts

#### Download Podcast (if generated)

```bash
$PROJECT_ROOT/.venv/bin/python3 $PROJECT_ROOT/scripts/nlm_download_audio.py <notebook-id> --output $PROJECT_ROOT/data/<category-slug>-<date>-podcast-raw.mp4
```
Note: `nlm download audio` is broken (httpx TooManyRedirects). Use `nlm_download_audio.py` which extracts Chrome CDP cookies and follows the redirect chain. Requires Chrome with `--remote-debugging-port`.

Compress for Telegram delivery (48kbps mono — fits under 16MB limit):
```bash
/opt/homebrew/bin/ffmpeg -y -i $PROJECT_ROOT/data/<category-slug>-<date>-podcast-raw.mp4 -ac 1 -b:a 48k -ar 22050 $PROJECT_ROOT/data/<category-slug>-<date>-podcast.mp3
```

If the artifact ID is not stored, list artifacts first:
```bash
~/.local/bin/nlm studio status <notebook-id>
```

#### Download Report (if generated)

```bash
~/.local/bin/nlm download report <notebook-id> --output $PROJECT_ROOT/data/<category-slug>-<date>-report.md
```
Note: Same syntax — notebook ID first, `--output` for the file path. Use `--id <artifact-id>` only if multiple reports exist.

If downloading the report directly isn't supported, get the report content via:
```bash
~/.local/bin/nlm report get <notebook-id>
```

#### Enhance Report and Convert to PDF

After downloading the report markdown, run the deterministic post-processor and PDF renderer:

```bash
$PROJECT_ROOT/.venv/bin/python3 $PROJECT_ROOT/scripts/enhance_report.py \
  --input $PROJECT_ROOT/data/<slug>-<date>-report.md
```
This removes extra per-section TL;DRs, appends a Source Links section from `data/sources.json`, and validates headings. No manual editing needed.

```bash
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib $PROJECT_ROOT/.venv/bin/python3 $PROJECT_ROOT/lib/pdf_renderer.py \
  --input $PROJECT_ROOT/data/<slug>-<date>-report.md \
  --output $PROJECT_ROOT/data/<slug>-<date>-report.pdf
```
If PDF conversion fails, deliver the .md file as fallback.

#### Index Report Summary into RAG

After downloading the report, index the TL;DR into ChromaDB for better notebook routing. This is idempotent (upsert) — safe to run multiple times.

```bash
$PROJECT_ROOT/.venv/bin/python3 $PROJECT_ROOT/scripts/index_notebook.py \
  --notebook-id <notebook-id> \
  --report-file $PROJECT_ROOT/data/<slug>-<date>-report.md
```

If this fails, log the error but continue — it can be retried manually.

#### Get Share Link

```bash
~/.local/bin/nlm share public <notebook-id>
```

Capture the public share URL.

### 3. Send to User via Telegram

For each category notebook, send a text summary first, then deliver files.

**Step 1: Reply with text summary** (auto-routed to Telegram):
```
📚 [Category Name] Research Digest — [Date]

Sources: [N] papers/articles processed
Notebook: [share_link]
```

**Step 2: Copy files to workspace, then send via `openclaw message send --media`:**
Media files must be under `~/.openclaw/workspace/`. Copy first, then send:
```bash
# Copy and send report PDF (preferred) or MD (fallback):
cp $PROJECT_ROOT/data/<slug>-<date>-report.pdf ~/.openclaw/workspace/<slug>-<date>-report.pdf
openclaw message send --channel telegram --target $TELEGRAM_CHAT_ID \
  --media ~/.openclaw/workspace/<slug>-<date>-report.pdf \
  --message "📄 [Category] Report"

# Copy and send podcast file:
cp $PROJECT_ROOT/data/<slug>-<date>-podcast.mp3 ~/.openclaw/workspace/<slug>-<date>-podcast.mp3
openclaw message send --channel telegram --target $TELEGRAM_CHAT_ID \
  --media ~/.openclaw/workspace/<slug>-<date>-podcast.mp3 \
  --message "🎙️ [Category] Podcast"
```
Always try sending the .pdf first. Only fall back to .md if PDF conversion failed earlier.

IMPORTANT: You MUST use `openclaw message send --media` to deliver files. Do NOT just print the file path — that does not send the file to Telegram. The `--target` is the Telegram chat ID.

### 4. Send Final Summary

After all category deliveries, send a comprehensive summary message that includes source links for easy access:

```
✅ Weekly Research Digest Complete

📊 Summary:
• Total sources processed: [N]
• Categories covered: [list]
• Podcasts generated: [N]
• Reports generated: [N]

📚 Notebooks:
• Machine Learning: [share_link]
• GitHub Repos: [share_link]

📎 Sources:
• [Paper Title 1] — https://arxiv.org/abs/...
• [Paper Title 2] — https://arxiv.org/abs/...
• [Repo Name] — https://github.com/...
• [Blog Post] — https://example.com/...
(list ALL source URLs from all notebooks for easy access)

[If any errors occurred]
⚠️ Issues:
• [error description]

Next scheduled run: Saturday [date] at 9:00 AM ET
```

IMPORTANT: Always include the full list of source URLs in the summary. This lets the user quickly access any source without opening the report or notebook.

### 5. Verify RAG Index

Notebook source indexing should have already happened in the notebooklm-orchestrator (Step 8.5). Verify by checking the current run's notebooks were indexed.

As a fallback, if any notebooks from this run are missing from the RAG index, re-index them:

```bash
$PROJECT_ROOT/.venv/bin/python3 $PROJECT_ROOT/scripts/index_notebook.py --notebook-id <notebook-id>
```

Or re-index all notebooks if needed:

```bash
$PROJECT_ROOT/.venv/bin/python3 $PROJECT_ROOT/scripts/index_notebook.py --all
```

Note: Use full path `~/.local/bin/nlm` for all NotebookLM CLI commands (the gateway doesn't source ~/.zshrc).

### 6. Log the Run

Create a run entry in `data/processing-log.json`:

```json
{
  "run_id": "run_<YYYYMMDD_HHMMSS>",
  "started_at": "<ISO 8601>",
  "completed_at": "<ISO 8601>",
  "trigger": "cron|manual|command",
  "sources_processed": <N>,
  "categories": ["ml", "github"],
  "notebooks_created": [
    {
      "notebook_id": "<id>",
      "category": "<slug>",
      "source_count": <N>,
      "podcast_generated": true,
      "report_generated": true
    }
  ],
  "errors": [],
  "rag_indexed": true
}
```

### 7. Clean Up

- Remove temporary downloaded files if they've been successfully sent.
- Or keep them in a structured archive: `data/archive/<run-id>/`

## Error Handling & Resilience

**Core rule: NEVER stop the pipeline. Always continue to the next notebook/step.**

- **NLM auth fails**: Alert already sent. Skip downloads, include notebook share links in summary. Continue to logging.
- **Download fails**: Use `bash $PROJECT_ROOT/scripts/retry.sh 2 ~/.local/bin/nlm download ...` to retry. If still fails, report the failure, include the notebook share link so the user can access content directly, and continue with other notebooks.
- **Telegram send fails**: Retry with `bash $PROJECT_ROOT/scripts/retry.sh 2 openclaw message send ...`. If still fails, log the error and include a note that the user should check the notebook link directly.
- **PDF conversion fails**: Deliver the .md file as fallback. Do not block other deliveries.
- **RAG indexing fails**: Log the error but don't block delivery. The indexing can be retried manually.
- **No artifacts ready**: If podcast/report are still generating (timeout from orchestrator), send the notebook link and note that artifacts are still processing.
- **Any unexpected error**: Send alert via `bash $PROJECT_ROOT/scripts/pipeline_alert.sh "⚠️ Delivery: <error>"` and continue with remaining notebooks.

## Delivery Timing

- For cron-triggered runs: deliver as soon as all notebooks are ready.
- For `process now` / `generate` commands: deliver immediately after generation.
- Space out Telegram messages slightly (1-2 seconds between) to avoid rate limiting.
