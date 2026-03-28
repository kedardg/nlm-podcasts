---
status: complete
priority: p2
issue_id: "009"
tags: [code-review, quality]
dependencies: []
---

# SF-6 code snippet has wrong generate_podcast() call signature

## Problem Statement

The plan's SF-6 (route handler leak fix) contains a code snippet that calls `generate_podcast()` with an incorrect signature. If implemented as-written, it will fail at runtime.

## Findings

- Architecture strategist identified wrong call signature in SF-6 code example
- `generate_podcast()` actual signature: `generate_podcast(page, format, length, focus_text, record, timeout=120)`
- SF-6 snippet likely uses a simplified/incorrect parameter list
- This is a plan-level error that would become a runtime error if implemented verbatim

## Proposed Solutions

### Option 1: Fix the code snippet in the plan

**Approach:** Update SF-6 code example to match actual `generate_podcast()` signature.

**Pros:**
- Prevents runtime error during implementation
- Single edit

**Cons:**
- None

**Effort:** Small (15 minutes)

**Risk:** Low

## Recommended Action

**To be filled during triage.**

## Technical Details

**Affected files:**
- Plan file SF-6 section — code snippet
- `scripts/nlm_playwright.py:639` — actual `generate_podcast()` signature

## Acceptance Criteria

- [ ] SF-6 code snippet matches actual function signature
- [ ] Plan updated before implementation begins

## Work Log

### 2026-03-05 - Code Review Discovery

**By:** Claude Code (`/ce:review`)

**Actions:**
- Architecture strategist identified signature mismatch in SF-6

## Resources

- **Review target:** `docs/plans/2026-03-05-fix-repo-wide-reliability-sweep-plan.md`
