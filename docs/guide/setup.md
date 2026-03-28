# Setup Guidelines & Troubleshooting

## Prerequisites

| Dependency | Version | Purpose | Install |
|---|---|---|---|
| Node.js | 22+ | OpenClaw runtime | `nvm install 22` or `brew install node` |
| Python | 3.10+ | Scripts, RAG, PDF generation | System or `pyenv` |
| `uv` | Latest | Python package management | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Chrome/Chromium | Any | NotebookLM cookie-based auth | System browser |
| `pango` | Latest | WeasyPrint PDF rendering | `brew install pango` |
| `pandoc` | Latest | Report format conversion (NLM download) | `brew install pandoc` |
| `ffmpeg` | Latest | Podcast compression for Telegram (48kbps mono) | `brew install ffmpeg` |
| OpenAI API key | — | LLM inference + RAG embeddings | openai.com |
| Telegram bot token | — | Bot ↔ user communication | @BotFather |
| Gmail App Password | — | Newsletter IMAP access | myaccount.google.com |

## Installation Order

### 1. OpenClaw Platform

```bash
npm install -g openclaw@latest
openclaw onboard --install-daemon
```

This installs the gateway, daemon, and CLI. The onboard wizard walks through:
- Telegram bot token configuration
- Default model selection
- Workspace initialization at `~/.openclaw/`

### 2. NotebookLM CLI

```bash
uv tool install notebooklm-mcp-cli
~/.local/bin/nlm login    # Opens browser for Google sign-in
```

Verify: `~/.local/bin/nlm --version`

### 3. X/Twitter API (ClawHub Skill)

```bash
npx clawhub@latest install aisa-twitter-api
```

### 4. Python Environment

```bash
cd $PROJECT_ROOT
uv venv .venv
uv pip install chromadb openai markdown-it-py weasyprint beautifulsoup4 trafilatura imapclient
```

### 5. Environment Variables

```bash
cp .env.example .env
# Edit .env — fill in OPENAI_API_KEY and TELEGRAM_CHAT_ID
```

`.env` is the single config file for all user settings. Shell scripts load it automatically via `config/env.sh`, which also auto-detects `PROJECT_ROOT` from the script's location (works in both bash and zsh). You do not need to export variables in your shell profile.

### 6. Email Credentials

```bash
mkdir -p ~/.config/openclaw-research
cat > ~/.config/openclaw-research/email-credentials.json << 'EOF'
{
  "email": "your-email+ai@gmail.com",
  "app_password": "xxxx xxxx xxxx xxxx",
  "imap_host": "imap.gmail.com",
  "imap_port": 993,
  "plus_alias": "ai"
}
EOF
```

Gmail App Password: Enable 2FA → myaccount.google.com/apppasswords → select "Mail".

### 7. OpenClaw Configuration

Edit `~/.openclaw/openclaw.json`:
- Telegram bot token (under channels)
- OpenAI API key (under model providers)
- See `config/openclaw-channel.json` for template

Reliability settings (apply via `openclaw config set`):
```bash
openclaw config set commands.restart true
openclaw config set session.reset.mode idle
openclaw config set session.reset.idleMinutes 60
openclaw config set session.maintenance.mode enforce
```

### 7.5. Gateway Health Plugin

The gateway-health plugin at `~/.openclaw/extensions/gateway-health/` provides automatic memory monitoring, NLM auth rotation on startup, and pre-cron auth checks. It's installed by default in the extensions directory.

Verify it's loaded: `openclaw health --json` should show `[gateway-health] Plugin loaded` in output.

Config: Edit `~/.openclaw/extensions/gateway-health/openclaw.plugin.json` to tune `maxRssMb` (default 800), `growthThresholdMb` (default 200), probe interval, and NLM auth hooks.

### 8. Workspace Files

Ensure symlinks/files exist in `~/.openclaw/workspace/`:
```bash
ln -sf $PROJECT_ROOT/CLAUDE.md ~/.openclaw/workspace/RESEARCH.md
```

TOOLS.md must be present and up-to-date. This is the most critical file — the LLM reads it to know how to execute pipeline commands.

### 9. Cron Jobs

