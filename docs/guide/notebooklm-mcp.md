# NotebookLM MCP Server

## Overview

NotebookLM is Google's AI-powered research notebook. We use the unofficial `notebooklm-mcp-cli` tool to interact with it programmatically — creating notebooks, adding sources, generating podcasts and reports.

**Important**: This uses undocumented Google APIs via cookie-based authentication. Sessions expire after ~20 minutes.

## Installation

```bash
uv tool install notebooklm-mcp-cli
~/.local/bin/nlm login          # Interactive browser login
~/.local/bin/nlm setup add claude-code  # Register as MCP server (optional, not used in pipeline)
```

The `nlm` binary installs to `~/.local/bin/nlm`. The gateway doesn't source `~/.zshrc`, so all pipeline commands use the full path.

## Authentication Flow

```
nlm login
    │
    ├── Opens Chrome browser
    ├── User signs into Google account
    ├── Captures authentication cookies
    ├── Stores in ~/.notebooklm-mcp/
    │
    ▼
Session valid for ~20 minutes
    │
    ├── nlm login --check  → verifies if still valid
    ├── nlm login           → headless refresh (if cookies still exist)
    │
    ▼
scripts/nlm_ensure_auth.sh (called before every pipeline step)
    ├── Runs: nlm login --check
    ├── If valid → exit 0
    ├── If expired → runs: nlm login (headless refresh)
    ├── If refresh fails → exit 1 (pipeline stops)
```

The `nlm_ensure_auth.sh` script is called at the start of every pipeline that uses NotebookLM:

```bash
#!/bin/bash
NLM="$HOME/.local/bin/nlm"
if [ ! -f "$NLM" ]; then
  echo "ERROR: nlm not found at $NLM"
  exit 1
fi
if $NLM login --check >/dev/null 2>&1; then
  echo "nlm auth: valid"
  exit 0
fi
echo "nlm auth: expired, re-authenticating..."
if $NLM login 2>&1; then
  echo "nlm auth: refreshed"
  exit 0
fi
echo "ERROR: nlm login failed"
exit 1
```

For managed browser authentication through OpenClaw:
```bash
nlm login --provider openclaw --cdp-url http://127.0.0.1:18800
```

## CLI Commands We Use

### Notebook Management

```bash
# Create notebook
~/.local/bin/nlm notebook create "Research Topic Name"
# Returns: notebook ID (e.g., "abc123def456")

# Set alias for easy reference
~/.local/bin/nlm alias set research abc123def456

# List all notebooks
~/.local/bin/nlm notebook list --json

# Query a notebook (ask questions about its sources)
~/.local/bin/nlm notebook query research "Summarize the key findings"

# Share publicly
~/.local/bin/nlm share public abc123def456
# Returns: public URL
```

### Source Addition

```bash
# URL source (web page, arXiv paper, blog post)
~/.local/bin/nlm source add research --url "https://arxiv.org/abs/2301.12345" --wait

# YouTube video
~/.local/bin/nlm source add research --youtube "https://youtube.com/watch?v=..." --wait

# Local PDF file
~/.local/bin/nlm source add research --file paper.pdf --wait

# Inline text (companion notes, summaries)
~/.local/bin/nlm source add research --text "content here" --title "My Notes" --wait
```

**Critical**: Always use `--wait` to ensure processing completes before adding the next source. Without it, NotebookLM may get overwhelmed with parallel processing.

**YouTube gotcha**: Only use `--youtube` for actual video URLs (`watch?v=...`, `/shorts/...`). Do NOT use it for channel pages, playlist pages, or subscribe links — these will fail.

### Content Studio (Audio, Reports, etc.)

```bash
# Generate podcast
~/.local/bin/nlm audio create research --format deep_dive --length default --confirm

# Generate report with custom prompt
~/.local/bin/nlm report create research --format "Create Your Own" --prompt "<prompt>" --confirm

# Check generation status
~/.local/bin/nlm studio status research

# Download artifacts
# NOTE: `nlm download audio` is broken (TooManyRedirects). Use the workaround:
.venv/bin/python3 scripts/nlm_download_audio.py research --output podcast.mp4
~/.local/bin/nlm download report research --output report.md
```

