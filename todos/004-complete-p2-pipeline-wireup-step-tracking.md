---
status: complete
priority: p2
issue_id: "004"
tags: [code-review, quality, pattern-consistency]
dependencies: []
---

# x_monitor and news_digest wireup missing step_ok/step_fail tracking

## Problem Statement

The plan's Issues #5 and #6 create new pipeline scripts (`x_monitor.py`, `news_digest.py`) but the weekly pipeline wireup code doesn't include `step_ok`/`step_fail` tracking calls. Pipeline failures from these steps won't appear in the summary log, breaking the established pattern where every step records success/failure.

## Findings

- `weekly_pipeline.sh` tracks every step with `step_ok "step_name"` / `step_fail "step_name" "error"` calls
- Plan's wireup code for x_monitor (Issue #5) and news_digest (Issue #6) omits these calls
- Without tracking, the ERRORS array and summary log will silently skip failures from these steps
- StepTimer logging (mentioned in plan prose) is also absent from the code stubs

## Proposed Solutions

### Option 1: Add step tracking to wireup code

**Approach:** Add `step_ok`/`step_fail` calls around the new script invocations in `weekly_pipeline.sh`, following the pattern of existing steps.

**Pros:**
- Consistent with all other pipeline steps
- Pipeline summary accurately reflects all step outcomes

**Cons:**
- None

**Effort:** Small (30 minutes)

**Risk:** Low

## Recommended Action

**To be filled during triage.**

## Technical Details

**Affected files:**
- `scripts/weekly_pipeline.sh` — wireup sections for steps 2 and 6
- `scripts/x_monitor.py` — new script (Issue #5)
- `scripts/news_digest.py` — new script (Issue #6)

## Acceptance Criteria

- [ ] x_monitor step records `step_ok` or `step_fail` in pipeline summary
- [ ] news_digest step records `step_ok` or `step_fail` in pipeline summary
- [ ] Failures from new steps appear in ERRORS array
- [ ] StepTimer logging integrated for timing data

## Work Log

### 2026-03-05 - Code Review Discovery

**By:** Claude Code (`/ce:review`)

**Actions:**
- Pattern recognition specialist identified missing step tracking (Finding 3)
- Cross-referenced with existing step tracking pattern in weekly_pipeline.sh

## Resources

- **Review target:** `docs/plans/2026-03-05-fix-repo-wide-reliability-sweep-plan.md`
