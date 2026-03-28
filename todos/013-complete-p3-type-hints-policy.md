---
status: complete
priority: p3
issue_id: "013"
tags: [code-review, quality, pattern-consistency]
dependencies: []
---

# Type hints policy unstated — inconsistent application in plan

## Problem Statement

The plan adds type hints to new functions but doesn't state a policy for existing functions. This creates inconsistency within `nlm_playwright.py` where some functions are typed and others aren't.

## Findings

- Pattern recognition specialist identified inconsistent type hint application
- New functions in plan have type hints; existing functions don't
- No explicit policy documented in CLAUDE.md or the plan

## Proposed Solutions

### Option 1: Document "new code typed, existing code not yet" policy

**Approach:** Add a one-line note to the plan: "Type hints on new/modified functions only. No retroactive typing."

**Effort:** Small (5 minutes)

**Risk:** Low

## Recommended Action

**To be filled during triage.**

## Technical Details

**Affected files:**
- Plan file — add policy note
- `CLAUDE.md` — optionally document convention

## Acceptance Criteria

- [ ] Type hint policy documented
- [ ] New functions follow stated policy

## Work Log

### 2026-03-05 - Code Review Discovery

**By:** Claude Code (`/ce:review`)

**Actions:**
- Pattern recognition specialist identified inconsistency (Finding 5)

## Resources

- **Review target:** `docs/plans/2026-03-05-fix-repo-wide-reliability-sweep-plan.md`