Audio formats: `deep_dive`, `brief`, `critique`, `debate`
Audio lengths: `short`, `default`, `long`

**Audio download**: `nlm download audio` fails because httpx strips cookies on cross-domain redirects through Google's CDN chain. Use `scripts/nlm_download_audio.py` instead — it extracts Chrome CDP cookies and follows redirects with domain-aware cookie matching. Requires Chrome with `--remote-debugging-port`. NLM outputs audio as `audio/mp4` — use `.mp4` extension for raw files.

### Report Generation Wrapper

We use a wrapper script (`scripts/nlm_create_report.sh`) that reads a custom prompt template:

```bash
bash scripts/nlm_create_report.sh <notebook-id-or-alias>
```

This reads `templates/report-prompt.txt` and passes it to:
```bash
~/.local/bin/nlm report create <id> --format "Create Your Own" --prompt "<template>" --confirm
```

The template produces reports with a single report-level TL;DR, section summaries as first sentences, and h2/h3-only headings.

## Notebook Date Issue

**Problem**: When the LLM creates notebooks, it sometimes picks incorrect dates for the notebook name (e.g., "Research Digest - February 2024" when it should be "February 2026"). This happens because the LLM generates the date string and may hallucinate.

**Solution approaches**:
1. **Deterministic date in scripts**: The `fetch_latest.py` and pipeline scripts compute dates in Python (`datetime.now()`), not the LLM
2. **TOOLS.md explicit date**: The pipeline instructions should compute the date via a shell command (`date +%Y-%m-%d`) rather than asking the LLM to generate it
3. **Post-creation validation**: The orchestrator can verify the notebook name's date against `datetime.now()` after creation

In `daily_feed_generator.py`, dates are computed deterministically:
```python
today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
```

The weekly orchestrator should follow the same pattern: compute dates in Python/bash, pass them as arguments to `nlm` commands, and never let the LLM generate date strings.

## Data Model: notebooks.json

Every notebook created is recorded in `data/notebooks.json`:

```json
{
  "id": "abc123def456",
  "alias": "ml-2026-02-15",
  "category": "ml",
  "category_name": "Machine Learning",
  "created_at": "2026-02-15T09:30:00Z",
  "source_count": 8,
  "source_ids": ["src_001", "src_002", "src_003"],
  "has_podcast": true,
  "has_report": true,
  "podcast_artifact_id": "xyz789",
  "report_artifact_id": "uvw456",
  "status": "complete",
  "run_id": "run_20260215_093000"
}
```

Status values: `complete`, `generating`, `error`

## MCP Server Mode (Not Used in Pipeline)

When registered via `nlm setup add claude-code`, NotebookLM becomes available as an MCP server with 29 tools. However, our pipeline uses the CLI directly (not MCP) because:
1. The OpenClaw gateway agent runs `nlm` via shell commands in TOOLS.md
2. MCP registration is for Claude Code interactive use, not automated pipelines
3. CLI commands give more predictable, scriptable behavior

## Common Issues

### "NotebookLM authentication failed"
- Session expired (>20 min idle). Run: `~/.local/bin/nlm login`
- For automated re-auth: `scripts/nlm_ensure_auth.sh` handles this

### Source addition fails
- Some URLs are incompatible (JS-heavy pages, paywalled content, URLs with complex query parameters)
- YouTube channel pages, playlist pages, and subscribe links fail with `--youtube`
- **Error handling policy**: Log the failure and CONTINUE with remaining sources. Don't stop the pipeline.

### Podcast/report generation stuck
- `nlm studio status <id>` returns "generating" for >15 minutes
- Workaround: Mark as "generating" in notebooks.json, deliver whatever was successful, note in summary

### `nlm` not found
- The gateway doesn't source `~/.zshrc`, so `~/.local/bin` isn't in PATH
- Always use full path: `~/.local/bin/nlm`
- Verify: `~/.local/bin/nlm --version`
- Diagnose: `~/.local/bin/nlm doctor --verbose`
