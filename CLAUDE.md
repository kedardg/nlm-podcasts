# OpenClaw Research Automation System

An OpenClaw-native system for collecting, processing, and synthesizing research papers, articles, and links. Sources arrive via Telegram, get categorized and processed into NotebookLM notebooks with auto-generated podcasts and reports, then delivered back via Telegram. Includes a dual-layer RAG system for querying across all accumulated research.

## Quick Reference

| Task | Command |
|------|---------|
| Daily pipeline (cron) | `bash scripts/daily_pipeline.sh` |
| Weekly pipeline (cron) | `bash scripts/weekly_pipeline.sh` |
| Daily feed (manual) | `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python3 daily_feed_generator.py --pdf-only --magazine` |
| Chat/RAG query | `.venv/bin/python3 scripts/rag_chat.py "question"` |
| NLM auth check | `bash scripts/nlm_ensure_auth.sh` |
| NLM Playwright login | `.venv/bin/python3 scripts/nlm_playwright.py --login` (one-time, headed, manual Google sign-in) |
| NLM via Playwright | `.venv/bin/python3 scripts/nlm_playwright.py --create "Title" --add-urls URL... --headless` |
| NLM via Playwright (CDP) | `.venv/bin/python3 scripts/nlm_playwright.py --cdp-url http://127.0.0.1:9222 --create "Title" --add-urls URL...` |
| Daily pipeline (Playwright) | `bash scripts/daily_pipeline.sh --use-playwright` |
| HF triage | `.venv/bin/python3 scripts/hf_triage.py [--no-llm] [--dry-run]` |
| Magazine generator | `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python3 scripts/magazine_generator.py [--no-openai] [--pdf] [--dry-run]` |
| Run tests | `.venv/bin/python3 -m pytest tests/ -v` |
| Render PDF | `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python3 lib/pdf_renderer.py --input file.md` |
| Fetch & deliver | `bash scripts/fetch_and_deliver.sh <category> <artifact>` |
| NLM notebook query | `~/.local/bin/nlm notebook query <alias> "question"` |
| Backup | `bash backup.sh` |

## Architecture

```
User (Telegram)
  |
  +-- Send PDF / URL / text --> research-ingest --> data/sources.json
  +-- "track @user" ----------> research-ingest --> data/accounts.json
  +-- "chat <question>" ------> chat-rag --> ChromaDB + NotebookLM --> answer
  +-- "process now" ----------> pipeline trigger
                                    |
Email (Gmail IMAP):                 |
  newsletter-monitor --> fetch + classify links
    +-- Research papers/GitHub/YouTube --> data/sources.json (research pipeline)
    +-- News/Blog articles --> data/news-queue.json (news pipeline)

Blog Feeds (RSS + Scrape):
  blog-monitor --> fetch AI company blogs (Anthropic, OpenAI, Google, Microsoft, Apple)
    +-- Research articles --> data/sources.json (research pipeline)
    +-- News/announcements --> data/news-queue.json (news pipeline)

launchd (Daily 11AM ET):
  daily-digest --> collect last 2 days' items --> NLM notebook + report + podcast --> Telegram

launchd (Saturday 9AM ET):
  0. archive_old_news.py --> archive queued news >30 days
  1. newsletter-monitor --> fetch newsletter emails via IMAP
  1.5. blog-monitor --> fetch AI company blog feeds
  2. x-monitor --> scrape X accounts --> sources.json
  2.5. hf_triage.py --> classify items (domain rules + local LLM) -> triage-results.json
  3. data_triage.py --auto --> cross-stream dedup + volume grouping -> triage-plan.json
  4. content-processor --> reads triage plan, categorize + group + companion notes
  5. notebooklm-orchestrator --> create notebooks + podcasts + reports
  5.5. magazine_generator.py --> triage + NLM report -> magazine JSON + PDF
  6. news-digest --> create weekly news briefing + podcast from news-queue.json
  7. digest-deliver --> download artifacts + index ChromaDB + send via Telegram
```

## Repository Map

```
openclaw-research/
+-- CLAUDE.md                   # This file (project reference)
+-- router.py                   # Telegram message router (entry point)
+-- newsletter_parser.py        # Gmail IMAP newsletter extraction (entry point)
+-- blog_monitor.py             # RSS/scrape blog monitor (entry point)
+-- daily_feed_generator.py     # Daily feed PDF + NLM orchestration (entry point)
+-- data_triage.py              # Cross-stream dedup + notebook grouping (entry point)
|
+-- lib/                        # Shared utility modules
|   +-- __init__.py             # Re-exports: load_json, save_json, normalize_url_deep, etc.
|   +-- classifier.py           # Hybrid content classifier (domain rules + mlx-lm LLM)
|   +-- file_utils.py           # Atomic JSON I/O with file locking
|   +-- url_utils.py            # Deep URL normalization + dedup
|   +-- log_utils.py            # Structured JSON logging (stderr)
|   +-- schemas.py              # Pydantic v2 data models (opt-in validation)
|   +-- pdf_renderer.py         # Markdown -> PDF (WeasyPrint + TOC generation)
|
+-- scripts/                    # Pipeline scripts + helpers
|   +-- daily_pipeline.sh       # Daily cron script (Steps 0-10)
|   +-- weekly_pipeline.sh      # Weekly cron script (10-step pipeline)
|   +-- fetch_and_deliver.sh    # Fetch + deliver artifacts to Telegram
|   +-- telegram_send.sh         # Telegram Bot API helpers (tg_send_message, tg_send_file, tg_send_audio)
|   +-- nlm_ensure_auth.sh      # 5-layer NLM auth recovery (quick check, rotation, CDP, nlm login, Playwright sync)
|   +-- nlm_rotate_cookies.py   # Google PSIDTS cookie rotation
|   +-- nlm_download_audio.py   # NLM audio download (CDP workaround)
|   +-- nlm_create_report.sh    # NLM report with custom prompt
|   +-- rag_chat.py             # Complete Chat/RAG pipeline
|   +-- content_processor.py    # Source categorization + companion notes
|   +-- fetch_latest.py         # Find latest notebook/artifact
|   +-- enhance_report.py       # Report post-processing
|   +-- index_notebook.py       # ChromaDB notebook indexer
|   +-- update_status.py        # Bulk source status updates
|   +-- retry.sh                # Generic retry with exponential backoff
|   +-- pipeline_alert.sh       # Telegram alert on pipeline failure
|   +-- archive_old_news.py     # Archive old news items (>N days) to data/archive/
|   +-- hf_triage.py           # Batch HF triage classifier (domain rules + local LLM)
|   +-- magazine_generator.py  # Magazine JSON + PDF from triage results + NLM report
|   +-- list_queued.py          # List queued sources
|   +-- list_queued_news.py     # List queued news articles
|
+-- rag/                        # RAG layer
|   +-- query.py                # Notebook-routed semantic search
|   +-- embeddings.py           # ChromaDB indexing
|   +-- chroma_db/              # Persistent vector store
|
+-- skills/                     # OpenClaw skill definitions (10 skills)
|   +-- research-ingest/        # Telegram input handler
|   +-- daily-digest/           # Daily feed pipeline skill
|   +-- newsletter-monitor/     # Email newsletter extraction
|   +-- x-monitor/              # X/Twitter scraping
|   +-- content-processor/      # Source categorization
|   +-- notebooklm-orchestrator/ # NLM notebook + artifact creation
|   +-- digest-deliver/         # Artifact download + delivery
|   +-- news-digest/            # Weekly news roundup
|   +-- chat-rag/               # RAG query handler
|   +-- aisa-twitter-api/       # Twitter API integration
|
+-- config/
|   +-- env.sh                  # Environment loader (auto-detects PROJECT_ROOT)
|   +-- prompts/                # Custom NLM prompts (daily/weekly podcast + report)
|   +-- cron-jobs.json          # Cron job definitions
|   +-- openclaw-channel.json   # Telegram channel config
|
+-- data/                       # All persistent state (JSON files + daily-feed/)
+-- templates/                  # CSS stylesheets for PDF generation
+-- tests/                      # pytest test suite (195 tests)
+-- docs/                       # Extended documentation
    +-- guide/                  # Setup, architecture, pipeline guides
    +-- logs/                   # Session logs (design decisions, debugging)
    +-- BUGS.md                 # Centralized bug tracker
```

