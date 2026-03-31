# Brainstorm: Repo-Wide Reliability Sweep

**Date**: 2026-03-05
**Status**: Refined
**Scope**: 16 issues found across the OpenClaw Research Automation system

---

## What We're Building

A comprehensive reliability fix across the entire pipeline — from critical NameErrors that crash Playwright, to unsafe JSON writes that can corrupt state, to gateway plugin improvements. The goal: both daily and weekly pipelines should run unattended without silent failures.

---

## Issue Inventory

### Tier 1: Critical (Pipeline-Breaking)

**Issue #1: Missing `_open_studio_panel()` and `_fill_custom_prompt()` functions**
- `scripts/nlm_playwright.py:592` calls `_open_studio_panel(page)` — never defined
- `scripts/nlm_playwright.py:614` calls `_fill_custom_prompt(page, prompt_text)` — never defined
- Confirmed crash in operations log: `"errors": ["generate_report: name '_open_studio_panel' is not defined"]`
- **Impact**: Playwright report generation always crashes. Since `generate_report()` runs whenever `--create` is passed (line 1187: `if args.report_prompt or args.create`), every notebook creation crashes.
- **Warning**: No existing NLM automation repo (israelbls, upamune, DataNath) automates report generation. The selector reference doc explicitly lists it as a "Missing Operation." Fresh UI inspection required.
- **Fix approach**: Implement both functions. The Studio panel is likely a sidebar/tab in NLM's notebook view. Requires manual browser inspection to discover selectors. Build with fallback chains like `generate_podcast()` already does.
- **Design question**: Should report generation be opt-in (only with `--report-prompt`) or default? Currently it's unconditionally triggered by `--create`.

**Issue #2: `add_url_source` selector mismatch**
- Operations log shows: "add source button not found"
- The `page.get_by_role("button", name="Add source")` selector may not match if the page hasn't fully loaded
- **Fix approach**: Add explicit wait for NLM's main UI (e.g., wait for notebook title or source list to render). Add fallback selectors.

**Issue #12 (NEW): `time.sleep(500)` bug — 8-minute hang**
- `scripts/nlm_playwright.py:368`: `time.sleep(500)` sleeps for **500 seconds** (8+ minutes)
- Should be `time.sleep(0.5)` (500 milliseconds)
- Inside `create_notebook()` after closing the initial source dialog
- **Impact**: Every notebook creation hangs for 8 minutes before proceeding. Single-character fix.

### Tier 2: High (Blocking Features)

**Issue #3: BUG-002 — OpenClaw 400 "reasoning" error**
- Upstream fix exists in OpenClaw 2026.2.26 (commit `46bf210e`)
- Just needs: `npm install -g openclaw@latest`
- **Fix approach**: Run the update, verify version, restart gateway. One command.

**Issue #4: Playwright auth bootstrapping**
- 4/7 runs fail with `login_failed`
- Root cause: Persistent browser profile needs manual headed login (display access required)
- macOS keychain inaccessible from SSH/cron — Chrome can't decrypt stored cookies
- Cookie injection from NLM CLI doesn't work (Google validates browser fingerprint)
- **Fix approach**:
  - Primary: Launch Chrome with `nlm login` (handles keychain), connect Playwright via `--cdp-url`
  - Integrate `nlm_ensure_auth.sh` into the Playwright script to auto-start Chrome with CDP
  - Fallback: Persistent profile for environments where Chrome can be launched headed

### Tier 3: Medium (Degraded Functionality)

**Issue #5: X-monitor skipped in weekly script**
- `weekly_pipeline.sh` step 2 always logs "x-monitor requires OpenClaw agent session (skipping)"
- **Fix approach**: Create `scripts/x_monitor.py` using `TwitterClient` from `skills/aisa-twitter-api/scripts/twitter_client.py`. Read `data/accounts.json`, fetch tweets, extract URLs, dedup with `normalize_url_deep()`, append to `sources.json`.
- **Prerequisite**: `AISA_API_KEY` must be added to `.env.example` and `config/env.sh`.

**Issue #6: News digest skipped in weekly script**
- Step 6 always skipped: "News digest requires agent session for briefing note creation"
- **Fix approach**: Create `scripts/news_digest.py` using OpenAI API directly (`openai` package already installed, `OPENAI_API_KEY` in `.env`). Reads `news-queue.json`, generates briefing note via GPT-4o, creates NLM notebook.
- **Spec needed**: Model choice, max_tokens budget, prompt template.

**Issue #7: Unsafe `json.dump(open(...))` in weekly pipeline**
- Lines 278 and 453 of `weekly_pipeline.sh` use non-atomic `json.dump(data, open(path, 'w'))`
- Process crash mid-write = corrupted `notebooks.json`
- **Fix approach**: Replace with `from lib.file_utils import save_json; save_json(path, data)` — atomic write utility already exists and is tested.

### Tier 4: Low (Code Quality)

**Issue #8: `${ERRORS[*]}` bash-to-JSON conversion**
- `weekly_pipeline.sh:496`: `python3 -c "import json; print(json.dumps('${ERRORS[*]}'.split()))"`
- If error messages contain spaces, `split()` breaks them into multiple items
- Scope: confirmed in weekly pipeline only (daily pipeline uses a different error format)
- **Fix approach**: Use heredoc or proper array construction in the inline Python.

