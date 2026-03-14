---
title: "fix: Repo-Wide Reliability Sweep"
type: fix
status: active
date: 2026-03-05
origin: docs/brainstorms/2026-03-05-repo-reliability-sweep-brainstorm.md
deepened: 2026-03-05
---

# fix: Repo-Wide Reliability Sweep

## Enhancement Summary

**Deepened on:** 2026-03-05
**Agents used:** kieran-python-reviewer, kieran-typescript-reviewer, performance-oracle, repo-research-analyst, learnings-researcher, spec-flow-analyzer

### Key Improvements from Review

1. **Python code quality**: All proposed functions must have type hints, consistent `bool` return contracts (no raise-on-failure), imports at module top, magic numbers extracted as constants. **Type hints policy**: new/modified functions are typed; existing functions not retroactively typed.
2. **Gateway plugin simplified**: Keep `getGatewayRssMb()` synchronous (fast local operation); only convert NLM auth functions to async; verify OpenClaw `api.on` supports async handlers before converting `session_start` hook. No new TypeScript interfaces — the plugin is 239 lines with one consumer.
3. **Performance hardening**: Scan all 3 CDP ports during probe; add per-group auth re-check in weekly Step 5; increase weekly auth retry limit from 2 to 5; use poll timeout of 3600s (1 hour); build URL set for O(1) dedup in x_monitor.py; use `urlparse` for auth expiry detection (not substring check)
4. **Security hardening**: Enforce `chmod 600` on all credential/cookie files; keep CDP opt-in only (no auto-launch); extend atomic write fixes to `nlm_rotate_cookies.py`

## Overview

Comprehensive reliability fix across the entire OpenClaw Research Automation pipeline — 16 issues spanning critical NameErrors that crash Playwright, unsafe JSON writes that can corrupt state, an 8-minute sleep bug, pipeline steps that silently skip, and a gateway plugin that blocks the event loop for 70 seconds. The goal: both daily and weekly pipelines run unattended without silent failures.

## Problem Statement

The system has accumulated reliability debt across multiple layers:

- **Playwright automation** (`scripts/nlm_playwright.py`) — 4 bugs prevent unattended operation: missing functions crash report generation, `time.sleep(500)` hangs 8 minutes per notebook, report generation fires unconditionally and hits the missing functions
- **Pipeline scripts** — unsafe `json.dump(open(...))` can corrupt `notebooks.json` on crash; `${ERRORS[*]}.split()` garbles error messages with spaces; both daily AND weekly pipelines are affected
- **Weekly pipeline gaps** — x-monitor and news digest steps always skip because they require an interactive agent session
- **Gateway plugin** — hardcoded paths, synchronous shell calls block the event loop up to 70s, dead code
- **Documentation** — TOOLS.md doesn't mention Playwright

## Proposed Solution

Fix all 16 issues in 6 phases following the dependency graph from the brainstorm (see brainstorm: `docs/brainstorms/2026-03-05-repo-reliability-sweep-brainstorm.md`).

## Technical Approach

### Architecture

No architectural changes. All fixes operate within existing patterns:
- Playwright functions follow the `func(page, ..., record, timeout=N)` pattern
- Shell pipeline scripts use `save_json()` from `lib/file_utils.py`
- Gateway plugin uses `api.on()` hooks and `api.registerService()`
- New Python scripts follow existing patterns in `scripts/` (argparse CLI, `StepTimer` logging, atomic JSON I/O)

### Implementation Phases

#### Phase 0: Security Baseline

Before any other changes, enforce restrictive permissions on all credential and cookie files.

**New: Credential file permissions (chmod 600)**

Multiple scripts write sensitive files using `Path.write_text()` or `open()`, inheriting the default umask (`0644` — world-readable). This exposes Google session cookies and credentials to any local process.

Affected files:
- `scripts/nlm_rotate_cookies.py:67` — cookies written via `Path.write_text()`
- `config/google-credentials.json` — plaintext credentials (also: remove unused `password` field from template)
- `~/.notebooklm-chrome/` — browser profile directory with session state