## Utility Modules (`lib/`)

| Module | Key Exports | Purpose |
|--------|-------------|---------|
| `classifier.py` | `classify(url, title)`, `classify_batch(items)`, `ContentType`, `Classification` | Hybrid content classifier: domain rules (55% coverage) → title heuristics → local Qwen2.5-7B-Instruct via mlx-lm. Classifies items as research/news/blog for pipeline treatment routing. |
| `file_utils.py` | `load_json(path, default={})`, `save_json(path, data)`, `now_iso()` | Atomic JSON I/O with `.tmp` + rename. File locking for concurrent access. `now_iso()` returns UTC timestamp. |
| `url_utils.py` | `normalize_url_deep(url)`, `dedup_urls(sources, news)` | Deep URL normalization: arXiv abs/pdf/html equivalence, GitHub case-insensitive, YouTube video ID extraction, HuggingFace paths. Strips UTM, beehiiv, Substack tracking params. Cross-stream dedup between sources.json and news-queue.json. |
| `log_utils.py` | `log_event(event, step, status, ...)`, `StepTimer(name)` | Structured JSON logging to stderr. `StepTimer` is a context manager that logs step duration. |
| `schemas.py` | `Source`, `NewsArticle`, `Notebook`, `validate_sources()`, `validate_news()` | Pydantic v2 models for data file validation. Opt-in — logs warnings but doesn't block execution. |
| `pdf_renderer.py` | `render_pdf(md, path)`, `render_html_pdf(html, path)`, `render_magazine_pdf(mag, path)`, `domain_pill_color(url)`, CLI: `--input`, `--output`, `--css`, `--skip-toc` | Markdown to styled PDF via markdown-it-py + WeasyPrint. Auto-generates clickable TOC from h2/h3 headings. `render_magazine_pdf()` renders magazine JSON with executive front page (editorial brief + top stories + section navigator), three-tier cards (featured/standard/reference), domain pills, content type badges, significance bars, and semantic `<article>` tags with `data-*` attributes. `domain_pill_color(url)` maps domains to pill colors. Requires `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` on macOS. |

## Pipeline Scripts (`scripts/`)

