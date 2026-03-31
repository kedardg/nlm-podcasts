# Report & Podcast Generation

## Two Modes: Fetch vs Create

**Fetch (interactive)**: All `/generate` commands are fetch-only — they deliver existing artifacts. No new notebooks, podcasts, or reports are created.

**Create (cron/process)**: Only cron jobs and `/process` create new content (notebooks, podcasts, reports).

| Command | Router Reason | What Happens |
|---|---|---|
| `/generate report\|podcast\|report+podcast` | `command_generate_fetch` (category=research) | Fetch latest research artifacts from existing notebook |
| `/generate news` | `command_generate_fetch` (category=news) | Fetch latest news podcast from existing notebook |
| `/generate daily` | `command_generate_fetch` (category=daily) | Fetch latest daily feed PDF from `data/daily-feed/` |
| Weekly cron + `/process` | `command_process` | Full pipeline: triage → content-processor → orchestrator → deliver |

## Fetch Latest Pipeline (`/generate report|podcast|report+podcast`)

This is the **interactive path**. It does NOT create new notebooks — it retrieves already-generated artifacts from the most recent research notebook.

### `scripts/fetch_latest.py` — Notebook Lookup

```bash
# Find latest research notebook with a report
python3 scripts/fetch_latest.py --category research --artifact report

# Find latest news notebook
python3 scripts/fetch_latest.py --category news

# Find any latest notebook
python3 scripts/fetch_latest.py
```

**How it works** (fetch_latest.py):

1. Load notebooks from `data/notebooks.json`
2. If empty, fallback to `~/.local/bin/nlm notebook list --json` (live query)
3. Filter by criteria:
   - `--category news` → `category == "news"`
   - `--category research` → anything NOT "news" or "daily"
   - `--artifact report` → `has_report == true`
   - `--artifact podcast` → `has_podcast == true`
   - Only `status: "complete"` notebooks
4. Pick the one with the latest `created_at` timestamp
5. Output JSON: `notebook_id`, `alias`, `has_podcast`, `has_report`, `source_ids`, etc.
6. Exit 1 with helpful stderr message if nothing found

### Fetch & Deliver Script

All fetch pipeline steps are encapsulated in a single script. TOOLS.md calls this one command — the LLM does not execute individual steps:

```bash
bash scripts/fetch_and_deliver.sh <category> <artifact>
# e.g.: bash scripts/fetch_and_deliver.sh research report
```

Internally, the script handles: `fetch_latest.py` → branch on category → NLM auth → download → `enhance_report.py` → `pdf_renderer.py` → copy to workspace → `openclaw message send` → summary output.

## Report Enhancement Pipeline

After downloading a report from NotebookLM, two post-processing scripts transform it into a polished PDF.

### Step 1: `scripts/enhance_report.py`

Three deterministic operations:

**1. Remove extra TL;DRs** (line 26-54):
NotebookLM sometimes generates per-section TL;DR blockquotes. The enhancer keeps only the first one (report-level) and strips the rest.
```python
# Regex matches: > **TL;DR** ... (multiline blockquote)
# Removes all matches after the first one, working backwards
```

**2. Append source links** (line 57-99):
Reads `data/sources.json`, builds a `## Source Links` section with clickable markdown links:
```markdown
---

## Source Links

- [Paper Title](https://arxiv.org/abs/...) *(ml, newsletter)*
- [Repo Name](https://github.com/...) *(oss, telegram)*
```

**3. Validate headings** (line 102-124):
Warns about `h4+` headings (should be h2/h3 only) and orphan h3s (h3 without a preceding h2).

```bash
# Usage
.venv/bin/python3 scripts/enhance_report.py --input report.md
.venv/bin/python3 scripts/enhance_report.py --input report.md --dry-run
```

### Step 2: `pdf_renderer.py`

Converts enhanced markdown to a styled PDF using markdown-it-py + WeasyPrint.

**Pipeline** (pdf_renderer.py:170-234):
```
Markdown → HTML (markdown-it-py)
    → Style report TL;DR blockquote (add class="report-tldr" for green accent)
    → Inject heading IDs on h2/h3 tags
    → Extract heading descriptions (first sentence of next paragraph)
    → Build clickable Table of Contents
    → Insert TOC after title + TL;DR
    → Wrap in HTML template with CSS
    → WeasyPrint → PDF
```

**TOC generation** (line 124-149):
- Collects all h2/h3 headings
- Each h2 entry includes a one-line description (first sentence of the section)
- Clickable anchor links to each section

**TL;DR styling** (line 152-167):
The first blockquote containing "TL;DR" gets a `report-tldr` CSS class for green accent styling. Other blockquotes stay blue (default).

**CSS files**:
- `templates/research-report.css` — Used for research reports (preferred)
- `templates/daily-feed.css` — Used for daily feed PDFs (fallback)

```bash
# Usage
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python3 pdf_renderer.py \
  --input report.md --output report.pdf

# With custom CSS
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python3 pdf_renderer.py \
  --input report.md --css templates/custom.css
```

**macOS WeasyPrint requirement**: Needs `pango` (`brew install pango`) and the `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` prefix because uv-managed Python doesn't automatically find Homebrew libraries.