Fix:
```python
# After every sensitive file write:
import os
os.chmod(path, 0o600)

# For directories:
os.makedirs(profile, mode=0o700, exist_ok=True)
```

Extend `lib/file_utils.py::save_json()` to accept an optional `mode` parameter for sensitive files.

- [ ] Add `os.chmod(path, 0o600)` after cookie writes in `scripts/nlm_rotate_cookies.py`
- [ ] Create browser profile directory with `mode=0o700`
- [ ] Remove unused `password` field from `config/google-credentials.json` template
- [ ] Add optional `mode` parameter to `save_json()` for sensitive files

#### Phase 1: Quick Wins (Issues #3, #12, #13)

Zero-dependency fixes that immediately improve reliability. Can be done in any order.

**Issue #3: Update OpenClaw to fix BUG-002**

```bash
npm install -g openclaw@latest
# Verify: openclaw --version should show >= 2026.2.26
openclaw daemon restart
```

After update, clear orphaned reasoning blocks:
```bash
echo '{}' > ~/.openclaw/agents/main/sessions/sessions.json
rm -f ~/.openclaw/agents/main/sessions/*.jsonl
```

Update `docs/BUGS.md`: change BUG-002 status from `open` to `fixed`.

- [ ] Run `npm install -g openclaw@latest`
- [ ] Verify version >= 2026.2.26
- [ ] Clear stale sessions
- [ ] Restart gateway
- [ ] Update BUGS.md

**Issue #12: Fix `time.sleep(500)` at `scripts/nlm_playwright.py:368`**

```python
# Before (line 368):
time.sleep(500)

# After:
time.sleep(0.5)
```

Single-character fix. Every notebook creation currently hangs 8+ minutes.

- [ ] Change `time.sleep(500)` to `time.sleep(0.5)` at `scripts/nlm_playwright.py:368`

**Issue #13: Make report generation opt-in at `scripts/nlm_playwright.py:1187,1210`**

```python
# Before (line 1187):
if args.report_prompt or args.create:

# After:
if args.report_prompt:
```

Same change at line 1210 (download section). Combined with Issue #1 (missing `_open_studio_panel`), the `or args.create` clause crashes every `--create` invocation.

- [ ] Change `if args.report_prompt or args.create:` to `if args.report_prompt:` at line 1187
- [ ] Change `if args.report_prompt or args.create:` to `if args.report_prompt:` at line 1210

#### Phase 2: Playwright Core Fixes (Issues #2, #4, #1)

These require auth to be working for testing. Issue #4 enables testing of #2 and #1.

**Issue #4: Playwright auth bootstrapping — CDP stays opt-in**

The `create_browser_cdp()` function exists (lines 172-197) but requires manually starting Chrome. **Security decision: CDP remains strictly opt-in** — auto-launching Chrome with `--remote-debugging-port` expands the unauthenticated attack surface (any local process can connect to CDP ports and steal cookies, navigate pages, execute JS).

Auth approach:
1. **Primary**: Manual headed login once (`--login`), session persists in `~/.notebooklm-chrome/`
2. **Secondary**: User starts Chrome with CDP, passes `--cdp-url` explicitly
3. **No auto-launch**: Do NOT auto-start Chrome with CDP by default

Add a `_probe_cdp()` helper for the `--cdp-url auto` convenience shortcut:

```python
import urllib.request
import json

CDP_PORTS: list[int] = [9222, 9223, 9224]
CDP_PROBE_TIMEOUT_S: int = 2

def _probe_cdp(port: int, timeout_s: int = CDP_PROBE_TIMEOUT_S) -> bool:
    """Check if Chrome CDP is responding on the given port."""
    try:
        resp = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=timeout_s
        )
        if resp.status != 200:
            return False
        # Validate response is actually a Chrome CDP endpoint
        data = json.loads(resp.read())
        return "Browser" in data and ("Chrome" in data["Browser"] or "Chromium" in data["Browser"])
    except Exception:
        return False
```

CDP port list should be defined in one place (`config/env.sh: CDP_PORTS="9222 9223 9224"`) and read by all scripts.

Update `daily_pipeline.sh` to optionally probe for an already-running Chrome:

