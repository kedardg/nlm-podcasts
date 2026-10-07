# Bugs

Centralized bug tracker for the OpenClaw Research Automation System.
All bugs should be logged here. Status: `open`, `fixed`, `wontfix`.

NLM authentication is a known limitation, not a bug — do not track it here.

---

## Open

### BUG-018: Deep Dive stubbed every paper — `notebook query` answer moved out of `value`

**Status**: fixed
**Severity**: high — the Deep Dive Review was content-free for ~2 months
**Found**: 2026-10-07
**Fixed**: 2026-10-07
**Component**: `src/pipeline/deep_dive_generator.py`, `src/pipeline/rag_chat.py`

**Symptom**: `2026-10-05-deepdive.md` was 125 words across 3 papers, every one
ending in `_(Limited information available from the notebook sources.)_`. One
paper's entire body was `Nenad Tomasev et al.` For comparison, 2026-06-28 was
2,565 words across 4 papers.

**Root cause**: `nlm notebook query --json` returns the answer at the **top
level** in 0.9.x:

```
keys: ['answer', 'question', 'conversation_id', 'sources_used', 'citations', 'references']
```

Earlier versions nested it as `{"value": {"answer": ...}}`, which is what both
call sites read. The `nlm` binary was replaced on 2026-08-06 (0.9.x is required
since the notebook.google.com migration); the extraction was not updated. So
`nlm_query()` returned `""` for every paper, failed the 600-char content floor,
and fell through to `_stub_explainer()` — whose body comes from the report
summary, which is why the output looked plausible rather than empty.

The query itself was never broken. It had been answering correctly the whole
time, with citations.

**Why it went unnoticed**: an empty answer is indistinguishable from "the
notebook had nothing useful to say", and the stub fallback made every run look
successful. `rag_chat.py` had the same bug but masked differently — it fell
back to raw stdout, handing the caller an entire JSON payload as if it were
prose.

**Fix**: one shared `nlm.client.extract_query_answer()` that accepts both
envelopes, used by both call sites. The legacy path is kept so a pinned or
rolled-back CLI cannot silently reintroduce the stubs. An answerless query now
logs a warning naming the payload keys, so the next envelope change is visible
instead of silent. Covered by `tests/test_nlm_query_envelope.py`.

**Verified**: regenerating 2026-10-06 gives 3,478 words across 5 papers, 0
stubs, all 5 resolving via `[nlm]`.


### BUG-017: hard per-notebook source cap is unenforced on the split path

**Status**: open
**Severity**: low — no observed impact, the guard is simply unreachable
**Found**: 2026-09-29
**Component**: `src/data_triage.py:363-370`

**Description**: The split path computes
`num_splits = ceil(source_count / IDEAL_SPLIT_SIZE)` and then guards each chunk
with `if end - start > MAX_SOURCES_PER_NOTEBOOK`. By construction
`end - start <= IDEAL_SPLIT_SIZE` (15), which is always below
`MAX_SOURCES_PER_NOTEBOOK` (50), so the branch never executes and the hard cap
is enforced only on the non-split path at `:326`.

**Related**: the alias-slash corruption found alongside this is tracked and fixed as BUG-016 below.

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

### BUG-016: Weekly split-notebook slugs corrupt paths and collide; delivery retries podcasts that were never created

**Status**: fixed
**Severity**: medium — Aug 8 weekly ran 8h51m and delivered no split-part podcasts
**Found**: 2026-08-09 (2026-08-08 weekly run)
**Fixed**: 2026-08-09
**Component**: `scripts/weekly_pipeline.sh` (creation + delivery phases)

**Description**: Three interacting problems on the backlog-flush run (~30 sub-notebooks
from a month of queued sources):
1. Slugs from names like `Open Source & Tools (Part 1/20)` kept `(` and `/` (path
   corruption) and the 30-char truncation made different parts collide on the same
   filenames.
2. `has_podcast` was hardcoded `True` in notebooks.json even when `nlm audio create`
   failed — most split-part podcast creations failed (NLM audio quota exhausted by
   ~30 requests in one run).
3. The delivery phase therefore spent ~6 min retrying each download that could never
   exist (~3 wasted hours).

**Fix**: Slugs sanitized (`re.sub(r'[^a-z0-9._-]+', '-')`) with in-run dedup counter;
`has_podcast` now records the actual create result; delivery skips podcast download when
creation failed. Quota note: the ~30-notebook fan-out was a one-time backlog flush —
normal weekly volume is a handful of notebooks. If a flush like that recurs, expect
podcast quota failures again (reports are unaffected).

### BUG-015: Long-audio download fails — media URL propagation outlasts CLI retry budget

**Status**: fixed
**Severity**: medium — daily podcast (~40 min, `--length long`) never downloaded/delivered
**Found**: 2026-08-07 (first seen on the 2026-08-06 run)
**Fixed**: 2026-08-07
**Component**: `scripts/daily_pipeline.sh` Step 6, `scripts/weekly_pipeline.sh` delivery phase

