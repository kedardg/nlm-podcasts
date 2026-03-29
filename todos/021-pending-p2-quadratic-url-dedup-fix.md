---
status: pending
priority: p2
issue_id: "021"
tags: [code-review, performance, html-first-magazine]
dependencies: []
---

# Fix Quadratic URL Dedup in `merge_nlm_with_feed()`

## Problem Statement

At `daily_feed_generator.py` line 1027, a new set is constructed from all `merged_items` on every iteration of the `nlm_extras` loop: `if norm in {normalize_url_deep(mi["url"]) for mi in merged_items}`. This is O(N*M) set constructions.

## Findings

**Source:** Performance Oracle

- With 50-100 items, each section has ~25 items * ~25 extras = ~625 set constructions
- Each calls `normalize_url_deep()` which involves regex operations
- Currently negligible but becomes dominant bottleneck at scale

## Proposed Solutions

### Solution 1: Pre-build the set once (Recommended)
```python
merged_url_set = {normalize_url_deep(mi["url"]) for mi in merged_items}
for entry in nlm_extras:
    norm = normalize_url_deep(entry["url"]) if entry["url"] else ""
    if norm in merged_url_set:
        continue
    merged_url_set.add(norm)
```
**Effort:** Small (5 lines)

## Acceptance Criteria

- [ ] Set built once outside the loop
- [ ] Updated incrementally as items are added
- [ ] Existing dedup behavior preserved

## Work Log

| Date | Action | Learnings |
|------|--------|-----------|
| 2026-03-28 | Created from plan review | Performance oracle found O(N*M) pattern |
