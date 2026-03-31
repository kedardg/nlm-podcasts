---
status: complete
priority: p2
issue_id: "008"
tags: [code-review, architecture, quality]
dependencies: []
---

# Missing test plan for Phase 3 inline Python changes

## Problem Statement

Phase 3 modifies inline Python blocks embedded in bash heredocs (`weekly_pipeline.sh`). These blocks are untestable by `pytest` (they're string literals inside shell scripts) and unlintable. The plan has no test strategy for verifying these changes work correctly.

## Findings

- `weekly_pipeline.sh` lines 153-283: ~130 lines of inline Python for Step 5 orchestrator
- `weekly_pipeline.sh` lines 335-455: ~120 lines of inline Python for Step 7 delivery
- Changes to Issue #7 (unsafe JSON) and Issue #8 (ERRORS array) modify these inline blocks
- Architecture strategist noted these blocks should ideally be extracted to standalone scripts
- No test coverage is possible for inline Python — only full pipeline runs can verify

## Proposed Solutions

### Option 1: Add integration test notes to Phase 3 acceptance criteria

**Approach:** Add "run weekly pipeline dry-run after Phase 3 changes" as explicit acceptance criterion.

**Pros:**
- Simple, practical verification
- No code restructuring needed

**Cons:**
- Manual verification only
- Doesn't catch edge cases

**Effort:** Small (15 minutes to update plan)

**Risk:** Low

---

### Option 2: Extract inline Python to standalone scripts (longer term)

**Approach:** Move the 130-line and 120-line Python blocks to `scripts/weekly_orchestrator.py` and `scripts/weekly_delivery.py`. Call from bash. Now testable with pytest.

**Pros:**
- Testable, lintable Python code
- Removes security risk of shell variable interpolation in Python
- Easier to maintain

**Cons:**
- Larger refactor scope — may not belong in this sweep
- Creates new files

**Effort:** Medium (4-6 hours)

**Risk:** Medium — changes working pipeline structure

## Recommended Action

**To be filled during triage.**

## Technical Details

**Affected files:**
- `scripts/weekly_pipeline.sh:153-283` — Step 5 inline Python
- `scripts/weekly_pipeline.sh:335-455` — Step 7 inline Python

## Acceptance Criteria

- [ ] Phase 3 changes verified via pipeline dry-run
- [ ] Plan updated with explicit test strategy for inline Python modifications

## Work Log

### 2026-03-05 - Code Review Discovery

**By:** Claude Code (`/ce:review`)

**Actions:**
- Architecture strategist identified missing test plan
- Noted 250+ lines of inline Python as architectural debt
- Simplicity reviewer agreed extraction is valuable but out of scope for this sweep

## Resources

- **Review target:** `docs/plans/2026-03-05-fix-repo-wide-reliability-sweep-plan.md`