| Script | Usage | Description |
|--------|-------|-------------|
| `daily_pipeline.sh` | `bash scripts/daily_pipeline.sh [--skip-nlm\|--pdf-only\|--date YYYY-MM-DD\|--use-playwright]` | Daily launchd (11 AM ET). Steps 0-10: newsletter fetch, blog fetch, HTML/PDF generation, NLM notebook + report + podcast (30-min audio poll), magazine HTML, RAG indexing, Telegram delivery via direct Bot API. 2-day item window. |
| `weekly_pipeline.sh` | `bash scripts/weekly_pipeline.sh [--skip-nlm\|--dry-run]` | Weekly launchd (Sat 9 AM ET). 10 steps: archive, newsletter, blog, x-monitor, HF triage, data triage, content-processor, NLM orchestrator, magazine generator, news digest, delivery. Consolidated weekly report + per-category podcasts. |
| `telegram_send.sh` | `source scripts/telegram_send.sh` | Telegram Bot API helpers: `tg_send_message`, `tg_send_file`, `tg_send_audio`. Uses curl directly — no OpenClaw gateway dependency. `sendAudio` gives inline player for podcasts. |
| `fetch_and_deliver.sh` | `bash scripts/fetch_and_deliver.sh <category> <artifact>` | Fetch latest artifact + deliver via Telegram. Handles daily/research/news branching. |
| `nlm_ensure_auth.sh` | `bash scripts/nlm_ensure_auth.sh` | 5-layer NLM auth recovery: (1) quick check, (2) cookie rotation, (3) CDP extraction, (4) `nlm login` via Chrome CDP, (5) Playwright `--sync-auth` with auto-login. Exit 1 = all failed + Telegram alert. |
| `nlm_rotate_cookies.py` | `.venv/bin/python3 scripts/nlm_rotate_cookies.py [--check\|--verify\|--cdp]` | Rotates Google PSIDTS cookies via RotateCookies endpoint. Falls back to CDP. |
| `nlm_download_audio.py` | `.venv/bin/python3 scripts/nlm_download_audio.py <notebook> --output <path>` | Downloads NLM audio via Chrome CDP cookies (workaround for httpx TooManyRedirects). |
| `nlm_create_report.sh` | `bash scripts/nlm_create_report.sh <notebook-id> [prompt-file]` | Creates NLM report with custom prompt. Defaults to weekly prompt; pass `config/prompts/daily-report-prompt-v3.txt` for daily. |
| `rag_chat.py` | `.venv/bin/python3 scripts/rag_chat.py "question"` | Full Chat/RAG pipeline: ChromaDB search, NLM auth, notebook queries, formatted output. |
| `content_processor.py` | `.venv/bin/python3 scripts/content_processor.py [--dry-run]` | Categorizes sources, creates companion notes per category group. |
| `fetch_latest.py` | `.venv/bin/python3 scripts/fetch_latest.py --category <cat> --artifact <type>` | Finds latest notebook/artifact from notebooks.json or daily-feed/. |
| `enhance_report.py` | `.venv/bin/python3 src/pipeline/enhance_report.py --input report.md [--manifest data/daily-feed/DATE-sources.txt] [--dry-run]` | Post-processes NLM reports: removes extra TL;DRs, validates headings, and rebuilds `## Source Links` **deterministically** — strips the model's unreliable version and regenerates from the `--manifest` of URLs actually added to the notebook (falls back to `sources.json` by `--status`). |
| `index_notebook.py` | `.venv/bin/python3 scripts/index_notebook.py --notebook-id <id> [--all\|--report-file <path>]` | Indexes notebook sources + report TL;DRs into ChromaDB. |
| `retry.sh` | `bash scripts/retry.sh <max_retries> <command...>` | Generic retry with exponential backoff (5s, 15s, 45s). |
| `archive_old_news.py` | `.venv/bin/python3 scripts/archive_old_news.py [--days 30] [--dry-run]` | Archives queued news items older than N days to `data/archive/news-YYYY-MM.json`. Runs as Step 0 in weekly pipeline. |
| `hf_triage.py` | `.venv/bin/python3 scripts/hf_triage.py [--no-llm] [--batch-size 100] [--dry-run]` | Batch content classifier. Domain rules + local Qwen2.5-7B-Instruct for uncertain items. Outputs `data/triage-results.json`. Runs as Step 2.5 in weekly pipeline. |
| `magazine_generator.py` | `.venv/bin/python3 scripts/magazine_generator.py [--date YYYY-MM-DD] [--no-openai] [--pdf] [--dry-run]` | Builds magazine JSON from triage results + NLM report. GPT-4o writes editorial brief, section intros, section previews, and item briefs. Selects top 3-5 stories for front page. Cross-section URL dedup (first section wins). Featured items capped at 3 per section. `--pdf` renders via WeasyPrint. |
| `pipeline_alert.sh` | `bash scripts/pipeline_alert.sh "<message>"` | Sends Telegram alert on pipeline failure. |
| `nlm_playwright.py` | `.venv/bin/python3 scripts/nlm_playwright.py --create "Title" --add-urls URL... [--headless] [--cdp-url URL]` | NLM browser automation via Playwright. Full workflow: login, notebook, sources, report, podcast, download, share. Fixes BUG-001 (audio download). `--sync-auth` syncs Playwright cookies to nlm CLI format (Layer 5 of auth recovery). Auto-cleans stale profile lock files. Logs to `data/nlm-operations-log.json`. |

## LLM Provider

- **Default**: OpenAI GPT-4o
- API key configured in `~/.openclaw/openclaw.json`
- RAG embeddings use `text-embedding-3-small` (same OpenAI key via `OPENAI_API_KEY` env var)

## Environment Variables

All user config lives in a single file: **`.env`** (copy from `.env.example`). This includes `OPENAI_API_KEY`, `TELEGRAM_CHAT_ID`, `TELEGRAM_BOT_TOKEN`, and optionally `PROJECT_ROOT`.

Shell scripts and SKILL.md preambles source `config/env.sh`, a thin loader that:
1. Auto-detects `PROJECT_ROOT` from its own file location (works in both bash and zsh)
2. Sources `.env` to export all variables
3. Fails with a clear error if `PROJECT_ROOT` is still unset

```bash
# How scripts load the environment (already wired in all scripts/)
source "$PROJECT_ROOT/config/env.sh"
```

You do **not** need to set `PROJECT_ROOT` in `.env` when running from within the project directory — it's auto-detected. Set it only if running scripts from an external location.

## Prerequisites

- **Node.js 22+** (for OpenClaw)
- **Python 3.10+** with `uv` (for notebooklm-mcp-cli and RAG scripts)
- **Google account** (for NotebookLM access)
- **OpenAI API key** (for LLM provider and RAG embeddings)
- **Telegram bot token** (created via @BotFather)
- **Chrome/Chromium** (for NotebookLM cookie-based authentication)
- **Gmail account** with App Password (for newsletter monitoring via IMAP)
- **Pango** (`brew install pango` / `apt-get install libpango-1.0-0`) — required by WeasyPrint for PDF generation
- **Pandoc** (`brew install pandoc` / `apt-get install pandoc`) — required for report format conversion
- **FFmpeg** (`brew install ffmpeg` / `apt-get install ffmpeg`) — compresses podcast audio for Telegram delivery (48kbps mono)

## Telegram Commands

7 registered slash commands + LLM commands. Old bare-text forms (`untrack`, `unsubscribe`, `news`, `daily`, `triage`, `putdata`) still work via backwards compat.

| Command | Description |
|---------|-------------|
| *(send PDF/file/URL)* | Auto-queued & categorized |
| `/note <text>` | Add a text note to the queue |
| `/track @username` | Start tracking an X/Twitter account |
| `/track off @username` | Pause tracking |
| `/list accounts\|sources\|categories\|newsletters` | Browse data |
| `/addcategory <name> keywords: k1, k2` | Add a new category |
| `/subscribe <name> from: <email>` | Track a newsletter |
| `/subscribe off <name>` | Stop tracking a newsletter |
| `/generate report\|podcast\|report+podcast` | Fetch & deliver latest research artifacts (fetch-only, no creation) |
| `/generate news` | Fetch & deliver latest news podcast (fetch-only) |
| `/generate daily` | Fetch & deliver latest daily feed PDF (fetch-only) |
| `/process` | Trigger full pipeline (creation — same as cron) |
| `/chat <question>` | Search all categories via RAG + NotebookLM |
| `/chat <category>: <question>` | Search specific category (e.g., `chat ml: attention mechanisms`) |
| `/status` | System overview (queue, accounts, schedules) |
| `/status triage` | Data triage summary (dedup, notebook grouping plan) |
| `/status news` | News queue counts |
| `/status daily` | Today's feed status |
| `/reset` | Clear chat context |
| `/model` | Switch LLM model |
| `/manual` | Show command reference |

## Skills

