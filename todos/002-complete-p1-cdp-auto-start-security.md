---
status: complete
priority: p1
issue_id: "002"
tags: [code-review, security]
dependencies: []
---

# CDP auto-start expands unauthenticated attack surface

## Problem Statement

The plan's Issue #4 proposes auto-launching Chrome with `--remote-debugging-port` via `ensure_chrome_cdp()`. CDP ports expose **unauthenticated full browser control** — any local process can read cookies, navigate pages, execute JavaScript, and exfiltrate credentials. Auto-starting this by default expands the attack window from "user explicitly launches Chrome" to "every pipeline run."

Additionally, `_probe_cdp()` trusts any HTTP 200 response without validating the response body is actually a Chrome DevTools endpoint.

## Findings

- CDP ports (9222-9224) have **no authentication** — connecting gives full browser control
- Plan proposes `ensure_chrome_cdp()` to auto-start Chrome with CDP — runs on every `--create` invocation
- `_probe_cdp()` in `nlm_playwright.py` only checks `response.status_code == 200` — doesn't verify Chrome's `/json/version` response format
- Any local process (malware, compromised dependency, other user) can connect to the CDP port
- This is flagged by security sentinel as HIGH-01 + MEDIUM-01

## Proposed Solutions

### Option 1: Keep CDP opt-in only (Recommended)

**Approach:** Remove `ensure_chrome_cdp()` auto-start from the default flow. Require explicit `--cdp-url` flag. Document that users should manually start Chrome with CDP when needed.

**Pros:**
- No expanded attack surface
- User consciously opts in
- Simpler code (remove auto-start logic)

**Cons:**
- Less convenient for automated pipelines
- Requires user to start Chrome separately

**Effort:** Small (1 hour)

**Risk:** Low

---

### Option 2: Keep auto-start but bind to localhost + validate response

**Approach:** Ensure Chrome is launched with `--remote-debugging-address=127.0.0.1` (not `0.0.0.0`). Add response body validation in `_probe_cdp()` to check for `"Browser"` and `"webSocketDebuggerUrl"` keys.

**Pros:**
- Limits access to local machine
- Validates actual Chrome endpoint
- Preserves automation convenience

**Cons:**
- Still exposes to any local process
- More code to maintain
- False sense of security if machine is shared

**Effort:** Small (2 hours)

**Risk:** Medium — localhost binding helps but doesn't fully mitigate

## Recommended Action

**To be filled during triage.**

## Technical Details

**Affected files:**
- `scripts/nlm_playwright.py` — `_probe_cdp()`, `ensure_chrome_cdp()` functions
- Plan Issue #4 — auth bootstrapping approach
- `scripts/nlm_ensure_auth.sh` — CDP fallback layer

## Acceptance Criteria

- [ ] CDP is not auto-started unless user explicitly opts in
- [ ] `_probe_cdp()` validates response body structure, not just status code
- [ ] Chrome launched with `--remote-debugging-address=127.0.0.1`
- [ ] Documentation warns about CDP security implications

## Work Log

### 2026-03-05 - Code Review Discovery

**By:** Claude Code (`/ce:review`)

**Actions:**
- Security sentinel flagged CDP auto-start as HIGH-01
- Verified `_probe_cdp()` only checks HTTP status, not response content
- Architecture strategist confirmed auto-start scope expansion concern

## Resources

- **Review target:** `docs/plans/2026-03-05-fix-repo-wide-reliability-sweep-plan.md`
- **Chrome DevTools Protocol docs:** https://chromedevtools.github.io/devtools-protocol/
