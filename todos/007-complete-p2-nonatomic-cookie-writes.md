---
status: complete
priority: p2
issue_id: "007"
tags: [code-review, security, quality]
dependencies: ["001"]
---

# Non-atomic cookie writes in nlm_rotate_cookies.py

## Problem Statement

`scripts/nlm_rotate_cookies.py` uses `Path.write_text()` for cookie writes, which is neither atomic nor permission-safe. A crash mid-write corrupts the cookie file, breaking all NLM auth until manual intervention. The plan's Issue #7 addresses unsafe JSON writes in `weekly_pipeline.sh` but doesn't cover `nlm_rotate_cookies.py`.

## Findings

- `scripts/nlm_rotate_cookies.py:67` — `COOKIES_FILE.write_text(json.dumps(cookies, indent=2))`
- `Path.write_text()` truncates then writes — crash mid-write = empty/partial file
- Cookie rotation happens every ~10 minutes — frequent writes increase corruption risk
- `lib/file_utils.py::save_json()` already provides atomic writes (tmp+fsync+rename) — just needs to be used
- Plan Issue #7 only covers `weekly_pipeline.sh` lines 278 and 453

## Proposed Solutions

### Option 1: Extend Issue #7 scope to include nlm_rotate_cookies.py

**Approach:** Replace `COOKIES_FILE.write_text(json.dumps(...))` with `save_json(COOKIES_FILE, cookies)` from `lib/file_utils.py`.

**Pros:**
- One-line fix using existing utility
- Consistent with other JSON writes
- Atomic + permission-safe (with todo #001)

**Cons:**
- Need to add `from lib.file_utils import save_json` import

**Effort:** Small (30 minutes)

**Risk:** Low

## Recommended Action

**To be filled during triage.**

## Technical Details

**Affected files:**
- `scripts/nlm_rotate_cookies.py:67` — cookie write
- `lib/file_utils.py` — `save_json()` utility

## Acceptance Criteria

- [ ] Cookie writes use `save_json()` for atomicity
- [ ] Cookie file is not corrupted by mid-write crashes
- [ ] Plan Issue #7 scope expanded to include this file

## Work Log

### 2026-03-05 - Code Review Discovery

**By:** Claude Code (`/ce:review`)

**Actions:**
- Security sentinel flagged as HIGH-03
- Cross-referenced with plan Issue #7 — scope too narrow
- Verified `save_json()` already handles atomic writes

## Resources

- **Review target:** `docs/plans/2026-03-05-fix-repo-wide-reliability-sweep-plan.md`
