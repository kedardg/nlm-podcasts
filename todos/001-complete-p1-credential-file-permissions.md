---
status: complete
priority: p1
issue_id: "001"
tags: [code-review, security]
dependencies: []
---

# Credential files written without restrictive permissions

## Problem Statement

Multiple scripts write sensitive credential files (cookies, auth tokens, google-credentials.json) using `Path.write_text()` or `open()` which inherit the default umask — typically `0644` (world-readable). This exposes Google session cookies and credentials to any local user or process.

## Findings

- `scripts/nlm_rotate_cookies.py:67` — `COOKIES_FILE.write_text(json.dumps(cookies, indent=2))` writes cookies world-readable
- `config/google-credentials.json` — plan calls for storing plaintext password without specifying permissions
- `~/.notebooklm-chrome/` browser profile directory — no permission enforcement on session data
- The plan's Issue #7 addresses unsafe JSON writes for atomicity but does NOT address file permissions
- `lib/file_utils.py::save_json()` uses atomic writes but also doesn't enforce permissions

**Security agents flagged this as CRITICAL (OWASP A01 Broken Access Control).**

## Proposed Solutions

### Option 1: Extend Issue #7 to include permission enforcement

**Approach:** Add `os.chmod(path, 0o600)` after every sensitive file write. Update `save_json()` to accept an optional `mode` parameter.

**Pros:**
- Minimal code change
- Fits naturally into existing Issue #7 scope

**Cons:**
- Easy to forget on new files
- `os.chmod` after write has a tiny race window

**Effort:** Small (1-2 hours)

**Risk:** Low

---

### Option 2: Create `save_sensitive_file()` utility in `lib/file_utils.py`

**Approach:** New function that opens with `os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)` to set permissions atomically at creation time. Use for all credential/cookie writes.

**Pros:**
- No race window — permissions set at creation
- Single function to audit
- Extends existing `save_json()` pattern

**Cons:**
- Slightly more code
- Need to find and update all call sites

**Effort:** Small (2-3 hours)

**Risk:** Low

## Recommended Action

**To be filled during triage.**

## Technical Details

**Affected files:**
- `scripts/nlm_rotate_cookies.py:67` — cookie write
- `lib/file_utils.py` — `save_json()` utility
- Plan Issue #7 scope — currently only covers atomicity, not permissions
- `config/google-credentials.json` — new file in plan

**Related components:**
- `nlm_ensure_auth.sh` — reads cookies
- `nlm_playwright.py` — reads browser profile
- Gateway health plugin — reads auth state

## Acceptance Criteria

- [ ] All cookie/credential files written with mode `0600`
- [ ] `google-credentials.json` created with `0600` permissions
- [ ] Browser profile directory has `0700` permissions
- [ ] `save_json()` supports optional `mode` parameter for sensitive files
- [ ] No credential file is world-readable after any pipeline run

## Work Log

### 2026-03-05 - Code Review Discovery

**By:** Claude Code (`/ce:review`)

**Actions:**
- Security sentinel flagged as CRITICAL-02 + MEDIUM-05
- Cross-referenced with plan Issue #7 (atomic writes) — permissions not addressed
- Verified `Path.write_text()` uses default umask in Python stdlib

## Resources

- **Review target:** `docs/plans/2026-03-05-fix-repo-wide-reliability-sweep-plan.md`
- **OWASP reference:** A01:2021 Broken Access Control
