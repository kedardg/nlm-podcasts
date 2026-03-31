# 2026-03-03: Playwright NLM Automation (JOB 1)

## Summary

Replaced the `nlm` CLI with Playwright browser automation for NotebookLM workflows. The NLM CLI has persistent reliability issues: cookie rotation every 10 min, audio download broken (BUG-001 TooManyRedirects via httpx), 180-min poll timeouts, opaque errors. Playwright drives NLM's web UI directly — keeping NLM's grounding quality while fixing all reliability bugs. JOB 2 (Podcastfy as local fallback) is documented for future implementation.

## Problem

Three reliability issues with `nlm` CLI made automated pipeline runs fragile:

1. **BUG-001: Audio download always fails** — `httpx.TooManyRedirects` because httpx strips cookies on cross-domain redirects through Google's CDN chain (`lh3.googleusercontent.com` -> `lh3.google.com` -> `googlevideo.com`). Required Chrome CDP workaround (`scripts/nlm_download_audio.py`).

2. **Cookie rotation** — Google rotates `__Secure-1PSIDTS` every ~10 min. Auth can expire mid-pipeline during 15-min artifact polling. Required 4-layer auth recovery (`nlm_ensure_auth.sh`).

3. **Opaque errors** — NLM CLI wraps all errors in generic `ServiceError` messages, making debugging difficult.

## Solution

### `scripts/nlm_playwright.py` — NLM browser automation

Full NLM workflow via Playwright with persistent browser profile:
- **Login**: Two-phase auth — manual headed login once (`--login`), headless for all subsequent runs. Google blocks automated login in headless Chromium ("This browser or app may not be secure").
- **Create notebook**: `page.get_by_role("button", name="Create new notebook")` (STABLE selector)
- **Add sources**: URL and text addition via Angular form controls with robust completion detection (DataNath's container attach -> spinner detach -> checkbox label change)
- **Generate report/podcast**: Studio panel with config dialog selectors
- **Poll studio**: Text-based status detection ("sync"/"generating" = in progress, "play_arrow" = done)
- **Download audio**: Dual method — More menu -> Download (primary), play interception (fallback). Both handle the redirect chain natively via browser cookies.
- **Share link**: Public link toggle

### Operations logging (`data/nlm-operations-log.json`)

Append-only provenance log tracking every URL, text source, and artifact per run with status, size, and timing. Enables auditing what NLM received on any given day.

### Pipeline integration

`scripts/daily_pipeline.sh --use-playwright` replaces NLM CLI steps with single `nlm_playwright.py` call. All other steps unchanged.

## Key Technical Decisions

### Selectors: ARIA roles over CSS classes

NLM's CSS classes are minified and change frequently. Prioritized `aria-label`, `role="button"`, and text content (STABLE). Angular `formcontrolname` attributes as secondary selectors (MODERATE). Documented stability ratings in `docs/guide/nlm-playwright-selectors.md`.

### Auth: Persistent browser profile, not stored credentials

Google blocks programmatic login in automated browsers. Instead of fighting it:
1. User runs `--login` once in headed mode, signs in manually
2. Session persists in `~/.notebooklm-chrome/` browser profile
3. All subsequent runs reuse the profile in headless mode
4. If session expires, script detects it and asks user to re-login

### Audio download: Browser-native vs CDP hack

The old `nlm_download_audio.py` used Chrome CDP cookies + manual redirect following with `requests`. Playwright's `page.expect_download()` handles the full redirect chain natively — the browser manages cookies across all domains. This is the core BUG-001 fix.

## Research

### Existing NLM automation repos studied

| Repo | Auth Approach | Key Innovation |
|------|---------------|----------------|
| israelbls/notebooklm-podcast-automator | CDP manual launch | Text-based studio status detection |
| upamune/notebooklm-podcast-automator | CDP headless | CLI + Streamlit UI |
| DataNath/notebooklm_source_automation | `storage_state` JSON | Robust source completion detection via container/spinner/checkbox |

### JOB 2: Podcastfy evaluation

Documented in `docs/guide/podcastfy-evaluation.md`. Key finding: `github_copilot/gpt-4.1` via LiteLLM enables $0 cost podcast generation. Viable on Raspberry Pi 4B 16GB with cloud LLM + Edge TTS (~5-10 min for a 20-min podcast). Implementation deferred until JOB 1 is tested.

## Files Changed

| File | Action |
|------|--------|
| `scripts/nlm_playwright.py` | **Created** — NLM browser automation (~1200 lines) |
| `docs/guide/nlm-playwright-selectors.md` | **Created** — Comprehensive NLM UI selector reference (586 lines) |
| `docs/guide/podcastfy-evaluation.md` | **Created** — JOB 2 research doc |
| `scripts/daily_pipeline.sh` | **Edited** — Added `--use-playwright` flag |
| `.gitignore` | **Edited** — Added `config/nlm-playwright-state.json` |
| `CLAUDE.md` | **Edited** — Documented Playwright approach across multiple sections |
| `docs/BUGS.md` | **Edited** — Added Playwright workaround to BUG-001 |

## Auth Testing Results (2026-03-05)

Attempted multiple approaches to authenticate Playwright without manual headed login:

| Approach | Result | Reason |
|----------|--------|--------|
| Cookie injection (Playwright API) | FAIL | Google validates session cookies against browser fingerprint |
| Cookie injection (SQLite DB) | FAIL | Chromium ignores unencrypted values in cookie DB |
| Storage state with system Chrome binary | FAIL | Same fingerprint mismatch — cookies created by httpx rejected by browser |
| System Chrome profile (persistent context) | FAIL | Profile too heavy (extensions, state), hangs on headless launch |
| CDP connection | BLOCKED | Chrome crashes without display in this terminal context |

**Conclusion**: Cookie injection from NLM CLI to Playwright does NOT work. Google ties session cookies to the client identity. The only viable auth methods are:

1. **Manual headed login once (`--login`)** — user runs from desktop terminal, session persists
2. **CDP connection (`--cdp-url`)** — connect to running Chrome with `--remote-debugging-port`

Added `--cdp-url` option and headless fallback for headed mode CVDisplayLink crashes on macOS.

## Open Items

- End-to-end test requires `--login` from a GUI terminal (one-time) or `--cdp-url` to running Chrome
- Google may change NLM UI selectors — MODERATE-stability selectors may need updates
- JOB 2 (Podcastfy fallback) implementation deferred
