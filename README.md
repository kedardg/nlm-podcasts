# Research Automation System

Automated research ingestion, processing, and synthesis pipeline. Sources arrive via Telegram, get categorized and processed into NotebookLM notebooks with auto-generated podcasts and reports, then delivered back via Telegram. Includes a dual-layer RAG system for querying across all accumulated research.

## Features

- **Multi-source ingestion** — PDFs, URLs, text via Telegram; newsletters via Gmail IMAP; RSS/blog monitoring; X/Twitter account tracking
- **Automated pipelines** — Daily tech feed (11 AM ET) and weekly research digest (Saturday 9 AM ET) via macOS launchd
- **NotebookLM integration** — Auto-generated podcasts, reports, quizzes, and mind maps
- **Magazine output** — HTML-first magazine with editorial briefs, tiered cards, and optional PDF
- **RAG search** — Query all research via ChromaDB-backed notebook routing
- **Telegram interface** — Slash commands for ingestion, generation, and chat

## Architecture

```
User (Telegram)
  |
  +-- Send PDF / URL / text --> router --> data/sources.json
  +-- "track @user" ---------> router --> data/accounts.json
  +-- "chat <question>" -----> chat-rag --> ChromaDB + NotebookLM --> answer
  +-- "process now" ----------> pipeline trigger

Email (Gmail IMAP):
  newsletter_parser --> fetch + classify links
    +-- Research papers/GitHub/YouTube --> data/sources.json
    +-- News/Blog articles --> data/news-queue.json

Blog Feeds (RSS + Scrape):
  blog_monitor --> fetch AI company blogs
    +-- Research --> data/sources.json
    +-- News --> data/news-queue.json

launchd (Daily 11AM ET):
  daily_pipeline.sh --> newsletters + blogs + feed PDF/HTML + NLM notebook
  + report + podcast --> Telegram

launchd (Saturday 9AM ET):
  weekly_pipeline.sh --> archive + newsletters + blogs + triage + content
  + NLM notebooks + magazine + news digest --> Telegram
```

## Project Structure

```
src/                        # All Python source code
  router.py                 # Telegram message router
  newsletter_parser.py      # Gmail IMAP newsletter extraction
  blog_monitor.py           # RSS/scrape blog monitor
  daily_feed_generator.py   # Daily feed PDF/HTML + NLM orchestration
  data_triage.py            # Cross-stream dedup + notebook grouping
  lib/                      # Shared utility modules
    file_utils.py            # Atomic JSON I/O with file locking
    url_utils.py             # Deep URL normalization + dedup
    log_utils.py             # Structured JSON logging
    classifier.py            # Hybrid content classifier (domain + LLM)
    pdf_renderer.py          # Markdown/HTML to PDF (WeasyPrint)
    html_renderer.py         # Magazine HTML renderer
    schemas.py               # Pydantic v2 data models
  pipeline/                  # Pipeline processing scripts
    magazine_generator.py    # Magazine JSON + PDF from triage + NLM
    hf_triage.py             # Batch content classifier
    content_processor.py     # Source categorization + companion notes
    archive_old_news.py      # Archive old news items
    enhance_report.py        # Report post-processing
    fetch_latest.py          # Find latest notebook/artifact
    index_notebook.py        # ChromaDB notebook indexer
    rag_chat.py              # Complete Chat/RAG pipeline
  nlm/                       # NotebookLM integration
    nlm_rotate_cookies.py    # Google cookie rotation
    nlm_download_audio.py    # Audio download (CDP workaround)
    nlm_playwright.py        # Browser automation for NLM
  rag/                       # RAG layer
    query.py                 # Notebook-routed semantic search
    embeddings.py            # ChromaDB indexing
scripts/                    # Shell pipeline scripts
  daily_pipeline.sh          # Daily cron (11 AM ET)
  weekly_pipeline.sh         # Weekly cron (Saturday 9 AM ET)
  fetch_and_deliver.sh       # Fetch + deliver artifacts to Telegram
  telegram_send.sh           # Telegram Bot API helpers
  nlm_ensure_auth.sh         # 5-layer NLM auth recovery
  nlm_create_report.sh       # NLM report with custom prompt
  pipeline_alert.sh          # Telegram alert on pipeline failure
  retry.sh                   # Generic retry with exponential backoff
config/                     # Prompt templates + environment
data/                       # All persistent state (JSON + daily-feed/)
rag/                        # Persistent vector store (chroma_db/)
templates/                  # CSS stylesheets for PDF/HTML
tests/                      # pytest test suite
skills/                     # Skill definitions (10 skills)
docs/                       # Extended documentation
todos/                      # Issue tracker
```

## Quick Start

```bash
git clone <repo-url> && cd openclaw-research
cp .env.example .env          # Fill in OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
./setup.sh                    # Installs all prerequisites + Python deps
nlm login                     # Authenticate NotebookLM
```

### Scheduled Pipelines (macOS launchd)

```bash
# Install launchd agents
launchctl load ~/Library/LaunchAgents/com.openclaw.daily-pipeline.plist
launchctl load ~/Library/LaunchAgents/com.openclaw.weekly-pipeline.plist
```

### Manual Runs

```bash
# Daily feed (PDF only, no podcast)
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python3 src/daily_feed_generator.py --pdf-only --magazine

# Weekly pipeline
bash scripts/weekly_pipeline.sh

# Chat/RAG query
.venv/bin/python3 src/pipeline/rag_chat.py "question"

# Run tests
.venv/bin/python3 -m pytest tests/ -v
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| *(send PDF/URL)* | Auto-queued and categorized |
| `/note <text>` | Add a text note to the queue |
| `/track @username` | Start tracking an X/Twitter account |
| `/list accounts\|sources` | Browse data |
| `/subscribe <name> from: <email>` | Track a newsletter |
| `/generate report\|podcast\|both` | Fetch and deliver latest research artifacts |
| `/generate news` | Fetch and deliver latest news podcast |
| `/generate daily` | Fetch and deliver latest daily feed |
| `/process` | Trigger full pipeline |
| `/chat <question>` | Search all categories via RAG + NotebookLM |
| `/status` | System overview |

## Prerequisites

- Python 3.10+, `uv`
- OpenAI API key (GPT-4o for briefs, text-embedding-3-small for RAG)
- Telegram bot token (via @BotFather)
- Google account (NotebookLM access)
- Pango, Pandoc, FFmpeg (installed by `setup.sh`)
- Gmail App Password (for newsletter monitoring)

## Documentation

- **[CLAUDE.md](CLAUDE.md)** — Full project reference (architecture, skills, data files, troubleshooting)
- **[docs/guide/](docs/guide/)** — Setup, architecture, pipeline guides
- **[docs/BUGS.md](docs/BUGS.md)** — Known bugs and workarounds

## License

Private project.