```bash
# Daily tech feed (6 PM ET, every day)
openclaw cron add \
  --name "daily-tech-feed" \
  --cron "0 18 * * *" \
  --tz "America/New_York" \
  --session isolated \
  --message "Execute the daily-digest skill. Follow every step in the Daily Digest Pipeline section of TOOLS.md — Step 0 through Step 9. Do not skip NLM steps. If a step fails, continue to the next step. Reply with a summary of what was collected and delivered." \
  --announce \
  --channel telegram

# Weekly research digest (Saturday 9 AM ET)
openclaw cron add \
  --name "weekly-research-digest" \
  --cron "0 9 * * 6" \
  --tz "America/New_York" \
  --session isolated \
  --message "Run the weekly research digest: 1) Execute newsletter-monitor. 2) Execute x-monitor. 3) Run data triage: .venv/bin/python3 data_triage.py --auto. 4) Execute content-processor. 5) Execute notebooklm-orchestrator. 6) Execute news-digest for weekly roundup. 7) Execute digest-deliver." \
  --announce \
  --channel telegram
```

### 10. Custom Prompts for Podcast & Report Generation

NotebookLM supports custom prompts (up to 10,000 characters) for steering both audio overviews and report generation. The system uses four prompt files:

| File | Used by | Purpose |
|------|---------|---------|
| `config/prompts/daily-podcast-focus.txt` | Daily audio `--focus` | Daily briefing — cross-source synthesis, technical implications, thematic progression |
| `config/prompts/weekly-podcast-focus.txt` | Weekly audio `--focus` | Deep research — architecture/data/innovation emphasis, critical peer review, multi-paper synthesis |
| `config/prompts/daily-report-prompt-v3.txt` | Daily report `--prompt` | Sectioned briefing (AI Research, Tech, Development, News), v3 markdown format |
| `templates/report-prompt.txt` | Weekly report `--prompt` | Technical research briefing with cross-source synthesis, actionable insights |

Both podcast and report prompts emphasize:
- **Multi-source synthesis** — treat sources as a connected body of work, not isolated items
- **Thematic organization** — group by topic and progression, not source-by-source
- **Technical depth** — architecture, data pipelines, compute trade-offs
- **No filler** — short intros, no ethical/societal commentary, assume ML expertise

To customize, edit the prompt files directly. Changes take effect on the next pipeline run. The `nlm_create_report.sh` script reads the prompt file at runtime:
```bash
# Weekly report (default prompt)
bash scripts/nlm_create_report.sh <notebook-id>

# Daily report (custom prompt)
bash scripts/nlm_create_report.sh <notebook-id> config/prompts/daily-report-prompt.txt
```

### 11. Verify

```bash
# Check everything
openclaw doctor --deep --yes
openclaw health --json
openclaw channels status --probe
~/.local/bin/nlm doctor --verbose
python3 url_utils.py  # Self-test
```

---

## Docker Deployment (Alternative)

If you prefer running in Docker instead of native macOS:

### Prerequisites

- Docker and Docker Compose (v2)
- `~/.openclaw/openclaw.json` configured with Telegram bot token and OpenAI API key
- `~/.config/openclaw-research/email-credentials.json` (for newsletter monitoring)

### Steps

```bash
cd /path/to/openclaw-research

# 1. Create .env from template
cp .env.example .env
# Edit .env — fill in OPENAI_API_KEY and TELEGRAM_CHAT_ID

# 2. Ensure data directories exist
mkdir -p data data/daily-feed rag/chroma_db

# 3. Build and start
docker compose build
docker compose up -d

# 4. Verify
docker compose run --rm openclaw-research /app/.venv/bin/python3 -c \
  "import chromadb; import openai; import weasyprint; print('All imports OK')"
```

### Data Persistence

- `./data/` and `./rag/chroma_db/` are bind-mounted — data persists on host and is editable
- OpenClaw sessions live in the `openclaw-home` named volume
- NLM auth tokens live in the `nlm-auth` named volume

### NLM Authentication in Docker

```bash
# Initial login (opens headless browser)
docker compose exec openclaw-research nlm login

# Check auth status
docker compose exec openclaw-research nlm login --check
```

---

## Common Problems & Solutions

### Problem: "Pipeline not connected" / LLM gives wrong pipeline steps