**Issue #9: Hardcoded PROJECT_ROOT in gateway plugin + SKILL.md files**
- Gateway plugin: `~/.openclaw/extensions/gateway-health/index.ts:4` hardcodes `/Volumes/ext/projects/openclaw-research`
- All 9 SKILL.md files also hardcode `source /Volumes/ext/projects/openclaw-research/config/env.sh`
- **Fix approach**: Gateway plugin: read from `process.env.PROJECT_ROOT` with current path as fallback. SKILL.md files: lower priority since they're LLM-read templates and `config/env.sh` auto-detects anyway.

**Issue #10: Synchronous `execFileSync` in gateway plugin**
- `ensureNlmAuth()` blocks event loop up to 70s (10s check + 30s rotate + 30s CDP)
- `getGatewayRssMb()` also blocks up to 5s on each 10-min health probe
- `execFile` (async) is already imported but never used
- **Fix approach**: Switch to async `execFile` with promises. Note: `session_start` hook needs to await auth completion before allowing cron job to proceed.
- Dead code: `ENSURE_SCRIPT` (line 8) is defined but never used — clean up.

**Issue #11: TOOLS.md doesn't mention Playwright path**
- TOOLS.md Step 6 references `nlm_download_audio.py` but not the Playwright alternative
- **Fix approach**: Add Playwright as the recommended cron path, keep NLM CLI for manual agent runs.

**Issue #13 (NEW): `generate_report()` unconditionally triggered**
- Line 1187: `if args.report_prompt or args.create` — report generation runs whenever `--create` is used
- Combined with Issue #1 (missing function), every `--create` invocation crashes
- **Fix approach**: Make report generation opt-in (only when `--report-prompt` is explicitly provided). The `or args.create` should be removed.

---

## Dependency Graph

```
Issue #3 (update OpenClaw)  ← do FIRST, one command
    |
Issue #12 (sleep 500 fix)   ← do SECOND, one-line fix
    |
Issue #4 (auth bootstrap)   ← unblocks testing
    |
Issue #1 (studio panel)     ← needs auth working + manual UI research
Issue #2 (add source)       ← needs auth working to test
Issue #13 (report opt-in)   ← part of Issue #1 fix
    |
Issue #7 (unsafe JSON)      ← independent, safe at any time
Issue #8 (ERRORS array)     ← independent
Issue #9 (hardcoded paths)  ← independent
Issue #10 (async gateway)   ← independent but test carefully
Issue #11 (TOOLS.md)        ← after Playwright is working
    |
Issue #5 (x_monitor.py)     ← independent, new script
Issue #6 (news_digest.py)   ← independent, new script
```

---

## Key Decisions

1. **Playwright becomes default**: `daily_pipeline.sh` uses Playwright by default (no flag needed). Old NLM CLI path available via `--use-nlm-cli`. This bypasses BUG-001 entirely.

2. **Playwright auth via CDP**: Use `nlm login`-managed Chrome via CDP as the primary auth path. Integrate `nlm_ensure_auth.sh` to auto-start Chrome with CDP before Playwright runs. Persistent profile as fallback.

3. **Missing Playwright functions**: Implement `_open_studio_panel()` and `_fill_custom_prompt()` with fresh UI research. No reference implementations exist — requires manual browser inspection.

4. **Report generation opt-in**: Remove `or args.create` from the report trigger condition. Reports only generated when `--report-prompt` is explicitly passed.

5. **X-monitor + news digest**: Create deterministic Python scripts with direct API calls. Weekly pipeline becomes fully self-contained. Prerequisites: `AISA_API_KEY` in environment for x_monitor.

6. **Update OpenClaw**: Run `npm install -g openclaw@latest` to fix BUG-002 immediately. Do this first.

7. **Unsafe JSON writes**: Replace `json.dump(open(...))` with `save_json()` from `lib/file_utils.py`.

8. **Gateway plugin**: Make PROJECT_ROOT configurable, switch `execFileSync` to async `execFile`, remove dead `ENSURE_SCRIPT` variable.

9. **TOOLS.md**: Add Playwright as the recommended cron path after it's working.

---

## Resolved Questions

1. **`--use-playwright` becomes the default** for daily_pipeline.sh once bugs are fixed. NLM CLI path remains as `--use-nlm-cli` fallback.

2. **X-monitor and news digest**: Add deterministic Python scripts with direct API calls. Weekly pipeline fully self-contained.

3. **Update OpenClaw now** — `npm install -g openclaw@latest` to fix BUG-002.

4. **Report generation**: Opt-in only (requires `--report-prompt`), not unconditionally triggered by `--create`.

## Open Questions

1. **What selectors does the NLM Studio report panel use?** No reference implementations exist. Requires manual browser inspection before implementing Issue #1. This is a blocking dependency.

2. **Should `AISA_API_KEY` be added to the project's standard environment?** Needed for `x_monitor.py`. Currently not in `.env.example` or `config/env.sh`.

3. **What model and token budget for `news_digest.py`?** Direct OpenAI API call needs: model (gpt-4o?), max_tokens (2000?), and a prompt template for news summarization.
