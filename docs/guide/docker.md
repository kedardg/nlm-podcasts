# Docker Deployment

Run the OpenClaw Research Automation System in Docker for portable, reproducible deployment.

## Prerequisites

- Docker and Docker Compose v2
- Host machine configured with:
  - `~/.openclaw/openclaw.json` (Telegram bot token + OpenAI API key)
  - `~/.config/openclaw-research/email-credentials.json` (Gmail IMAP credentials)

## Quick Start

```bash
cd /path/to/openclaw-research

# 1. Create .env
cp .env.example .env
# Edit .env — fill in OPENAI_API_KEY and TELEGRAM_CHAT_ID

# 2. Create data directories (bind-mounted, persist on host)
mkdir -p data data/daily-feed rag/chroma_db

# 3. Build and start
docker compose build
docker compose up -d
```

## Image Contents

Multi-stage build on `python:3.11-slim-bookworm`:

| Component | Version | Purpose |
|-----------|---------|---------|
| Node.js | 22.x | OpenClaw runtime |
| Python | 3.11 | Scripts, RAG, PDF generation |
| openclaw | latest | Gateway + daemon + CLI |
| nlm | latest | NotebookLM CLI |
| Chromium | Debian package | NLM cookie-based auth |
| Pandoc | Debian package | Report format conversion |
| FFmpeg | Debian package | Podcast compression |
| Pango | Debian package | WeasyPrint PDF rendering |

Python packages installed in `/app/.venv`: chromadb, openai, weasyprint, beautifulsoup4, trafilatura, imapclient, markdown-it-py.

## Volume Mounts

| Host Path | Container Path | Type | Purpose |
|-----------|---------------|------|---------|
| `./data` | `/app/data` | bind | Sources, notebooks, news queue, daily feeds |
| `./rag/chroma_db` | `/app/rag/chroma_db` | bind | ChromaDB vector store |
| `openclaw-home` | `/root/.openclaw` | named | OpenClaw sessions + state |
| `nlm-auth` | `/root/.notebooklm-mcp` | named | NotebookLM auth tokens |
| `~/.openclaw/openclaw.json` | `/root/.openclaw/openclaw.json` | bind (ro) | Gateway config (bot token, API key) |
| `~/.config/openclaw-research/` | `/root/.config/openclaw-research/` | bind (ro) | Email credentials |

**Bind mounts** (`./data`, `./rag/chroma_db`) are host-editable and persist across container restarts. **Named volumes** are managed by Docker.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes | — | OpenAI API key for LLM + RAG embeddings |
| `TELEGRAM_CHAT_ID` | Yes | — | Telegram chat ID for alerts and delivery |
| `PROJECT_ROOT` | Auto | `/app` | Set automatically in container |
| `CHROME_PATH` | Auto | `/usr/bin/chromium` | Set automatically in container |

## macOS vs Docker

| Aspect | macOS (native) | Docker |
|--------|----------------|--------|
| WeasyPrint | Needs `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` | Works out of the box |
| Chrome | System Chrome or `brew install chromium` | `/usr/bin/chromium` (Debian) |
| FFmpeg | `/opt/homebrew/bin/ffmpeg` | `/usr/bin/ffmpeg` |
| Pango | `brew install pango` | Pre-installed via apt |
| nlm auth | `nlm login` (opens browser) | `docker compose exec openclaw-research nlm login` |
| Data files | Direct filesystem access | Bind-mounted from host |

## Common Operations

### Manage the service

```bash
docker compose up -d          # Start in background
docker compose down           # Stop
docker compose logs -f        # Follow logs
docker compose restart        # Restart
```

### Shell into container

```bash
docker compose exec openclaw-research bash
```

### Run scripts

```bash
# List queued sources
docker compose run --rm openclaw-research /app/.venv/bin/python3 /app/scripts/list_queued.py

# Run data triage
docker compose run --rm openclaw-research /app/.venv/bin/python3 /app/data_triage.py --dry-run

# Generate daily feed
docker compose run --rm openclaw-research /app/.venv/bin/python3 /app/daily_feed_generator.py --dry-run
```

### NotebookLM Authentication

NLM uses cookie-based auth that expires after ~20 minutes of inactivity. The pipeline auto-refreshes via `nlm_ensure_auth.sh`, but for initial setup or manual re-auth:

```bash
# Initial login
docker compose exec openclaw-research nlm login

# Check auth status
docker compose exec openclaw-research nlm login --check

# For managed browser auth via OpenClaw
docker compose exec openclaw-research nlm login --provider openclaw --cdp-url http://127.0.0.1:18800
```

### Rebuild after code changes

```bash
docker compose build
docker compose up -d
```

## Data Persistence

- **`./data/`** — All JSON state files (sources, notebooks, news queue, etc.) and daily feed artifacts live here. Survives container restarts and rebuilds.
- **`./rag/chroma_db/`** — ChromaDB vector store. Same persistence guarantees.
- **Named volumes** (`openclaw-home`, `nlm-auth`) — Managed by Docker. To reset, run `docker compose down -v`.

## Troubleshooting

### Container won't start

Check if host config files exist:
```bash
ls -la ~/.openclaw/openclaw.json
ls -la ~/.config/openclaw-research/email-credentials.json
```

If either is missing, the read-only bind mount will fail. Create the files first (see [setup.md](setup.md)).

### openclaw daemon not responding

```bash
docker compose exec openclaw-research openclaw daemon status
docker compose exec openclaw-research openclaw health --json
docker compose restart
```

### NLM auth fails in container

Chromium in the container runs headless. If `nlm login` fails:
1. Try `nlm login --provider openclaw --cdp-url http://127.0.0.1:18800` (managed browser)
2. Or authenticate on the host and copy tokens: `docker compose cp ~/.notebooklm-mcp/. openclaw-research:/root/.notebooklm-mcp/`

### Pipeline can't send to Telegram

Verify `TELEGRAM_CHAT_ID` is set in `.env` and the bot token is correct in `~/.openclaw/openclaw.json`.