| Skill | Purpose |
|-------|---------|
| `research-ingest` | Entry point for all Telegram input. Routes files, URLs, text notes, and commands to data stores. |
| `x-monitor` | Scrapes tracked X/Twitter accounts. Extracts arXiv papers, blog posts, GitHub repos, YouTube links. |
| `content-processor` | Categorizes queued sources (URL patterns then keywords). Groups by category, creates companion notes. |
| `notebooklm-orchestrator` | Creates NLM notebooks per category, adds sources + companion notes, generates podcasts + reports with custom prompts. |
| `digest-deliver` | Downloads artifacts (MP3, reports), compresses podcasts via ffmpeg, delivers via Telegram, triggers RAG indexing. |
| `blog-monitor` | `blog_monitor.py` — monitors 7 AI company blog RSS feeds + HTML scraping. Config in `data/feeds.json`. |
| `newsletter-monitor` | `newsletter_parser.py` — fetches Gmail IMAP, extracts links, classifies and routes to sources or news queue. |
| `daily-digest` | Daily 11 AM ET pipeline. Collects last 2 days' items, generates magazine HTML/PDF, NLM notebook + report + podcast. |
| `news-digest` | Weekly news roundup from `news-queue.json`. Creates NLM notebook + deep-dive podcast. Requires 3+ articles. |
| `chat-rag` | Notebook-routed RAG queries. ChromaDB identifies notebooks, NLM provides deep answers with citations. |

## Data Files

All persistent state lives in `data/`:

| File | Purpose |
|------|---------|
| `sources.json` | All research sources (queued, processing, completed). Fields: id, type, origin, url, file_path, title, category, tags, added_at, status, notebook_id, from_account |
| `accounts.json` | Tracked X/Twitter accounts. Fields: username, added_at, last_scraped, scrape_for, status |
| `categories.json` | Category definitions with keywords, URL patterns, and daily_section mappings. Fields: name, slug, daily_section, priority, keywords, url_patterns, research_domains |
| `notebooks.json` | NotebookLM notebook registry. Fields: id, alias, category, category_name, created_at, source_count, source_ids, has_podcast, has_report, status, run_id |
| `newsletters.json` | Tracked newsletter subscriptions and check timestamps. Fields: name, sender_pattern, added_at, last_checked, status, extraction_count |
| `feeds.json` | Blog/RSS feed configurations for `blog_monitor.py`. Fields: id, name, url, rss_url, type (rss/scrape), scrape_selector, category, route_to (sources/news), status, last_checked, total_extracted |
| `news-queue.json` | Accumulated news/blog articles for digest generation. Fields: id, url, title, source_newsletter, category, content_type, added_at, status (queued/daily-digested/digested) |
| `processing-log.json` | History of all processing runs. Fields: run_id, started_at, completed_at, trigger, sources_processed, categories, notebooks_created, errors, rag_indexed |
| `nlm-operations-log.json` | Append-only provenance log for Playwright NLM runs. Tracks every URL, text source, and artifact per run with status, size, and timing. Created by `nlm_playwright.py`. |
| `triage-results.json` | HF triage classification results. Each item has content_type (research/news/blog), confidence, method (domain_rule/title_heuristic/llm), reasoning. Created by `scripts/hf_triage.py`, consumed by `scripts/magazine_generator.py`. Incremental — cached results are reused across runs. |
| `triage-plan.json` | Generated triage plan (notebook grouping, dedup report). Created by `data_triage.py --auto`, consumed by content-processor. After use, renamed to `triage-plan-consumed-YYYY-MM-DD.json` |
| `daily-feed/` | Directory of daily feed artifacts. Contains `YYYY-MM-DD.md` (deterministic briefing), `YYYY-MM-DD.pdf` (magazine PDF when `--magazine` used, else plain PDF), `YYYY-MM-DD-report.md` (NLM report — v3 markdown), `YYYY-MM-DD-report.pdf` (NLM report PDF), `YYYY-MM-DD-podcast-raw.mp4` (original audio/mp4 from NLM), `YYYY-MM-DD-podcast.mp3` (compressed 48kbps), `YYYY-MM-DD-sources.txt` (deterministic manifest of source URLs actually added to the notebook, written at source-add time; consumed by `enhance_report.py` to build the report's `## Source Links` from ground truth) |
| `archive/` | Archived old news items. `news-YYYY-MM.json` files grouped by month. Created by `scripts/archive_old_news.py` (weekly pipeline Step 0). |

## Categories

9 unified categories with daily feed section mappings:

| # | Category | Slug | Daily Section | URL Patterns | Keywords (sample) |
|---|----------|------|---------------|--------------|-------------------|
| 1 | Machine Learning | `ml` | AI Research | arxiv.org, openreview.net, paperswithcode.com | transformer, neural network, deep learning, diffusion |
| 2 | NLP & Language Models | `nlp` | AI Research | — | LLM, language model, GPT, RAG, prompt engineering, agents |
| 3 | Computer Vision | `cv` | AI Research | — | object detection, segmentation, YOLO, ViT, CLIP |
| 4 | Robotics & Embodied AI | `robotics` | AI Research | — | ROS, robot, SLAM, manipulation, sim-to-real |
| 5 | AI Safety & Policy | `ai-safety` | News | — | alignment, safety, regulation, governance, RLHF |
| 6 | Open Source & Tools | `oss` | Development | github.com, gitlab.com | framework, library, SDK, open source, benchmark |
| 7 | Tech Industry | `tech` | Tech | — | launch, funding, acquisition, startup, product |
| 8 | Developer Experience | `devx` | Development | dev.to, hashnode.dev | tutorial, guide, API, MLOps, DevOps |
| 9 | General | `general` | News | — | *(catch-all)* |

**Daily feed sections** (PDF report groupings):
- **AI Research** — ml, nlp, cv, robotics (papers, technical posts)
- **Tech** — tech (industry news, products, funding)
- **Development** — oss, devx (repos, tools, tutorials)
- **News** — ai-safety, general (policy, regulation, everything else)

Add new categories via Telegram: `add category <name> keywords: <k1>, <k2>, ...`

## RAG Layer

Notebook-routed RAG system using ChromaDB. Two collections serve different purposes:

### Collections

| Collection | Purpose | Indexed by |
|------------|---------|------------|
| `notebook_sources` | Source metadata + companion notes for notebook routing | `scripts/index_notebook.py` (at notebook creation time) |
| `research_reports` | Full report text chunks (legacy) | `rag/embeddings.py --report-file` |

### How Notebook Routing Works

```
User: "chat what papers discuss attention?"
  |
  +- Step 1: query.py --mode notebook -> ChromaDB "notebook_sources" (k=5)
  |   Returns: [{notebook_id, hit_count, best_score, composite_score, sources_matched}, ...]
  |
  +- Step 2: Group by notebook_id -> pick top 2-3
  |
  +- Step 3: nlm notebook query <id> "<question>" (x2-3 notebooks)
  |
  +- Step 4: Merge responses -> answer with citations
```

**Scoring**: Results are filtered by `MIN_NOTEBOOK_SCORE = 0.6` (removes irrelevant matches), then ranked by composite score (70% best match quality + 30% normalized hit volume).

### What Gets Indexed

Per notebook, at creation time (orchestrator Step 8.5):
- **Source documents**: Title + URL + category name + keywords (one per source)
- **Companion note chunks**: ~500-token chunks of the companion note (themes, connections, summaries)
- **Report summaries**: TL;DR extracted from NotebookLM-generated reports (`doc_type: "report_summary"`, one per notebook, upsert-safe)
- **Metadata**: notebook_id, notebook_alias, category, source_id, source_url, doc_type, created_at

### Indexing

- **Automatic**: The notebooklm-orchestrator runs `scripts/index_notebook.py` after creating each notebook (Step 8.5).
- **Storage**: `rag/chroma_db/` (auto-created, persists between runs).

### Querying

- **Notebook mode** (default): Routes to relevant notebooks via semantic search on source metadata.
- **Report mode** (legacy): Searches full report text chunks directly.

### Manual RAG commands
```bash
# Index a specific notebook's sources
.venv/bin/python3 scripts/index_notebook.py --notebook-id <id>

# Index most recent notebook (with companion note)
.venv/bin/python3 scripts/index_notebook.py --companion-note path/to/note.md

# Re-index all notebooks
.venv/bin/python3 scripts/index_notebook.py --all

# Index a report's TL;DR summary for a notebook
.venv/bin/python3 scripts/index_notebook.py --notebook-id <id> --report-file path/to/report.md

# Query: notebook routing (default)
.venv/bin/python3 rag/query.py "attention mechanisms in transformers" --mode notebook
.venv/bin/python3 rag/query.py "SLAM algorithms" --mode notebook --category robotics

# Query: legacy report search
.venv/bin/python3 rag/query.py "popular repos" --mode report --category github --top-k 10

# Index a report (legacy)
.venv/bin/python3 rag/embeddings.py --report-file data/ml-2026-02-15-report.md --run-id run_20260215

# Index notebook sources directly (alternative to scripts/index_notebook.py)
.venv/bin/python3 rag/embeddings.py --notebook-id <id> --notebook-alias <alias> --category <slug> --source-ids src_001,src_002
```

## Newsletter / Email Integration

### How it works
1. Subscribe to AI newsletters using a dedicated Gmail address with a `+ai` alias (e.g., `kedar-research+ai@gmail.com`)
2. The `newsletter-monitor` skill fetches emails via IMAP, extracts links from HTML, and classifies them
3. Research papers (arXiv, OpenReview), GitHub repos, and YouTube -> `data/sources.json` (existing research pipeline)
4. News articles and blog posts -> `data/news-queue.json` (news digest pipeline)
5. The `news-digest` skill creates a structured briefing and generates a podcast via NotebookLM

### Gmail setup
1. Create a Gmail account (or use existing with `+` alias trick)
2. Enable 2FA at https://myaccount.google.com/security
3. Generate an App Password at https://myaccount.google.com/apppasswords (select "Mail")
4. Save credentials:
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

### Multi-project reuse
The same Gmail account supports multiple projects via `+` aliases:
- `your-email+ai@gmail.com` — this project (AI research)
- `your-email+travel@gmail.com` — future travel project
- `your-email+finance@gmail.com` — future finance project

Each project uses its own `plus_alias` value in the credentials file.

### Blog monitor commands
```bash
# Normal run (fetch all active feeds)
.venv/bin/python3 blog_monitor.py

# Dry-run (preview without saving)
.venv/bin/python3 blog_monitor.py --check

# Run single feed
.venv/bin/python3 blog_monitor.py --feed feed_001
```

### Newsletter parser commands
```bash
# Normal run (process since last check)
.venv/bin/python3 newsletter_parser.py

# Dry-run (preview without saving)
.venv/bin/python3 newsletter_parser.py --check

# Process since specific date
.venv/bin/python3 newsletter_parser.py --since 2026-02-01
```

## Daily Feed Commands

```bash
# Generate daily feed with magazine layout (NLM-first, requires NLM report)
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python3 daily_feed_generator.py --magazine

# Magazine layout for specific date
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python3 daily_feed_generator.py --date 2026-02-20 --magazine

# PDF only with magazine (no podcast orchestration)
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python3 daily_feed_generator.py --pdf-only --magazine

# Generate daily feed (legacy plain layout)
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python3 daily_feed_generator.py

# Dry run (preview only)
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python3 daily_feed_generator.py --dry-run

# Render markdown to PDF directly (with optional --skip-toc)
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python3 lib/pdf_renderer.py --input data/daily-feed/2026-02-16.md --skip-toc
```

The `--magazine` flag enables the NLM-first magazine layout. It looks for an NLM report at `data/daily-feed/YYYY-MM-DD-report.md` (generated with `daily-report-prompt-v3.txt`). The parser extracts sections/items/takeaways from the v3 markdown format (## sections, ### articles, **Source:** URLs). These are merged with the original feed items (enriching them with NLM summaries and significance ratings), then rendered as a card-based PDF. Items are tiered: high-significance items get featured cards, medium get standard two-column cards, and unmatched/low items get a compact list (capped at 20 per section). Cross-section URL dedup prevents items from appearing in multiple sections. Falls back to plain PDF if no report exists.

## Core Workflow Commands (NotebookLM CLI)

### Create a research notebook
```bash
nlm notebook create "Research Topic Name"
nlm alias set research <notebook-id>
```

### Add sources
```bash
nlm source add research --url "https://arxiv.org/..." --wait
nlm source add research --file paper.pdf --wait
nlm source add research --youtube "https://youtube.com/..." --wait
nlm source add research --text "inline content" --title "Notes" --wait
```
Always use `--wait` to ensure processing completes before next steps.

### Query the notebook
```bash
nlm notebook query research "Summarize the key findings"
```

### Generate podcast audio
```bash
nlm audio create research --format deep_dive --length default --confirm
nlm studio status research          # poll until generation completes
# NOTE: `nlm download audio` is broken (TooManyRedirects). Use the workaround:
.venv/bin/python3 scripts/nlm_download_audio.py research --output podcast.mp4
```
Audio formats: `deep_dive`, `brief`, `critique`, `debate`
Lengths: `short`, `default`, `long`

### Generate reports and other artifacts
```bash
nlm report create research --format "Briefing Doc" --confirm
nlm mindmap create research --confirm
nlm slides create research --confirm
nlm video create research --format explainer --confirm
```

### Web/Drive research (NotebookLM built-in)
```bash
nlm research start "query" --notebook-id <id> --mode deep
nlm research status research --max-wait 300
nlm research import research <task-id>
```

### Sharing
```bash
nlm share public <notebook-id>
```

## Scheduled Pipelines (macOS launchd)

Both pipelines use macOS launchd (not cron — cron lacks TCC permissions for external volumes). Delivery uses direct Telegram Bot API via `scripts/telegram_send.sh` (no OpenClaw gateway dependency).

### Daily Tech Feed — 11 AM ET, every day

Plist: `~/Library/LaunchAgents/com.openclaw.daily-pipeline.plist`

```bash
# Install
launchctl load ~/Library/LaunchAgents/com.openclaw.daily-pipeline.plist

# Manual run
bash scripts/daily_pipeline.sh  # add --skip-nlm, --pdf-only, --date YYYY-MM-DD, --use-playwright
```

### Weekly Research Digest — Saturday 9 AM ET

Plist: `~/Library/LaunchAgents/com.openclaw.weekly-pipeline.plist`

```bash
# Install
launchctl load ~/Library/LaunchAgents/com.openclaw.weekly-pipeline.plist

# Manual run
bash scripts/weekly_pipeline.sh  # add --skip-nlm, --dry-run
```

### Manage
```bash
launchctl list | grep openclaw                    # Check status
launchctl unload ~/Library/LaunchAgents/com.openclaw.daily-pipeline.plist   # Stop
launchctl load ~/Library/LaunchAgents/com.openclaw.daily-pipeline.plist     # Start
```

### Why launchd, not cron?
- macOS cron lacks Full Disk Access (TCC) — gets "Operation not permitted" on `/Volumes/ext/`
- launchd agents run as the user and inherit TCC permissions
- launchd catches up on missed schedules after sleep/wake (cron does not)

## Workspace Files

The OpenClaw LLM agent reads these files from `~/.openclaw/workspace/` at session start:

| File | Purpose |
|------|---------|
| `TOOLS.md` | **Critical**: Tells the LLM how to handle pipeline commands (generate, process, daily digest). Contains the full Daily Digest Pipeline (Steps 0-9), Fetch Script, Chat Script, and Process Pipeline with concrete CLI commands. Must use `~/.local/bin/nlm` (not bare `nlm`) since gateway doesn't source shell profiles. |
| `RESEARCH.md` | Symlink to this file (`CLAUDE.md`). Gives the LLM project context. |
| `IDENTITY.md` | Agent personality / identity. |
| `SOUL.md` | Agent behavior guidelines. |
| `AGENTS.md` | Multi-agent configuration. |

**If generate/process commands fail**, check `TOOLS.md` first — it's the single source of truth for pipeline execution.

### Sending Files to Telegram

Pipeline scripts use `scripts/telegram_send.sh` for direct Telegram Bot API delivery (no OpenClaw gateway dependency):

```bash
source scripts/telegram_send.sh
tg_send_message "text"                    # sendMessage
tg_send_file "/path/to/file.pdf" "caption"  # sendDocument
tg_send_audio "/path/to/podcast.mp3" "caption"  # sendAudio (inline player)
```

Requires `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.

The OpenClaw LLM agent still uses text replies for interactive chat — OpenClaw auto-routes to the originating Telegram chat.

## OpenClaw Configuration

- Gateway config: `~/.openclaw/openclaw.json`
- Default workspace: `~/.openclaw/workspace`
- Telegram channel template: `config/openclaw-channel.json`
- Cron job definitions: `config/cron-jobs.json`
- Add agents: `openclaw agents add <name>` (each gets isolated workspace)
- Reconfigure: `openclaw configure`
- Enable web search: `openclaw configure --section web`

### Reliability Settings (in `~/.openclaw/openclaw.json`)

| Key | Value | Purpose |
|-----|-------|---------|
| `commands.restart` | `true` | Enables `/restart` chat command for manual gateway restart |
| `session.reset.mode` | `idle` | Auto-reset sessions after inactivity |
| `session.reset.idleMinutes` | `60` | Reset threshold (prevents stale context buildup) |
| `session.maintenance.mode` | `enforce` | Prune orphaned/stale sessions automatically |

### Gateway Health Plugin (`~/.openclaw/extensions/gateway-health/`)

Self-healing gateway monitor that runs as a native OpenClaw plugin (no external cron needed):

| Feature | Behavior |
|---------|----------|
| **Memory watchdog** | Probes RSS every 10 min. Kills process (launchd auto-restarts) if RSS exceeds 800MB hard limit OR grows >200MB from startup baseline |
| **NLM auth on startup** | Runs 3-layer auth recovery 5s after gateway start (quick check -> cookie rotation -> CDP) |
| **Pre-cron auth check** | Ensures NLM auth is valid before every cron/isolated session |
| **Post-compaction logging** | Logs RSS after context compaction for leak tracking |

Config via `openclaw.plugin.json`:
- `probeIntervalMs` (default: 600000 = 10 min)
- `maxRssMb` (default: 800 — OpenClaw baseline is ~550MB)
- `growthThresholdMb` (default: 200 — catches slow leaks from baseline)
- `nlmAuthOnStartup` (default: true)
- `nlmAuthBeforeCron` (default: true)

For managed browser authentication through OpenClaw:
```bash
nlm login --provider openclaw --cdp-url http://127.0.0.1:18800
```

## Triage & Volume Management

Pre-processing step in the weekly pipeline that handles deduplication, volume analysis, and notebook grouping.

### Grouping Rules

| Condition | Action |
|-----------|--------|
| Category has 0 sources | Skip (warning logged) |
| Category has <3 sources | Merge with affinity partner (same `daily_section`, largest sibling) |
| Category has 3-30 sources | Own notebook |
| Category has >30 sources | Split into sub-notebooks of ~15 each |
| Hard limit: 50 sources/notebook | Enforced on all groups |

### Triage commands

```bash
# Cron mode: apply rules and save plan
.venv/bin/python3 data_triage.py --auto

# Preview without saving
.venv/bin/python3 data_triage.py --dry-run

# Print summary for Telegram
.venv/bin/python3 data_triage.py --summary
```

### URL normalization self-test

```bash
.venv/bin/python3 lib/url_utils.py
```

## Custom Prompts

| File | Used by | Purpose |
|------|---------|---------|
| `config/google-credentials.json` | `nlm_playwright.py` | Google account credentials for auto-login when NLM session expires. Fields: `email`, `password`, `profile_dir`. Gitignored. Auto-login attempts headless form fill; falls back to `--login` prompt on 2FA or failure. |
| `config/prompts/daily-report-prompt-v3.txt` | `nlm_create_report.sh` | Daily report: standard markdown (## sections, ### articles, **Source:** URLs, **TL;DR:** summaries). Parsed by `daily_feed_generator.py::parse_nlm_report()`. Written in the **JournalClub arc** style (hook → stakes → prior-art-flaw → named mechanism → result/takeaway; analogy-first but jargon-exact). |
| `config/prompts/daily-podcast-focus.txt` | `nlm audio create --focus` | Daily podcast: two-host explainer (Explainer + Curious Skeptic) following the **JournalClub arc** per theme; accessible-but-precise cross-source synthesis. |
| `config/prompts/weekly-podcast-focus.txt` | `nlm audio create --focus` | Weekly podcast: deep two-host explainer (Explainer + Critical Peer Reviewer) following the **JournalClub arc**; architecture walkthrough, multi-paper synthesis. |
| `config/prompts/magazine-structure-prompt.txt` | `magazine_generator.py` | GPT-4o system prompt for editorial brief, section intros/previews, and per-item briefs. Compressed **JournalClub arc** (hook → mechanism → takeaway). Outputs JSON contract (`editorial_brief`, `sections[].introduction/preview/briefs[]`). |
| `templates/report-prompt.txt` | `nlm_create_report.sh` (default) | Weekly report: TL;DR, executive summary, architecture/methodology analysis, actionable insights — written in the **JournalClub arc** style. |
| `config/prompts/*.technical.txt`, `templates/report-prompt.technical.txt` | (unused fallback) | Pre-JournalClub technical-dense versions of the five prompts above, kept for A/B comparison. Swap a `.technical.txt` over the live name to revert that prompt's style. |

## External Dependencies

### Python packages (installed in `.venv`)

| Package | Version | Purpose |
|---------|---------|---------|
| `chromadb` | 1.5.0 | Vector store for RAG (notebook routing + report search) |
| `openai` | 2.21.0 | LLM provider + text-embedding-3-small for RAG |
| `weasyprint` | 68.1 | HTML/CSS to PDF rendering (requires pango) |
| `markdown-it-py` | 4.0.0 | Markdown to HTML conversion for PDF renderer |
| `feedparser` | 6.0.12 | RSS feed parsing for blog monitor |
| `beautifulsoup4` | 4.14.3 | HTML parsing for newsletter link extraction |
| `pydantic` | 2.12.5 | Data model validation (opt-in) |
| `requests` | 2.32.5 | HTTP client (NLM audio download, blog scraping) |
| `trafilatura` | 2.0.0 | Web page content extraction |
| `ural` | 1.5.0 | URL normalization baseline |
| `websocket-client` | 1.9.0 | Chrome CDP communication |
| `playwright` | 1.52.0 | Browser automation for NLM (replaces nlm CLI for audio download + auth) |
| `mlx-lm` | 0.30.7 | Apple Silicon LLM inference (Qwen2.5-7B-Instruct-4bit for content classification) |

### System tools

| Tool | Version | Purpose |
|------|---------|---------|
| `nlm` (notebooklm-mcp-cli) | 0.3.15 | NotebookLM CLI for notebooks, sources, audio, reports |
| `ffmpeg` | 8.0.1 | Podcast audio compression (48kbps mono for Telegram) |
| `pandoc` | 3.9 | Report format conversion |
| `pango` | — | Font rendering (required by WeasyPrint) |

## Extended Documentation

| Document | Path | Content |
|----------|------|---------|
| Setup Guide | `docs/guide/setup.md` | Full setup instructions, prerequisites, troubleshooting |
| Architecture Guide | `docs/guide/architecture.md` | System architecture deep-dive |
| OpenClaw Platform | `docs/guide/openclaw-platform.md` | OpenClaw gateway, plugins, skills |
| News Pipeline | `docs/guide/news-pipeline.md` | Newsletter + news digest pipeline |
| Report Generation | `docs/guide/generation.md` | Report enhancement + PDF rendering |
| Podcastfy Evaluation | `docs/guide/podcastfy-evaluation.md` | Podcastfy as local/fallback podcast generation alternative to NLM |
| NLM Playwright Selectors | `docs/guide/nlm-playwright-selectors.md` | NLM web UI selector reference for Playwright automation (stability ratings, click sequences) |
| Bug Tracker | `docs/BUGS.md` | Known bugs and workarounds |
| Session Logs | `docs/logs/` | Design decisions and debugging sessions |

## MCP Server Architecture

The `notebooklm-mcp-cli` exposes 29 MCP tools covering:
- **Notebook management**: create, list, query, delete
- **Source ingestion**: URL, text, file (PDF), YouTube, Google Drive
- **Content studio**: audio, video, reports, quizzes, flashcards, mindmaps, slides, infographics, data tables
- **Research**: web search, Drive search, import results
- **Sharing**: public links, email invitations

When registered via `nlm setup add claude-code`, Claude Code can call these tools directly in conversation using `@notebooklm-mcp`.

## Troubleshooting

### Gateway unresponsive or high memory usage
- The gateway-health plugin monitors RSS and restarts automatically if memory exceeds limits.
- If the gateway is hung (responding slowly or timing out), restart: `openclaw daemon restart` or `launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway`
- Check current RSS: `ps -p $(pgrep -f openclaw) -o rss= | awk '{print $1/1024 "MB"}'`
- If the plugin causes a restart loop (RSS exceeds limit immediately on startup), raise `maxRssMb` in `~/.openclaw/extensions/gateway-health/openclaw.plugin.json` or temporarily disable with `nlmAuthOnStartup: false`.
- The plugin logs probe results to the gateway log — check with `openclaw daemon logs`.

### Telegram bot not responding
- Check bot token in `~/.openclaw/openclaw.json`
- Ensure OpenClaw daemon is running: `openclaw daemon status`
- Restart: `openclaw daemon restart`

### "Pipeline not connected" or generate/process commands failing
- **TOOLS.md drives pipeline behavior**: The LLM reads `~/.openclaw/workspace/TOOLS.md` to know how to handle `generate` and `process` commands. If it says "not connected", check TOOLS.md for stale placeholder instructions.
- **Stale sessions**: If the LLM gave wrong answers previously, the session caches those bad responses and the LLM repeats them. Fix: clear sessions with `echo '{}' > ~/.openclaw/agents/main/sessions/sessions.json` and remove stale `.jsonl` files from that directory, then restart the gateway.
- **`nlm` not in PATH**: The gateway process doesn't source `~/.zshrc`, so `~/.local/bin` is not in PATH. Always use full path `~/.local/bin/nlm` in TOOLS.md and scripts.
- **Session labels**: Do NOT use `sessions_send` tool in pipeline instructions. The LLM should just reply with text directly — OpenClaw routes it to Telegram automatically.

### NotebookLM commands failing
- Google rotates `__Secure-1PSIDTS` and other cookies every ~10 minutes. The `scripts/nlm_ensure_auth.sh` has 4-layer recovery: (1) quick auth check, (2) cookie rotation via `RotateCookies` endpoint, (3) CDP extraction from running Chrome, (4) headless `nlm login`.
- Manual re-authenticate: `~/.local/bin/nlm login`
- NotebookLM MCP registration with Claude Code is NOT needed for the OpenClaw pipeline — the pipeline uses `nlm` CLI commands directly
- Verify tool is installed: `~/.local/bin/nlm --version`
- Diagnose: `~/.local/bin/nlm doctor --verbose`

### RAG queries returning empty results
- Check if ChromaDB has been populated: `ls rag/chroma_db/`
- Re-index all notebooks: `.venv/bin/python3 scripts/index_notebook.py --all`
- Ensure `OPENAI_API_KEY` is set in environment
- If `fallback_used: true` in query output, ChromaDB `notebook_sources` collection is empty — run indexing first
- Legacy report indexing: `.venv/bin/python3 rag/embeddings.py --report-dir data/ --run-id manual`

### X/Twitter scraping not working
- Check if the aisa-twitter-api skill is installed
- Verify accounts are active: send `list accounts` via Telegram
- Check rate limits — Twitter API has strict limits

### Files not delivered to Telegram
- Pipeline scripts use `scripts/telegram_send.sh` for direct Bot API delivery (no OpenClaw gateway needed).
- Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set in `.env`.
- Use `tg_send_file` for documents, `tg_send_audio` for podcasts (inline player).
- If files still don't appear: check that the file path exists and the file is not empty.

### Podcast too large for Telegram (>16MB)
- NLM generates podcasts at ~256kbps stereo (70MB for 38 min with `--length long`)
- Pipeline compresses via ffmpeg: `/opt/homebrew/bin/ffmpeg -y -i raw.mp4 -ac 1 -b:a 48k -ar 22050 compressed.mp3`
- Sweet spot: 48kbps mono at 22kHz — ~13MB for 38 min, clear spoken word
- If ffmpeg fails, the raw file still exists as `*-podcast-raw.mp4`; share via notebook link instead
- NLM outputs audio as `audio/mp4` format — raw files are `.mp4`, ffmpeg converts to `.mp3`

### `nlm download audio` fails (TooManyRedirects)
- **Known bug**: `nlm download audio` always fails because httpx strips cookies on cross-domain redirects through Google's CDN chain (`lh3.googleusercontent.com` -> `lh3.google.com` -> `googlevideo.com`).
- **Fix**: Use `scripts/nlm_download_audio.py` instead, which extracts cookies from Chrome via CDP and manually follows the redirect chain with domain-aware cookies.
- **Both pipelines** (`daily_pipeline.sh`, `weekly_pipeline.sh`) already use this workaround.
- **Requires**: Chrome running with `--remote-debugging-port` (any port in 9222-9231).
- `nlm download report` works fine (text, not CDN-streamed).

### `nlm download` syntax (for report/other non-audio artifacts)
- **Correct syntax**: `nlm download report <notebook-id-or-alias> --output /path/to/output.md`
- Use `--id <artifact-id>` flag only if there are multiple artifacts and you need to pick one.
- Always use full path: `~/.local/bin/nlm download ...`

### Scheduled pipeline not running
- Verify launchd agents: `launchctl list | grep openclaw`
- Check stderr logs: `cat /tmp/openclaw-daily-pipeline.err`
- macOS cron does NOT work for external volumes (TCC blocks `/Volumes/ext/`) — use launchd
- If Mac was asleep at scheduled time, launchd catches up on wake (cron does not)
- External volume must be mounted — check `ls /Volumes/ext/` before debugging further

## Setup

Run the bootstrap script:
```bash
./setup.sh
```

Or manually:
1. `npm install -g openclaw@latest`
2. `uv tool install notebooklm-mcp-cli` then `nlm login` and `nlm setup add claude-code`
3. `npx clawhub@latest install aisa-twitter-api`
4. `pip install chromadb openai`
5. Configure `.env` with `OPENAI_API_KEY`, `TELEGRAM_CHAT_ID`, `TELEGRAM_BOT_TOKEN`
6. Configure `~/.openclaw/openclaw.json` with Telegram bot token + OpenAI API key
7. `openclaw onboard --install-daemon`
8. Install launchd agents (see Scheduled Pipelines section above)

## Security Considerations

- OpenClaw skills from ClawHub are community-contributed and not fully vetted; review source code before installing
- notebooklm-mcp-cli uses undocumented Google APIs that may break without notice
- Cookie-based auth stores session tokens locally; treat `~/.notebooklm-mcp/` as sensitive
- Always pass `--confirm` for create/delete operations in scripts
- Use `nlm alias set <name> <id>` to avoid typing notebook IDs repeatedly
- Use `nlm notebook list --json` for programmatic output parsing

## Future Extensions

- **Rescript podcast transcripts**: Ingest AI researcher podcast transcripts from https://app.rescript.info/ — could be a new source type for deep research context.
- **Instagram reels**: New `instagram-monitor` skill + category for learning content reels. Scrape shared/saved reels, extract transcripts for NotebookLM.
- **Travel sources**: New "Travel" category + `stash travel <url>` command. Sources accumulate but don't auto-process into podcasts — just stored and queryable via `chat travel: <question>`.

## Documentation Maintenance

When making code changes, check if these files need updating:

| Change | Files to update |
|--------|----------------|
| New/modified Python imports | `lib/__init__.py` (if adding re-exports) |
| New script in `scripts/` | This file (Pipeline Scripts table), `backup.sh`, `restore.sh` |
| New skill | This file (Skills table), `restore.sh` (symlink step) |
| New data file | This file (Data Files table) |
| New Python dependency | `pyproject.toml`, this file (External Dependencies), `restore.sh` |
| Pipeline step changes | `~/.openclaw/workspace/TOOLS.md`, relevant `skills/*/SKILL.md` |
| New category | `data/categories.json`, this file (Categories table) |
| Bug found/fixed | `docs/BUGS.md` |
| Design decision | `docs/logs/YYYY-MM-DD-short-description.md` |
