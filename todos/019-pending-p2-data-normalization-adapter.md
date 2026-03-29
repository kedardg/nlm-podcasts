---
status: pending
priority: p2
issue_id: "019"
tags: [code-review, architecture, html-first-magazine]
dependencies: ["017"]
---

# Data Normalization Adapter — `_to_magazine_json()`

## Problem Statement

Two HTML generators consume different data shapes. The daily pipeline uses `merged_data` (with `nlm_body`, `nlm_tldr`, `headline`). The weekly pipeline uses `magazine` JSON (with `editorial_brief`, `top_stories`, `significance`). The unified HTML renderer needs a single input schema.

Without this adapter, the article-first layout (items with full NLM body content) would be lost when switching to the unified renderer.

## Findings

**Source:** Architecture Strategist

- Daily `merged_data` has: `headline`, `takeaways`, items with `nlm_body`, `nlm_tldr`, `nlm_summary`
- Weekly `magazine` JSON has: `editorial_brief`, `top_stories`, items with `summary`, `content_type`, `significance`, `type`
- Items with `nlm_body` >= 300 chars should become `type: "featured"` with full body
- Helper functions `_render_article()` and `_text_similar()` from `daily_feed_generator.py` may need to move to `html_renderer.py`

## Proposed Solutions

### Solution 1: Adapter function in `daily_feed_generator.py` (Recommended)
**Pros:** Keeps format translation in the module that owns the source format
**Cons:** None significant
**Effort:** Small (0.5 day)

## Acceptance Criteria

- [ ] `_to_magazine_json(merged_data, target_date)` returns valid magazine JSON dict
- [ ] Items with `nlm_body` >= 300 chars mapped to `type: "featured"` with body content
- [ ] Items without body mapped to `type: "standard"`
- [ ] `headline` mapped to `title`, `editorial_brief` populated
- [ ] HTML renderer produces article-first layout for featured items

## Work Log

| Date | Action | Learnings |
|------|--------|-----------|
| 2026-03-28 | Created from plan review | Architecture strategist identified data shape divergence |

## Resources

- Plan: `docs/plans/2026-03-28-001-feat-html-first-responsive-magazine-plan.md` (Phase 2.3)