## Report Creation via NotebookLM

### Custom Prompt Template

Reports are generated with a custom prompt (not the default NotebookLM format):

```bash
bash scripts/nlm_create_report.sh <notebook-id-or-alias>
```

This wrapper reads `templates/report-prompt.txt` and runs:
```bash
~/.local/bin/nlm report create <id> --format "Create Your Own" --prompt "<template>" --confirm
```

The template produces:
- Single report-level TL;DR (bold blockquote at top)
- h2 section headings with first-sentence summaries
- h3 sub-headings for detailed points
- No h4+ headings
- Citation-backed claims from the notebook sources

### Polling for Completion

After initiating generation:
```bash
~/.local/bin/nlm studio status <notebook-id>
# Poll every 30 seconds until "complete"
# Timeout after 15 minutes
```

### Downloading

```bash
# Report (markdown)
~/.local/bin/nlm download report <notebook-id> --output ~/.openclaw/workspace/report.md

# Podcast (audio/mp4 from NLM)
# NOTE: `nlm download audio` is broken (TooManyRedirects). Use the workaround:
.venv/bin/python3 scripts/nlm_download_audio.py <notebook-id> --output ~/.openclaw/workspace/podcast.mp4
```

## Podcast Generation

### Formats & Lengths

| Use Case | Format | Length | Description |
|---|---|---|---|
| Weekly research | `deep_dive` | `default` | Two hosts discussing research papers (~15-20 min) |
| Weekly news | `deep_dive` | `default` | Two hosts discussing AI news (~10-20 min) |
| Daily feed | `deep_dive` | `long` | Comprehensive daily coverage |

```bash
~/.local/bin/nlm audio create <notebook-id> --format deep_dive --length default --confirm
```

Other formats available: `brief`, `critique`, `debate`
Other lengths: `short`, `default`, `long`

### Focus Directive (Daily Feed)

The daily feed podcast includes a `--focus` directive:
```bash
~/.local/bin/nlm audio create daily-YYYY-MM-DD \
  --format deep_dive \
  --length long \
  --focus "Cover today's top AI research papers, tech industry news, new open source tools, and important AI policy developments." \
  --confirm
```

## Notebook Date Problem & Solution

### The Problem

When the LLM creates notebooks via the orchestrator or TOOLS.md pipeline, it generates the notebook name including a date string. The LLM sometimes picks wrong dates (e.g., "February 2024" instead of "February 2026") because it hallucinates the date.

### Where Dates Are Computed

| Component | Date Source | Correct? |
|---|---|---|
| `daily_feed_generator.py` | `datetime.now(timezone.utc)` in Python | Yes — deterministic |
| `fetch_latest.py` | Reads `created_at` from notebooks.json | Yes — reads stored value |
| `data_triage.py` | `datetime.now(timezone.utc)` | Yes — deterministic |
| `router.py` | `datetime.now(timezone.utc)` | Yes — deterministic |
| Orchestrator (SKILL.md) | LLM generates date string | **Unreliable** |
| TOOLS.md pipeline | LLM generates date string | **Unreliable** |

### Solution

Notebook names with dates should be computed via shell commands, not LLM generation:

```bash
# In TOOLS.md or SKILL.md, compute date first:
DATE=$(date +%Y-%m-%d)
~/.local/bin/nlm notebook create "Research Digest - $DATE"
```

Or have the orchestrator skill compute it in a Python script and pass it as an argument. The key principle: **never let the LLM generate date strings — always compute them deterministically**.

## Full Weekly Pipeline (Orchestrator)

The notebooklm-orchestrator runs during the Saturday cron and `/process`. For each category group:

```
0. nlm_ensure_auth.sh
1. nlm notebook create "<Category> - Week of YYYY-MM-DD"
2. nlm alias set <slug>-YYYY-MM-DD <id>
3. For each source: nlm source add <id> --url/--file/--youtube/--text --wait
4. Add companion note: nlm source add <id> --text "<note>" --title "..." --wait
5. Generate podcast: nlm audio create <id> --format deep_dive --length default --confirm
6. Generate report: bash scripts/nlm_create_report.sh <id>
7. Poll: nlm studio status <id> (every 30s, timeout 15min)
8. Record in notebooks.json
8.5. Index into ChromaDB: scripts/index_notebook.py --notebook-id <id>
9. Update source statuses: queued → added_to_notebook
```

Then the digest-deliver skill downloads artifacts, enhances reports to PDF, shares notebooks, delivers via Telegram, and logs the run.

## Error Handling Across the Pipeline

| Step | Error | Behavior |
|---|---|---|
| Source addition | URL incompatible with NotebookLM | Log failure, CONTINUE with remaining sources |
| Source addition | Timeout | Log, continue |
| Report generation | Fails | Deliver podcast only, note in summary |
| Podcast generation | Fails | Deliver report only, note in summary |
| Generation timeout | >15 min | Mark as "generating", deliver what's available |
| PDF conversion | WeasyPrint fails | Fall back to delivering .md file |
| RAG indexing | ChromaDB error | Log error, continue — delivery is more important |
| Telegram delivery | Send fails | Retry once, include notebook share link as fallback |
