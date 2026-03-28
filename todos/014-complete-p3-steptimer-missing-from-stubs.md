---
status: complete
priority: p3
issue_id: "014"
tags: [code-review, quality, pattern-consistency]
dependencies: []
---

# StepTimer usage absent from new script code stubs

## Problem Statement

The plan mentions using `lib.log_utils.StepTimer` for timing in new scripts but the code stubs for `x_monitor.py` and `news_digest.py` don't show StepTimer integration.

## Findings

- Pattern recognition specialist identified prose/code mismatch (Finding 4)
- `StepTimer` is the standard timing utility used across all pipeline scripts
- Code stubs don't import or use `StepTimer`

## Proposed Solutions

### Option 1: Add StepTimer to code stubs

**Approach:** Add `from lib.log_utils import StepTimer` and wrapping calls to code stubs.

**Effort:** Small (15 minutes)

**Risk:** Low

## Recommended Action

**To be filled during triage.**

## Technical Details

**Affected files:**
- Plan file — Issue #5 and #6 code stubs

## Acceptance Criteria

- [ ] New scripts use StepTimer for timing
- [ ] Code stubs show StepTimer integration

## Work Log

### 2026-03-05 - Code Review Discovery

**By:** Claude Code (`/ce:review`)

**Actions:**
- Pattern recognition specialist identified missing StepTimer (Finding 4)

## Resources

- **Review target:** `docs/plans/2026-03-05-fix-repo-wide-reliability-sweep-plan.md`