```bash
# Before Playwright step: check if Chrome CDP is already running (do NOT auto-launch)
if [[ "$USE_PLAYWRIGHT" == "true" ]]; then
    CDP_URL=$($PYTHON -c "
import urllib.request, json
for port in [9222, 9223, 9224]:
    try:
        r = urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version', timeout=2)
        d = json.loads(r.read())
        if 'Browser' in d: print(f'http://127.0.0.1:{port}'); break
    except: pass
" 2>/dev/null)
    if [[ -n "$CDP_URL" ]]; then
        PW_CMD+=(--cdp-url "$CDP_URL")
    fi
fi
```

- [ ] Add `_probe_cdp()` with response body validation to `scripts/nlm_playwright.py`
- [ ] Add `--cdp-url auto` option that probes existing ports (no auto-launch)
- [ ] Define `CDP_PORTS` in `config/env.sh` as single source of truth
- [ ] Update `daily_pipeline.sh` to probe (not launch) Chrome CDP

**Issue #2: Fix `add_url_source` selector — add explicit UI wait**

The "add source button not found" error happens because the function tries to click before the notebook UI has fully loaded.

Add an explicit wait at the start of `add_url_source()` (before line 432):

```python
def add_url_source(page, url, record, timeout=120):
    """Add a URL source to the current notebook."""
    with StepTimer("add_url_source"):
        # Wait for notebook UI to be fully loaded before interacting
        try:
            page.wait_for_selector(
                'button:has-text("Add source"), [aria-label*="Add source"]',
                state="visible", timeout=15000
            )
        except Exception:
            log("  Warning: Add source button not immediately visible, waiting for page load...")
            page.wait_for_load_state("networkidle", timeout=10000)

        # ... existing selector logic ...
```

- [ ] Add explicit wait for notebook UI at start of `add_url_source()`
- [ ] Add `page.wait_for_load_state("networkidle")` fallback

**Issue #1: Implement `_open_studio_panel()` and `_fill_custom_prompt()`**

These functions are called at lines 592 and 614 but never defined. No reference implementations exist — the selector reference doc lists report generation as a "Missing Operation."

**BLOCKED**: Requires manual browser inspection of NLM's Studio panel UI to discover selectors. Do not write speculative selectors — they will be wrong.

**Implementation rules** (follow `generate_podcast()` pattern at lines 639-722):
- Signatures: `_open_studio_panel(page, record, timeout=30)` and `_fill_custom_prompt(page, prompt_text, record, timeout=30)` — must include `record` parameter for operations logging
- Return `bool` (True=success, False=failure). Never raise exceptions.
- Caller uses `return False` pattern, not `raise RuntimeError`:
  ```python
  if not _open_studio_panel(page, record):
      record.errors.append("generate_report: could not open Studio panel")
      return False
  ```
- Extract selectors as module-level tuples for easy updates
- Log exceptions: `except Exception as exc: log(...)` (no silent swallowing)

**Verification process**:
1. Connect to NLM via CDP or `--login`
2. Open browser DevTools, navigate to a notebook with sources
3. Find Studio panel trigger element and report configuration UI
4. Implement functions with verified selectors
5. Update `docs/guide/nlm-playwright-selectors.md`

- [ ] Verify selectors via manual browser inspection (BLOCKING)
- [ ] Implement `_open_studio_panel(page, record, timeout=30)` with verified selectors
- [ ] Implement `_fill_custom_prompt(page, prompt_text, record, timeout=30)` with verified selectors
- [ ] Test report generation end-to-end
- [ ] Update `docs/guide/nlm-playwright-selectors.md` with new selectors

#### Phase 3: Pipeline Safety (Issues #7, #8)

Independent fixes that prevent data corruption. Safe to do at any time.

**Test plan**: After applying Phase 3 changes, verify with `bash scripts/weekly_pipeline.sh --dry-run` and check `data/processing-log.json` for valid JSON output. Inline Python in shell heredocs is not covered by pytest — manual verification required.

**Issue #7: Replace unsafe `json.dump(open(...))` with `save_json()`**

