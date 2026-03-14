---
status: complete
priority: p2
issue_id: "006"
tags: [code-review, architecture]
dependencies: []
---

# SF-2 and SF-3 must be coupled — reduce timeout and retry overspecification

## Problem Statement

SpecFlow issues SF-2 (per-source auth retry) and SF-3 (podcast timeout management) are architecturally coupled but placed in different plan sections without noting their dependency. SF-3's timeout calculation (10800s / 3 hours, 20 retries) is over-specified — NLM podcasts never take more than 60 minutes.

## Findings

- SF-2 proposes per-5-source auth checks — simplicity reviewer says per-group is sufficient
- SF-3 proposes `MAX_POLL_TIMEOUT = 10800` (3 hours) — real-world NLM generation takes 10-45 minutes
- SF-3 proposes 20 retries — 5 retries with exponential backoff is sufficient
- Both SF-2 and SF-3 touch the same code paths in `nlm_playwright.py` (source addition + polling loops)
- Architecture strategist flagged these as coupled — implementing one without the other creates inconsistency

## Proposed Solutions

### Option 1: Merge SF-2 + SF-3 into single implementation item

**Approach:** Combine into one checklist item: "Add auth retry per source group + reduce poll timeout to 3600s with 5 retries."

**Pros:**
- Prevents partial implementation
- Simpler specification
- Realistic timeout values

**Cons:**
- Slightly larger single change

**Effort:** Small (2 hours)

**Risk:** Low

## Recommended Action

**To be filled during triage.**

## Technical Details

**Affected files:**
- `scripts/nlm_playwright.py` — source addition loop, polling loop
- Plan SF-2 and SF-3 sections

## Acceptance Criteria

- [ ] Auth retry triggers per source group (not per individual source)
- [ ] Poll timeout set to 3600s (1 hour), not 10800s
- [ ] Maximum 5 retries with exponential backoff
- [ ] SF-2 and SF-3 implemented as a single change

## Work Log

### 2026-03-05 - Code Review Discovery

**By:** Claude Code (`/ce:review`)

**Actions:**
- Architecture strategist identified coupling between SF-2 and SF-3
- Simplicity reviewer flagged timeout overspecification (10800s → 3600s)
- Simplicity reviewer flagged retry overspecification (20 → 5)

## Resources

- **Review target:** `docs/plans/2026-03-05-fix-repo-wide-reliability-sweep-plan.md`
