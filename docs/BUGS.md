# Bugs

Centralized bug tracker for the OpenClaw Research Automation System.
All bugs should be logged here. Status: `open`, `fixed`, `wontfix`.

NLM authentication is a known limitation, not a bug — do not track it here.

---

## Open

### BUG-001: `nlm download audio` always fails (TooManyRedirects)

**Status**: open (upstream bug in notebooklm-mcp-cli)
**Severity**: high — breaks all podcast downloads
**Found**: 2026-02-27
**Component**: `notebooklm-mcp-cli` → `notebooklm_tools/core/download.py`

**Description**: `nlm download audio` fails with `httpx.TooManyRedirects` because httpx strips cookies on cross-domain redirects. Google's CDN chain is: `lh3.googleusercontent.com` → `lh3.google.com` → `googlevideo.com` — each hop needs domain-matched cookies that httpx drops.

The error is hidden by a generic `ServiceError("Download failed for audio.")` in `notebooklm_tools/services/downloads.py:175-179`.

**Workaround 1 (CDP)**: `scripts/nlm_download_audio.py` — extracts cookies from Chrome via CDP (`Network.getAllCookies`), manually follows the redirect chain with domain-aware cookie matching using `requests`. Requires Chrome running with `--remote-debugging-port` (any port in 9222-9231).

**Workaround 2 (Playwright)**: `scripts/nlm_playwright.py` — drives NLM's web UI via Playwright browser automation. Downloads audio using `page.expect_download()` which handles the full redirect chain natively (browser manages cookies). Use `--use-playwright` flag in `daily_pipeline.sh`. No Chrome CDP required — Playwright manages its own Chromium instance.

**Upstream fix**: Would need httpx cookie policy change or NLM using `requests` instead of `httpx` for audio downloads.

### BUG-002: 400 "reasoning" error crashes OpenClaw sessions

**Status**: open (upstream fix in OpenClaw 2026.2.26, update pending)
**Severity**: high — blocks all pipeline commands from Telegram
**Found**: 2026-02-28
**Component**: OpenClaw gateway → `pi-embedded-runner/google.ts`

**Description**: OpenAI reasoning models (GPT-5-mini with `thinking=low`) emit encrypted `reasoning` items in session history. When using the same model continuously, `downgradeOpenAIReasoningBlocks()` only runs when `modelChanged` is true, so orphaned reasoning blocks accumulate. On next API call, OpenAI rejects with `400 Item of type 'reasoning' was provided without its required following item`.

**Workaround**: Clear sessions — `echo '{}' > ~/.openclaw/agents/main/sessions/sessions.json` and delete `.jsonl` files, then restart gateway.