**Cause**: TOOLS.md has stale or incorrect instructions.

**Fix**: Check `~/.openclaw/workspace/TOOLS.md`. It must reference the wrapper scripts (`scripts/rag_chat.py`, `scripts/fetch_and_deliver.sh`) and the process pipeline. This is the single source of truth.

### Problem: LLM repeats old wrong answers

**Cause**: Stale session cache. The LLM's context includes previous wrong responses.

**Fix**:
```bash
echo '{}' > ~/.openclaw/agents/main/sessions/sessions.json
rm ~/.openclaw/agents/main/sessions/*.jsonl
openclaw gateway restart
```

### Problem: "No queued sources to process" on `/generate report`

**Cause**: The old pipeline tried to create new notebooks from queued sources. After the redesign, `/generate report` uses the fetch-latest pipeline which downloads existing artifacts.

**Fix**: Ensure TOOLS.md references `scripts/fetch_and_deliver.sh` (not the old multi-step flow). If no notebooks exist at all, run `/process` first or wait for the weekly cron.

### Problem: NotebookLM auth keeps expiring

**Cause**: Cookie-based auth expires after ~20 minutes of inactivity.

**How it's handled**: `scripts/nlm_ensure_auth.sh` retries 3 times with exponential backoff (5s, 15s, 45s). If all retries fail, it sends a Telegram alert with fix instructions. The pipeline **does not stop** — it skips NLM-dependent steps and continues with non-NLM work.

**To fix after alert**:
1. Run: `~/.local/bin/nlm login`
2. Re-trigger: `openclaw cron run weekly-research-digest`

For managed browser auth via OpenClaw:
```bash
nlm login --provider openclaw --cdp-url http://127.0.0.1:18800
```

### Problem: Files not delivered to Telegram (just path shown as text)

**Cause**: The LLM printed the file path instead of calling `openclaw message send --media`.

**Fix**: TOOLS.md must include explicit `openclaw message send --media` commands after every artifact download step. The `--target` is the Telegram chat ID (`$TELEGRAM_CHAT_ID` from `.env`). Files must be under `~/.openclaw/workspace/`.

### Problem: `nlm` command not found in pipeline

**Cause**: The gateway process doesn't source `~/.zshrc`, so `~/.local/bin` isn't in PATH.

**Fix**: Always use full path `~/.local/bin/nlm` in TOOLS.md and all scripts.

### Problem: WeasyPrint / PDF generation fails

**Cause**: Missing `pango` library or `DYLD_FALLBACK_LIBRARY_PATH` not set.

**Fix**:
```bash
brew install pango
# All PDF commands must be prefixed with:
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python3 pdf_renderer.py ...
```

### Problem: Podcast too large for Telegram (>16MB)

**Cause**: NLM generates podcasts at ~256kbps stereo (~70MB for 38 min with `--length long`).

**Fix**: The pipeline compresses via ffmpeg (48kbps mono at 22kHz → ~13MB for 38 min):
```bash
/opt/homebrew/bin/ffmpeg -y -i podcast-raw.mp4 -ac 1 -b:a 48k -ar 22050 podcast.mp3
```
If ffmpeg isn't installed: `brew install ffmpeg`

### Problem: `nlm download report` fails with "command not found: pandoc"

**Cause**: The NLM CLI uses pandoc for report format conversion.

**Fix**: `brew install pandoc`

### Problem: RAG queries return empty results

**Cause**: ChromaDB not populated, or `OPENAI_API_KEY` not set.

**Fix**:
```bash
# Check if ChromaDB exists
ls rag/chroma_db/

# Re-index all notebooks
.venv/bin/python3 scripts/index_notebook.py --all

# Ensure API key
echo $OPENAI_API_KEY
```

### Problem: Notebook dates are wrong (LLM picks random dates)

**Cause**: The LLM generates date strings and may hallucinate.

**Fix**: Compute dates deterministically in scripts, not in LLM prompts. Use `$(date +%Y-%m-%d)` in bash or `datetime.now()` in Python. Never ask the LLM to generate a date string for notebook names.

### Problem: Newsletter parser finds 0 emails

**Cause**: Wrong IMAP folder, expired app password, or plus_alias mismatch.