Both daily AND weekly pipelines have this pattern. SpecFlow analysis confirmed the daily pipeline also has an unsafe write at line 597-598.

**`scripts/weekly_pipeline.sh`**:

Line 278 (step 5 orchestrator):
```python
# Before:
json.dump(notebooks_registry, open(f'{project}/data/notebooks.json', 'w'), indent=2)

# After:
from lib.file_utils import save_json
save_json(f'{project}/data/notebooks.json', notebooks_registry)
```

The inline Python block starting around line 157 needs `sys.path.insert(0, project)` and `from lib.file_utils import save_json` added to its imports. Check if `sys.path.insert` is already present.

Line 453 (step 7 delivery):
```python
# Before:
json.dump(notebooks, open(f'{project}/data/notebooks.json', 'w'), indent=2)

# After:
save_json(f'{project}/data/notebooks.json', notebooks)
```

Same import addition needed in this inline Python block.

**`scripts/daily_pipeline.sh`**:

Lines 597-598 (notebook registration):
```python
# Before:
with open(registry_path, 'w') as f:
    json.dump(registry, f, indent=2)

# After:
from lib.file_utils import save_json
save_json(registry_path, registry)
```

- [ ] Fix `weekly_pipeline.sh:278` — add `save_json` import + replace `json.dump`
- [ ] Fix `weekly_pipeline.sh:453` — add `save_json` import + replace `json.dump`
- [ ] Fix `daily_pipeline.sh:597-598` — add `save_json` import + replace `with open`
- [ ] Fix `scripts/nlm_rotate_cookies.py:67` — replace `Path.write_text()` with `save_json()` (same unsafe pattern, most sensitive data)
- [ ] Verify all inline Python blocks have `sys.path.insert(0, project)` for lib imports

**Issue #8: Fix `${ERRORS[*]}` bash-to-JSON conversion**

Both pipelines have this bug. `'${ERRORS[*]}'.split()` breaks error messages with spaces into multiple items.

**`scripts/weekly_pipeline.sh:496`** and **`scripts/daily_pipeline.sh:641`**:

```python
# Before:
'errors': $(python3 -c "import json; print(json.dumps('${ERRORS[*]}'.split() if '${ERRORS[*]}' else []))"),

# After — use heredoc to pass array elements properly:
'errors': $(python3 << 'PYEOF'
import json, sys
errors = []
for line in sys.stdin:
    line = line.strip()
    if line:
        errors.append(line)
print(json.dumps(errors))
PYEOF
<<< "$(printf '%s\n' "${ERRORS[@]}")"),
```

Alternative simpler approach — use `"${ERRORS[@]}"` with proper quoting and a Python script that reads newline-delimited input:

```bash
'errors': $(printf '%s\n' "${ERRORS[@]}" | python3 -c "import json,sys; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))"),
```

- [ ] Fix `weekly_pipeline.sh:496` — pipe `"${ERRORS[@]}"` through proper JSON encoder
- [ ] Fix `daily_pipeline.sh:641` — same fix

#### Phase 4: Gateway Plugin (Issues #9, #10)

Independent fixes to `~/.openclaw/extensions/gateway-health/index.ts`.

**Issue #9: Make `PROJECT_ROOT` configurable**

```typescript
// Before (line 4):
const PROJECT_ROOT = "/Volumes/ext/projects/openclaw-research";

// After:
// Fallback to local dev path when PROJECT_ROOT is not set (macOS only).
// Docker sets PROJECT_ROOT=/app via Dockerfile.
const PROJECT_ROOT = process.env.PROJECT_ROOT || "/Volumes/ext/projects/openclaw-research";
```

Also remove dead `ENSURE_SCRIPT` variable (line 8) — it's defined but never used:

```typescript
// Remove this line:
const ENSURE_SCRIPT = `${PROJECT_ROOT}/scripts/nlm_ensure_auth.sh`;
```

- [ ] Replace hardcoded `PROJECT_ROOT` with `process.env.PROJECT_ROOT || fallback`
- [ ] Add comment explaining the fallback path is for backward compatibility
- [ ] Remove dead `ENSURE_SCRIPT` constant (line 8)