**Fix**: OpenClaw [#19121](https://github.com/openclaw/openclaw/issues/19121) — commit `46bf210e` makes reasoning-block cleanup unconditional. Ships in 2026.2.26+. Run `npm install -g openclaw@latest` to update.

### BUG-006: 159 sources stuck in "processing" since Feb 18

**Status**: fixed
**Severity**: medium — queued items never re-enter pipeline
**Found**: 2026-03-05
**Fixed**: 2026-03-05

**Description**: The weekly pipeline cron message told the LLM to "Execute newsletter-monitor skill" instead of running `bash weekly_pipeline.sh` directly. Both `gpt-5-mini` and `gpt-4.1-mini` timed out at 32s trying to interpret the skill-based instructions. This meant the `content-processor` marked sources as "processing" during partial runs, but the NLM orchestrator never completed — leaving 159 items stuck across Feb 18 – Feb 28 with `status: "processing"` and no `notebook_id`.

**Root cause**: Cron message format mismatch. The daily pipeline works because its message says `"Execute: bash .../daily_pipeline.sh"` (direct command). The weekly message was using skill names which require LLM interpretation.

**Fix**:
1. Fixed cron message in `config/cron-jobs.json` to use direct bash command
2. Reset all 159 stuck items to `status: "queued"` with `reset_reason: "stuck_processing_no_notebook_id_weekly_pipeline_broken"`
3. Added auto-reset safeguard in `data_triage.py` — items stuck in "processing" >24h with no notebook_id are auto-reset to "queued"

### BUG-007: `NameError: _open_studio_panel` crashes podcast generation

**Status**: fixed
**Severity**: high — podcast step silently fails, daily pipeline produces no audio
**Found**: 2026-03-10
**Fixed**: 2026-03-10
**Component**: `scripts/nlm_playwright.py` → `generate_report()` → `_open_studio_panel()`

**Description**: `generate_report()` called `_open_studio_panel(page)` to open the NotebookLM studio panel before generating a podcast, but the function was never defined. Raised `NameError: name '_open_studio_panel' is not defined`, crashing the podcast step silently.

**Fix**: Added `_open_studio_panel(page)` definition in `nlm_playwright.py` — clicks the Studio tab, waits for the panel to load, handles already-open state gracefully.

### BUG-008: Daily feed flooded with 200+ news articles from newsletter batch runs

**Status**: fixed
**Severity**: high — daily feed sections show 100+ items instead of ~10-15; NLM briefing text bloated
**Found**: 2026-03-10
**Fixed**: 2026-03-10
**Component**: `daily_feed_generator.py` → `collect_today_items()`

**Description**: `collect_today_items()` collected news articles from `news-queue.json` with no cap. News articles use `extracted_at` (set when the newsletter parser processes emails). When the pipeline's Step 0 (newsletter-monitor) ran and batch-extracted 213 articles from multiple newsletters, all were stamped with today's `extracted_at` and all appeared in the daily feed, flooding every section.

**Fix**: Added `NEWS_DAILY_LIMIT = 40` constant. After deduplication, news-queue items exceeding the limit are trimmed to the most recent 40 by `extracted_at`. Research items from `sources.json` are never capped.

### BUG-010: Cron pipelines fail with "Operation not permitted" on external volume

**Status**: fixed
**Severity**: high — all cron pipelines blocked
**Found**: 2026-03-12
**Fixed**: 2026-03-14
**Component**: OpenClaw daemon → cron → pipeline scripts on `/Volumes/ext`

**Description**: macOS TCC (Transparency, Consent, and Control) blocks the OpenClaw daemon from accessing files on the external APFS volume `/Volumes/ext` (mounted with `noowners,nosuid,nodev`). The daemon's launchd process lacks Full Disk Access, so all `source` and `bash` commands targeting the external volume fail with "Operation not permitted". This is an OS-level privacy gate, not a Unix permissions issue.

Compounding factor: the LLM agent wraps pipeline commands with `source config/env.sh` despite TOOLS.md saying not to, causing the error to surface before the pipeline script's own fallback can run.

**Fix**: Dockerized the entire system. The project runs inside a container where there are no TCC restrictions. Updated `Dockerfile` (full `requirements.txt`, Playwright install), `docker-compose.yml` (workspace mounts, extensions, healthcheck, restart policy), and `config/cron-jobs.json` (paths changed from `/Volumes/ext/...` to `/app/...`).

### BUG-009: `source_count` always 0 in notebooks.json for daily notebooks

**Status**: fixed
**Severity**: low — cosmetic; actual notebook sources are unaffected
**Found**: 2026-03-10
**Fixed**: 2026-03-10
**Component**: `scripts/daily_pipeline.sh` → Step 10 (notebook registration)

**Description**: The daily notebook registry entry was hardcoded with `'source_count': 0` at Step 10. The shell `$ADDED` counter (correctly incremented in Step 3) was never interpolated into the Python registration block, so all daily notebooks appeared to have 0 sources even when 15+ had been successfully added (confirmed: today's notebook had 16 actual sources via `nlm source list`).

**Fix**: Changed hardcoded `'source_count': 0` to `'source_count': ${ADDED:-0}` in the Step 10 inline Python. `${ADDED:-0}` safe-defaults to 0 if Step 3 was skipped due to auth failure.

---

## Fixed

### BUG-005: `flock` not available on macOS — pipeline lock always fails

**Status**: fixed
**Severity**: medium — blocks pipeline execution on macOS
**Found**: 2026-03-07
**Fixed**: 2026-03-07
**Component**: `scripts/weekly_pipeline.sh`, `scripts/daily_pipeline.sh`

**Description**: Both pipeline scripts used `flock` (Linux-only) for mutual exclusion locking. On macOS, `flock` is not available, causing `command not found` error and exit code 1. This prevented both pipelines from running on the development machine.

**Fix**: Replaced `flock -n` with `mkdir`-based atomic locking (POSIX-compatible). Added stale lock detection (>2 hours) and EXIT trap cleanup. Uses `stat -f %m` (macOS) with `stat -c %Y` (Linux) fallback for modification time.

### BUG-003: `daily_pipeline.sh` timing arithmetic fails on macOS

**Status**: fixed
**Severity**: low — timing metrics broken, pipeline still runs
**Found**: 2026-03-02
**Fixed**: 2026-03-02

**Description**: `date +%s%3N` is not valid on macOS — `%3N` is not a recognized format specifier, producing a trailing literal `N` (e.g., `17723196573N`). When used in bash arithmetic `$((now - start))`, it fails with "value too great for base".

**Fix**: Replaced with `millis()` helper using Python: `python3 -c 'import time;print(int(time.time()*1000))'`.

### BUG-004: RAG indexing fails — "notebook not found in registry"

**Status**: fixed
**Severity**: medium — RAG index skipped for daily notebooks
**Found**: 2026-03-02
**Fixed**: 2026-03-02

**Description**: Step 6.5b (RAG indexing) ran before Step 10 (notebook registration in `notebooks.json`). `index_notebook.py` looks up the notebook by ID in the registry, which doesn't exist yet.

**Fix**: Moved RAG indexing to Step 10.5, after notebook registration.

### BUG-005: Broken `from file_utils` imports in pipeline shell scripts

**Status**: fixed
**Severity**: low — only affects processing-log.json write (pipeline still runs)
**Found**: 2026-03-02
**Fixed**: 2026-03-02

**Description**: After moving utilities to `lib/`, inline Python in `daily_pipeline.sh` and `weekly_pipeline.sh` still used `from file_utils import ...` instead of `from lib.file_utils import ...`. Silently failed (guarded by `|| true`), so `processing-log.json` was never updated.