**Description**: After `studio status` reports an audio artifact `completed`, NLM's media
download URL can keep returning 404 ("still propagating") for 10+ minutes on long audio.
The nlm CLI's built-in retry gives up after ~4 minutes, so the daily pipeline's primary
download failed, and both fallbacks were unavailable (no Chrome CDP running; Playwright
session dead). Short deep-dive podcasts in the same run downloaded fine 10 minutes later,
confirming pure propagation delay. Note: a manual re-download later must pass
`--id <artifact-id>` — by then the notebook has multiple audio artifacts and the un-ID'd
download grabs the wrong one (during the pipeline Step 6 runs before the deep dives are
created, so it's unambiguous there).

**Fix**: Daily Step 6 retries `nlm download audio` every 120s for up to 20 min before
falling back to CDP/Playwright. Weekly delivery now uses the nlm CLI as primary (with the
same retry) and the CDP workaround script as fallback (it previously used CDP-only, which
requires a running Chrome debug session).: Cookie rotation dead — can't read Chrome-list `cookies.json` format

**Status**: fixed
**Severity**: high — killed the auth-recovery layer, all daily runs failed at `nlm-auth` Aug 2–5
**Found**: 2026-08-06
**Fixed**: 2026-08-06
**Component**: `src/nlm/nlm_rotate_cookies.py`

**Description**: `load_cookies()` only accepted a `{name: value}` dict, but the nlm CLI (and `nlm login` via CDP) writes `cookies.json` in Chrome DevTools format — a **list** of cookie objects. When the file was rewritten in list format on Aug 1, rotation started misreporting "No cookies found" even though the file existed, so the layer that keeps the Google session alive between logins never ran. Once the PSIDTS went stale, every downstream recovery layer failed too (no Chrome CDP, headless Google login blocked).

**Fix**: `load_cookies()` now accepts both formats (mirrors the CLI's own `parse_cookies_from_chrome_format`). Tests in `tests/test_nlm_rotate_cookies.py`.

**Recovery path when the session is fully dead**: launch Chrome with nlm's dedicated profile (`~/.notebooklm-mcp-cli/chrome-profiles/default`, which holds a persistent Google login) with `--remote-debugging-port=9222`, then `nlm login --provider openclaw --cdp-url http://127.0.0.1:9222`.

### BUG-012: Daily report download races report generation — failed every run since ~Jul 13

**Status**: fixed
**Severity**: medium — daily NLM report silently missing (magazine layout fell back to plain PDF)
**Found**: 2026-08-06
**Fixed**: 2026-08-06
**Component**: `scripts/daily_pipeline.sh` Step 5/6

**Description**: The Step 5 studio poll greps for `in_progress|generating|pending`, but the old CLI's (0.3.15) status output stopped matching those strings after an NLM backend change (~Jul 12), so the poll exited in 0s and Step 6 tried `nlm download report` ~10s after `report create` — always failing ("Download failed for report."). The audio path survived because it has its own readiness loop; the report had none.

**Fix**: Step 6 now waits for the report artifact to reach `"status": "completed"` (up to 20 min, mirroring the audio loop) and retries an empty download once. The new CLI (0.9.6) also emits parseable JSON from `studio status`, which re-enables the Step 5 poll.

### BUG-013: `processing-log.json` never updated — `sys.path` broken by `src/` refactor

**Status**: fixed
**Severity**: low — run history invisible (last daily entry was April), pipelines still ran
**Found**: 2026-08-06
**Fixed**: 2026-08-06
**Component**: `scripts/daily_pipeline.sh`, `scripts/weekly_pipeline.sh`

**Description**: Recurrence of the BUG-005 import-path class: the inline run-record writer did `sys.path.insert(0, '$PROJECT_ROOT')` + `from lib.file_utils import ...`, but the repo restructure moved `lib/` to `src/lib/`. The failure was swallowed by `2>/dev/null || true`.

**Fix**: `sys.path.insert(0, '$PROJECT_ROOT/src')` in both pipelines.

### BUG-014: ffmpeg broken by brew x265 upgrade — podcast compression aborted Jul 30–31

**Status**: fixed (environment)
**Severity**: medium — podcasts downloaded but never compressed/delivered
**Found**: 2026-08-06
**Fixed**: 2026-08-06

**Description**: Homebrew upgraded `x265` to 4.2 (ships `libx265.216.dylib`) while the installed `ffmpeg 8.0.1_4` binary still linked `libx265.215.dylib` → `dyld` abort on every launch (`podcast-compress` step failed with "Abort trap: 6").

**Fix**: `brew reinstall ffmpeg` (now 8.1.2). If it recurs after future brew upgrades, the same reinstall applies.

### BUG-001: `nlm download audio` always fails (TooManyRedirects)

**Status**: fixed upstream (notebooklm-mcp-cli 0.9.6)
**Severity**: high — broke all podcast downloads
**Found**: 2026-02-27
**Fixed**: 2026-08-06 (by upgrading `notebooklm-mcp-cli` 0.3.15 → 0.9.6)
**Component**: `notebooklm-mcp-cli` → `notebooklm_tools/core/download.py`

**Description**: `nlm download audio` failed with `httpx.TooManyRedirects` because httpx stripped cookies on cross-domain redirects through Google's CDN chain (`lh3.googleusercontent.com` → `lh3.google.com` → `googlevideo.com`).

**Fix**: Upstream fixed in the 0.4.9+ line; verified working directly on 0.9.6. The pipeline already tries the CLI first and keeps `scripts/nlm_download_audio.py` (CDP) and Playwright as fallbacks — no pipeline change needed.

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