**Issue #10: Switch NLM auth functions to async `execFile`**

The plugin is 239 lines with one consumer. Keep this simple — mechanical sync-to-async conversion, no new interfaces.

Three changes:
1. Replace `execFileSync` calls with `promisify(execFile)` in `nlmAuthCheck`, `nlmRotate`, `nlmRotateCdp`
2. Remove dead `ENSURE_SCRIPT` constant (line 8)
3. Read `PROJECT_ROOT` from `process.env` with current value as fallback (Issue #9)

```typescript
import { execFile as execFileCb } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFileCb);

// Convert each auth function: execFileSync → await execFileAsync
// Keep stdio: "pipe" and timeout on all calls
// Add .catch on fire-and-forget async calls in gateway_start setTimeout
```

Keep `getGatewayRssMb()` and `tick()` synchronous — they do fast local reads. Replace `catch (err: any)` with `catch (err: unknown)`.

**BLOCKING**: Verify OpenClaw `api.on` supports async handlers before converting `session_start`. If not supported, keep `session_start` synchronous.

- [ ] Replace `execFileSync` with `promisify(execFile)` in auth functions
- [ ] Add `.catch` on `gateway_start` setTimeout async call
- [ ] Remove dead `ENSURE_SCRIPT` constant
- [ ] **BLOCKING**: Verify `api.on` async support before converting `session_start`
- [ ] Replace `catch (err: any)` with `catch (err: unknown)`
- [ ] Test gateway restart

#### Phase 5: New Scripts (Issues #5, #6)

New Python scripts that make the weekly pipeline self-contained. Independent of each other.

**Issue #5: Create `scripts/x_monitor.py`**

Deterministic Python script replacing the x-monitor agent skill. Reads `data/accounts.json`, fetches tweets via AIsa API, extracts URLs, deduplicates against `data/sources.json`, appends new items.

Key conventions:
- Follow `content_processor.py` pattern (argparse CLI, `PROJECT_ROOT` via `os.environ.get`, `from lib.file_utils import load_json, save_json`)
- Use `from lib.log_utils import StepTimer` for timing
- Build URL `set` for O(1) dedup: `existing_urls = {normalize_url_deep(s['url']) for s in sources}`
- Exit 0 when `AISA_API_KEY` is missing (graceful skip)
- Per-account error handling (log and continue)

Wire into `weekly_pipeline.sh` step 2:
```bash
if [[ -n "${AISA_API_KEY:-}" ]]; then
    $PYTHON "$PROJECT_ROOT/scripts/x_monitor.py" ${DRY_RUN:+--dry-run} 2>&1 && \
        step_ok "x-monitor" || step_fail "x-monitor"
else
    log "  AISA_API_KEY not set — skipping x-monitor"
fi
```

- [ ] Create `scripts/x_monitor.py`
- [ ] Add `AISA_API_KEY` to `.env.example`
- [ ] Wire into `weekly_pipeline.sh` step 2 (with `step_ok`/`step_fail`)
- [ ] Test with `--dry-run`

**Issue #6: Create `scripts/news_digest.py`**

Deterministic Python script replacing the news-digest agent skill. Reads queued articles from `data/news-queue.json`, generates a structured briefing via OpenAI API (`gpt-4o`, temp 0.3, max 4096 tokens, 3-retry backoff), creates an NLM notebook with the briefing.

Key conventions:
- Follow `content_processor.py` pattern (argparse CLI, `PROJECT_ROOT` via `os.environ.get`, `from lib.file_utils import load_json, save_json`)
- Use `from lib.log_utils import StepTimer` for timing
- Filter for `status: "queued"`, require `--min-articles 3` (configurable)
- Use NLM CLI for weekly pipeline (not Playwright). If NLM fails, save briefing to `data/news-digest-YYYY-MM-DD.md` and mark articles `status: "briefed"` for manual retry
- Prompt template: `config/prompts/news-digest-prompt.txt` (themes + synthesis + article index)
- Use `save_json()` for all writes

Wire into `weekly_pipeline.sh` step 6:
```bash
QUEUED_NEWS=$($PYTHON -c "
import sys; sys.path.insert(0, '$PROJECT_ROOT')
from lib.file_utils import load_json
news = load_json('$PROJECT_ROOT/data/news-queue.json')
count = sum(1 for a in news.get('articles', []) if a.get('status') == 'queued')
print(count)
")
if [[ "$QUEUED_NEWS" -ge 3 ]]; then
    $PYTHON "$PROJECT_ROOT/scripts/news_digest.py" ${DRY_RUN:+--dry-run} 2>&1 && \
        step_ok "news-digest" || step_fail "news-digest"
else
    log "  Only $QUEUED_NEWS queued articles (need 3+) — skipping news digest"
fi
```

- [ ] Create `scripts/news_digest.py`
- [ ] Create `config/prompts/news-digest-prompt.txt`
- [ ] Wire into `weekly_pipeline.sh` step 6 (with `step_ok`/`step_fail`)
- [ ] Test with `--dry-run`

#### Phase 6: Documentation (Issue #11)

After all code changes are complete.

**Issue #11: Update TOOLS.md with Playwright path**

Add Playwright as the recommended cron path in `~/.openclaw/workspace/TOOLS.md`. Keep NLM CLI commands for interactive agent use.

Add a note after the Daily Digest Pipeline section:
```markdown
### Playwright Mode (Recommended for Cron)

For automated/cron runs, use Playwright mode which handles auth and audio downloads natively:

```bash
bash /Volumes/ext/projects/openclaw-research/scripts/daily_pipeline.sh --use-playwright
```

This replaces Steps 2-6 (NLM notebook creation, source addition, report/podcast generation, polling, download) with a single Playwright call that drives NLM's web UI directly. Fixes BUG-001 (audio download) and handles auth automatically via Chrome CDP.
```

- [ ] Add Playwright section to `~/.openclaw/workspace/TOOLS.md`

## Acceptance Criteria

### Functional Requirements

- [ ] `scripts/nlm_playwright.py --create "Test" --add-urls <url>` completes without crash (no NameError, no 8-min hang)
- [ ] `scripts/nlm_playwright.py --create "Test" --add-urls <url> --report-prompt <file>` generates a report (after selector verification)
- [ ] `scripts/daily_pipeline.sh --use-playwright` runs end-to-end unattended
- [ ] `scripts/weekly_pipeline.sh` x-monitor step runs when `AISA_API_KEY` is set
- [ ] `scripts/weekly_pipeline.sh` news digest step runs when 3+ articles are queued
- [ ] `openclaw --version` shows >= 2026.2.26
- [ ] No `json.dump(open(...))` patterns in any pipeline script
- [ ] Gateway plugin NLM auth functions use async `execFile` (no 70s event loop blocking)
- [ ] Gateway plugin uses `catch (err: unknown)` (no `any` types)
- [ ] Error arrays in processing-log.json preserve full error messages

### Quality Gates

- [ ] All existing tests pass: `.venv/bin/python3 -m pytest tests/ -v`
- [ ] No new Python lint warnings in changed files
- [ ] Gateway plugin TypeScript compiles without errors
- [ ] `docs/BUGS.md` updated for BUG-002 fix and any new bugs found
- [ ] Session log created at `docs/logs/2026-03-05-reliability-sweep.md`

### SpecFlow-Discovered Issues (Additional Fixes)

The SpecFlow analysis identified several additional gaps not in the original brainstorm. These are incorporated into the relevant phases above or noted as standalone fixes:

**SF-1: `download_audio()` route handler not cleaned up on exception** (Phase 2)

At `scripts/nlm_playwright.py:976`, `page.route("**/*", intercept_media)` is set but `page.unroute("**/*")` only runs in the happy path (line 988) and one fallback (line 1013). Wrap in `try/finally`:

```python
try:
    page.route("**/*", intercept_media)
    # ... download logic ...
finally:
    try:
        page.unroute("**/*")
    except Exception:
        pass  # Page may already be closed on error path
```

> **Review insight (Performance oracle):** After `page.unroute()`, in-flight intercepted requests may still be pending. The `unroute` removes future matching but does not cancel already-intercepted requests. The inner try/except handles the case where the page is already closed (common in error paths). If the route leaks, the `intercept_media` handler will corrupt all subsequent page interactions in the session.

- [ ] Wrap `download_audio()` route handler in `try/finally` with inner try for page-closed case

**SF-2 + SF-3: Weekly auth rotation + poll timeout fix** (Phase 3)

Two coupled issues: (1) Step 5 inline Python creates multiple notebooks in a loop without re-checking auth between iterations — Google rotates cookies every ~10 min. (2) Step 7 delivery poll timeout is 600s (10 min) but podcast generation takes 10-45 min per notebook.

Fix both together:

```python
# SF-2: At start of each group iteration in Step 5:
subprocess.run([nlm, 'login', '--check'], capture_output=True, timeout=10)

# SF-3: In Step 7 delivery poll:
max_wait = 3600  # 1 hour (podcasts take 10-45 min, not 3 hours)

# SF-3: Auth retry limit:
if auth_failures <= 5:  # 1 hour / 10 min rotation = 6 expected rotations
```

- [ ] Add per-group auth re-check in weekly Step 5
- [ ] Change weekly delivery `max_wait` from 600 to 3600
- [ ] Increase weekly delivery auth retry limit from 2 to 5

**SF-4: `poll_studio()` does not detect auth expiry during long polls** (Phase 2)

The Playwright `poll_studio()` (lines 725-789) does not check for Google login page redirects. If auth expires during the 3-hour max poll, the page silently redirects and the function returns incorrect results. Add URL check inside the poll loop:

```python
# Inside poll loop, before checking artifact status:
from urllib.parse import urlparse

parsed = urlparse(page.url)
if parsed.hostname and parsed.hostname.endswith("accounts.google.com"):
    log("  Auth expired during polling — need re-login")
    record.errors.append("poll_studio: auth expired")
    return False
```

> **Review insight (Python reviewer):** Use `urlparse` for proper URL matching instead of `"accounts.google" in page.url` substring check. The substring check would match hypothetical URLs containing "accounts.google" in other contexts. `urlparse` is cheap and correct.

- [ ] Add auth expiry detection in `poll_studio()` (check `page.url` via `urlparse` for login redirect)

**SF-5: Daily pipeline Step 10 hardcodes `has_podcast: True, has_report: True`** (Phase 3)

At `daily_pipeline.sh:591-592`, these are always `True` regardless of actual download success. If podcast generation failed, `fetch_and_deliver.sh` will try to download a nonexistent podcast.

Make conditional on actual file existence:

```python
'has_podcast': os.path.exists(f'{daily_dir}/{date}-podcast-raw.mp4'),
'has_report': os.path.exists(f'{daily_dir}/{date}-report.md'),
```

- [ ] Make `has_podcast`/`has_report` conditional on file existence in daily Step 10

**SF-6: `generate_report()` failures should be non-fatal** (Phase 2)

Currently, if `generate_report()` raises an exception in `run_workflow()`, it does not crash the entire run (it's inside a try/except at line 1232). But the polling step at line 1201 uses `if args.report_prompt or args.podcast_format:` — after Issue #13 fix this becomes `if args.report_prompt:`. If report generation fails, `poll_studio()` may still poll for a report that was never created. Ensure the poll condition tracks actual generation success:

```python
# Use a single should_poll flag instead of *_started booleans (scales to more artifact types)
should_poll = False
if args.report_prompt:
    log("Generating report...")
    should_poll |= generate_report(page, report_prompt, record)
    time.sleep(2)
if args.podcast_format:
    log("Generating podcast...")
    should_poll |= generate_podcast(page, args.podcast_format, args.podcast_length, focus_text, record)
    time.sleep(2)

# Only poll if something was actually started
if should_poll:
    poll_studio(page, ...)
```

> **Review insight (Python reviewer):** Use a single `should_poll` flag with `|=` instead of separate `report_started`/`podcast_started` booleans. This pattern scales cleanly to additional artifact types without a growing list of `*_started` variables.

- [ ] Track generation success with `should_poll` flag and only poll if something was started


## Dependencies & Prerequisites

| Dependency | Blocks | Status |
|------------|--------|--------|
| Chrome running with CDP (via `nlm login`) | Issues #1, #2, #4 testing | Available when user is at desktop |
| Manual NLM UI inspection for Studio selectors | Issue #1 implementation | BLOCKING — no reference exists |
| Verify OpenClaw `api.on` supports async handlers | Issue #10 Phase 2 (`session_start` conversion) | BLOCKING — check docs or test |
| `AISA_API_KEY` in environment | Issue #5 | User must provide |
| `OPENAI_API_KEY` in environment | Issue #6 | Already configured |
| OpenClaw npm update | Issue #3 | Internet access required |

## Risk Analysis & Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| NLM Studio selectors unknown | Issue #1 can't be implemented | Verify selectors manually first; if NLM changed Studio UI, defer Issue #1 and keep report generation opt-in (Issue #13 fix already prevents crash) |
| OpenClaw update introduces breaking changes | Gateway may not start | Pin to specific version if needed; test gateway restart before and after |
| OpenClaw `api.on` does not await async handlers | `session_start` hook races with cron job — auth incomplete when pipeline starts | **Phase the migration**: convert `gateway_start`+auth functions first (Phase 1). Only convert `session_start` after confirming async support (Phase 2). If not supported, keep `session_start` synchronous. |
| Gateway async migration breaks hooks | Pre-cron auth may not complete | Keep `session_start` hook synchronous as fallback; only convert non-critical hooks to async |
| AIsa API rate limits during x-monitor | Tweet fetching may fail | Add `time.sleep(2)` between accounts; retry with backoff; respect `usage.credits_remaining` in responses |
| Playwright route handler leak on exception | Corrupts all subsequent page interactions in session | Wrap in `try/finally` with inner try for page-closed case (SF-1) |
| Auth expiry during 60-min weekly poll | False "complete" signals, failed artifact downloads | Auth expiry detection via `urlparse` in `poll_studio()` (SF-4); increase auth retry limit from 2 to 5 |

## Files Changed

| File | Action | Issues |
|------|--------|--------|
| `scripts/nlm_playwright.py` | Edit | #1, #2, #4, #12, #13 |
| `scripts/weekly_pipeline.sh` | Edit | #5, #6, #7, #8 |
| `scripts/daily_pipeline.sh` | Edit | #4, #7, #8 |
| `scripts/x_monitor.py` | Create | #5 |
| `scripts/news_digest.py` | Create | #6 |
| `config/prompts/news-digest-prompt.txt` | Create | #6 |
| `.env.example` | Edit | #5 |
| `~/.openclaw/extensions/gateway-health/index.ts` | Edit | #9, #10 |
| `~/.openclaw/workspace/TOOLS.md` | Edit | #11 |
| `docs/BUGS.md` | Edit | #3 |
| `docs/guide/nlm-playwright-selectors.md` | Edit | #1 |
| `docs/logs/2026-03-05-reliability-sweep.md` | Create | — |
| `CLAUDE.md` | Edit | Pipeline Scripts table update |

## Sources & References

### Origin

- **Brainstorm document:** [docs/brainstorms/2026-03-05-repo-reliability-sweep-brainstorm.md](docs/brainstorms/2026-03-05-repo-reliability-sweep-brainstorm.md) — Key decisions carried forward: Playwright becomes default, auth via CDP, report generation opt-in, x-monitor + news digest as deterministic scripts, update OpenClaw immediately.

### Internal References

- Playwright function patterns: `scripts/nlm_playwright.py:639-722` (`generate_podcast()`)
- Atomic JSON write: `lib/file_utils.py:25-40` (`save_json()`)
- Twitter client API: `skills/aisa-twitter-api/scripts/twitter_client.py`
- NLM selector reference: `docs/guide/nlm-playwright-selectors.md`
- Gateway plugin: `~/.openclaw/extensions/gateway-health/index.ts`
- Operations log patterns: `data/nlm-operations-log.json`
- News queue format: `data/news-queue.json`
- Accounts format: `data/accounts.json`
- Session log: `docs/logs/2026-03-03-playwright-nlm-automation.md`
