---
status: complete
priority: p2
issue_id: "003"
tags: [code-review, quality, pattern-consistency]
dependencies: []
---

# Missing `record` parameter and wrong error pattern in new Playwright functions

## Problem Statement

The plan's Issue #1 proposes implementing `_open_studio_panel()` and `_fill_custom_prompt()` but the code stubs break two established patterns in `nlm_playwright.py`: (1) missing the `record` parameter used by all other functions for operations logging, and (2) using `raise RuntimeError` instead of `return False` for error handling.

## Findings

- Every existing function in `nlm_playwright.py` follows the signature pattern: `func(page, ..., record, timeout=N)`
- Example: `generate_podcast(page, format, length, focus_text, record, timeout=120)` at line 639
- Plan's Issue #1 code stubs omit `record` parameter entirely
- Plan shows `raise RuntimeError(...)` in `_open_studio_panel` but `generate_podcast()` at line 713 uses `return False` pattern
- `record` dict is used to track operation status in `data/nlm-operations-log.json`
- Without `record`, these functions can't log their status to the operations log

## Proposed Solutions

### Option 1: Fix signatures in plan before implementing

**Approach:** Update Issue #1 code stubs to include `record` parameter and use `return False` pattern.

**Pros:**
- Prevents pattern drift at implementation time
- Consistent with existing codebase

**Cons:**
- Minor plan update needed

**Effort:** Small (30 minutes)

**Risk:** Low

## Recommended Action

**To be filled during triage.**

## Technical Details

**Affected files:**
- `scripts/nlm_playwright.py:587-636` — `generate_report()` and its helper calls
- `scripts/nlm_playwright.py:639-722` — `generate_podcast()` reference pattern
- Plan Issue #1 code stubs

## Acceptance Criteria

- [ ] `_open_studio_panel(page, record, timeout=30)` signature matches pattern
- [ ] `_fill_custom_prompt(page, prompt_text, record, timeout=30)` signature matches pattern
- [ ] Error handling uses `return False` not `raise RuntimeError`
- [ ] Operations log records studio panel and custom prompt status

## Work Log

### 2026-03-05 - Code Review Discovery

**By:** Claude Code (`/ce:review`)

**Actions:**
- Pattern recognition specialist identified signature mismatch (Finding 1)
- Pattern recognition specialist identified error handling inconsistency (Finding 2)
- Cross-referenced with `generate_podcast()` as reference implementation

## Resources

- **Review target:** `docs/plans/2026-03-05-fix-repo-wide-reliability-sweep-plan.md`
- **Reference pattern:** `scripts/nlm_playwright.py:639` (`generate_podcast`)
