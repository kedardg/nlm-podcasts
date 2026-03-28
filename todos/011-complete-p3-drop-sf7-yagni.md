---
status: complete
priority: p3
issue_id: "011"
tags: [code-review, simplicity]
dependencies: []
---

# Drop SF-7 — YAGNI, --use-nlm-cli flag not needed

## Problem Statement

SF-7 proposes adding a `--use-nlm-cli` fallback flag to `daily_pipeline.sh`. Since the entire reliability sweep is about making Playwright the primary path, maintaining a fallback to the broken NLM CLI adds complexity without value.

## Findings

- Simplicity reviewer flagged SF-7 as YAGNI
- The NLM CLI path has known unfixable bugs (BUG-001: TooManyRedirects)
- Maintaining two code paths doubles testing surface
- If Playwright doesn't work, the fix is to fix Playwright — not fall back to broken tooling

## Proposed Solutions

### Option 1: Remove SF-7 from the plan

**Approach:** Delete SF-7 section. Keep Playwright as the only path.

**Effort:** Small (5 minutes)

**Risk:** Low

## Recommended Action

**To be filled during triage.**

## Technical Details

**Affected files:**
- Plan file — SF-7 section

## Acceptance Criteria

- [ ] SF-7 removed from plan
- [ ] No `--use-nlm-cli` flag added to daily_pipeline.sh

## Work Log

### 2026-03-05 - Code Review Discovery

**By:** Claude Code (`/ce:review`)

**Actions:**
- Simplicity reviewer identified YAGNI violation

## Resources

- **Review target:** `docs/plans/2026-03-05-fix-repo-wide-reliability-sweep-plan.md`
