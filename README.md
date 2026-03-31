# OpenClaw Research Automation System

An OpenClaw-native system for collecting, processing, and synthesizing research papers, articles, and links. Sources arrive via Telegram, get categorized and processed into NotebookLM notebooks with auto-generated podcasts and reports, then delivered back via Telegram. Includes a dual-layer RAG system for querying across all accumulated research.

## Features

- **Multi-source ingestion** — PDFs, URLs, text via Telegram; newsletters via Gmail IMAP; X/Twitter account monitoring
- **Automated pipelines** — Daily tech feed (6 PM ET) and weekly research digest (Saturday 9 AM ET)
- **NotebookLM integration** — Auto-generated podcasts, reports, quizzes, and mind maps
- **RAG search** — Query all research via ChromaDB-backed notebook routing
- **Telegram interface** — Slash commands for everything: `/generate`, `/process`, `/chat`, `/status`

## Architecture

```
User (Telegram)
  |
  +-- Send PDF / URL / text --> research-ingest --> data/sources.json
  +-- "track @user" ---------> research-ingest --> data/accounts.json
  +-- "chat <question>" -----> chat-rag --> ChromaDB + NotebookLM --> answer
  +-- "process now" ----------> pipeline trigger

Email (Gmail IMAP):
  newsletter-monitor --> fetch + classify links
    +-- Research papers/GitHub/YouTube --> data/sources.json
    +-- News/Blog articles --> data/news-queue.json

Cron (Daily 6PM ET):
  daily-digest --> collect items --> NLM notebook + report + podcast --> Telegram

Cron (Saturday 9AM ET):
  newsletter-monitor --> x-monitor --> data_triage --> content-processor
  --> notebooklm-orchestrator --> news-digest --> digest-deliver --> Telegram
```

## Quick Start (macOS)

```bash
git clone <repo-url> && cd openclaw-research
cp .env.example .env          # Fill in your API keys
cp config/openclaw-channel.example.json ~/.openclaw/openclaw.json  # Edit with your tokens
./setup.sh                    # Installs all prerequisites
nlm login                     # Authenticate NotebookLM
openclaw onboard --install-daemon
```

## Quick Start (Docker)

```bash
git clone <repo-url> && cd openclaw-research
cp .env.example .env          # Fill in OPENAI_API_KEY and TELEGRAM_CHAT_ID
mkdir -p data rag/chroma_db   # Create data directories
docker compose build
docker compose up -d
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| *(send PDF/URL)* | Auto-queued and categorized |
| `/note <text>` | Add a text note to the queue |
| `/track @username` | Start tracking an X/Twitter account |
| `/list accounts\|sources` | Browse data |
| `/subscribe <name> from: <email>` | Track a newsletter |
| `/generate report\|podcast` | Fetch and deliver latest research artifacts |
| `/generate news` | Fetch and deliver latest news podcast |
| `/generate daily` | Fetch and deliver latest daily feed PDF |
| `/process` | Trigger full pipeline |
| `/chat <question>` | Search all categories via RAG + NotebookLM |
| `/status` | System overview |

## Prerequisites

- Node.js 22+, Python 3.10+, `uv`
- OpenAI API key, Telegram bot token, Google account (NotebookLM)
- Pango, Pandoc, FFmpeg (installed by `setup.sh`)
- Gmail App Password (for newsletter monitoring)

## Documentation

- **[CLAUDE.md](CLAUDE.md)** — Full project reference (architecture, skills, data files, troubleshooting)
- **[docs/guide/setup.md](docs/guide/setup.md)** — Detailed setup guide and troubleshooting
- **[docs/](docs/)** — Technical deep-dives, session logs, future plans

## License

Private project.
