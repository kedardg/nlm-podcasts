---
status: pending
priority: p2
issue_id: "020"
tags: [code-review, pipeline, html-first-magazine]
dependencies: ["017", "018"]
---

# Pipeline Flag Changes + Delivery Update

## Problem Statement

Both `daily_pipeline.sh` and `weekly_pipeline.sh` need to be updated to produce and deliver `.html` instead of `.pdf` by default. CLI flags need updating in `daily_feed_generator.py` and `magazine_generator.py`.

## Findings

**Sources:** Code Simplicity Reviewer, Pattern Recognition Specialist

- Just change the flags — no deprecation warnings needed (single-user project)
- Remove `--magazine` entirely, remove `--pdf-only`
- Add `--pdf` flag for optional PDF alongside HTML
- Pattern specialist warns `--pdf` alongside `--pdf-only` creates naming collision — solution: just remove `--pdf-only`
- `fetch_latest.py` needs to glob for `.html` first, fall back to `.pdf`
- No `"format"` JSON field needed — file extension is the format
- Use descriptive filename: `Research-Digest-2026-03-28.html`
- Add `disable_content_type_detection=true` to Telegram `sendDocument`

## Proposed Solutions

### Solution 1: Direct flag changes + script updates (Recommended)
**Effort:** Small (0.5 day)

## Acceptance Criteria

- [ ] `daily_feed_generator.py`: no flags = HTML, `--pdf` = HTML + PDF
- [ ] `--magazine` and `--pdf-only` flags removed
- [ ] `magazine_generator.py`: same flag semantics
- [ ] `daily_pipeline.sh` sends `.html` via Telegram
- [ ] `weekly_pipeline.sh` sends `.html` via Telegram
- [ ] `fetch_latest.py` globs for `.html` first
- [ ] `fetch_and_deliver.sh` works with `.html` files
- [ ] Descriptive filename used in delivery

## Work Log

| Date | Action | Learnings |
|------|--------|-----------|
| 2026-03-28 | Created from plan review | Simplicity + pattern reviewers informed flag design |

## Resources

- Plan: `docs/plans/2026-03-28-001-feat-html-first-responsive-magazine-plan.md` (Phase 3)