**Fix**:
```bash
# Dry run with verbose output
.venv/bin/python3 newsletter_parser.py --check

# Check specific date range
.venv/bin/python3 newsletter_parser.py --check --since 2026-02-01
```

Verify credentials in `~/.config/openclaw-research/email-credentials.json`. Ensure the `plus_alias` matches the email alias you subscribe newsletters to.

### Problem: Gateway unresponsive or high memory usage

**Cause**: Node.js memory leak over extended uptime (days/weeks). The gateway-health plugin at `~/.openclaw/extensions/gateway-health/` monitors this automatically.

**How it's handled**: The plugin probes RSS every 10 minutes. If RSS exceeds the hard limit (800MB) or grows >200MB from startup baseline, it calls `process.exit(1)` and launchd auto-restarts a clean process.

**If the plugin causes a restart loop** (RSS exceeds limit on fresh startup):
1. Raise `maxRssMb` in `~/.openclaw/extensions/gateway-health/openclaw.plugin.json`
2. Or temporarily disable: set `"nlmAuthOnStartup": false` in the plugin config
3. Restart: `openclaw daemon restart`

**Manual restart**: `launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway`

### Problem: Telegram bot not responding at all

**Fix** (in order):
1. `openclaw daemon status` — is it running?
2. `openclaw health --json` — is the gateway healthy?
3. Check bot token in `~/.openclaw/openclaw.json`
4. `openclaw channels status --probe` — channel connectivity
5. `openclaw daemon restart` — restart everything

### Problem: X/Twitter scraping returns nothing

**Fix**:
1. Check `aisa-twitter-api` skill is installed
2. `python3 router.py "list accounts"` — are accounts active?
3. Twitter API rate limits are strict — wait and retry

---

## Module Dependencies

```
                                url_utils.py
                              (URL normalization)
                           /         |         \
                          /          |          \
                  router.py    newsletter_parser.py    data_triage.py
               (Telegram cmds)  (Email ingestion)    (Pre-processing)
                     |                |                    |
                     └────────┬───────┘                    |
                              |                            |
                    data/sources.json ◄────────────────────┘
                    data/news-queue.json ◄──────────────────┘
                              |
              ┌───────────────┼───────────────┐
              |               |               |
       daily_feed_generator.py  content-processor  news-digest
         (deterministic PDF)  |               |
              |         notebooklm-orchestrator
              |               |
              ├── nlm notebook ┌──────┴──────┐
              |    + sources   |             |
              |         nlm report create  nlm audio create
              |          (custom prompt)   (custom focus)
              |               |             |
              |        enhance_report.py  ffmpeg (48kbps mono)
              |               |             |
              |        pdf_renderer.py      |
              |               |             |
              └───────────────┴─────────────┘
                              |
                        digest-deliver
                              |
                 ┌────────────┴────────────┐
                 |                         |
           index_notebook.py        openclaw message send
          (rag/embeddings.py)         (Telegram delivery)
           + report TL;DR
                 |
           rag/query.py ◄── chat-rag skill
```

## Data File Dependency Map

```
categories.json ──→ Used by: router.py, newsletter_parser.py, data_triage.py,
                              content-processor, daily_feed_generator.py,
                              rag/embeddings.py

sources.json ──→ Written by: router.py, newsletter_parser.py, x-monitor
                 Read by: data_triage.py, content-processor, enhance_report.py,
                          daily_feed_generator.py, fetch_latest.py,
                          rag/embeddings.py

news-queue.json ──→ Written by: newsletter_parser.py
                    Read by: data_triage.py, daily_feed_generator.py, news-digest

notebooks.json ──→ Written by: notebooklm-orchestrator, news-digest, daily-digest
                   Read by: fetch_latest.py, index_notebook.py, rag/query.py,
                            digest-deliver, router.py (status)

accounts.json ──→ Written by: router.py (track/untrack)
                  Read by: x-monitor, router.py (list/status)

newsletters.json ──→ Written by: router.py (subscribe), newsletter_parser.py
                     Read by: newsletter_parser.py, router.py (list)

triage-plan.json ──→ Written by: data_triage.py
                     Read by: content-processor, router.py (status triage)
```
