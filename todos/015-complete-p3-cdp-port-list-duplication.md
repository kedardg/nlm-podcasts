---
status: complete
priority: p3
issue_id: "015"
tags: [code-review, architecture]
dependencies: []
---

# CDP port list defined in 3 places — should have single source of truth

## Problem Statement

The CDP port range (9222-9224 or 9222-9231) is defined independently in `nlm_playwright.py`, `nlm_ensure_auth.sh`, and potentially the gateway plugin. Changes to one won't propagate to others.

## Findings

- Architecture strategist identified three independent CDP port definitions
- `nlm_playwright.py` — `_probe_cdp()` checks ports 9222-9224
- `nlm_ensure_auth.sh` — CDP fallback checks port range
- Gateway plugin — may reference CDP ports for auth hooks
- Port range inconsistency between files (9222-9224 vs 9222-9231)

## Proposed Solutions

### Option 1: Define ports in config/env.sh, read from all scripts

**Approach:** Add `CDP_PORTS="9222 9223 9224"` to `config/env.sh`. Source from shell scripts, parse from Python via dotenv or subprocess.

**Effort:** Small (1-2 hours)

**Risk:** Low

## Recommended Action

**To be filled during triage.**

## Technical Details

**Affected files:**
- `scripts/nlm_playwright.py` — `_probe_cdp()`
- `scripts/nlm_ensure_auth.sh` — CDP fallback
- `config/env.sh` — central config

## Acceptance Criteria

- [ ] CDP ports defined in single location
- [ ] All scripts read from the same source

## Work Log

### 2026-03-05 - Code Review Discovery

**By:** Claude Code (`/ce:review`)

**Actions:**
- Architecture strategist identified 3-place duplication

## Resources

- **Review target:** `docs/plans/2026-03-05-fix-repo-wide-reliability-sweep-plan.md`
