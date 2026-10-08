---
status: complete
priority: p1
issue_id: "032"
tags: [code-review, architecture, data-integrity]
dependencies: []
---

# The frozen allowlist cannot authorize daily deletes — the plan contradicts itself

## Problem Statement

Gate condition 1 requires `notebook_id ∈ backfill-allowlist.json`, and the acceptance criteria call that file "the **sole** delete authority." A notebook the daily pipeline creates tomorrow is by construction absent from a frozen 465-id file, so daily can never delete — contradicting the acceptance criterion "Daily run adds zero net notebooks."

## Findings

- Plan gate condition 1 vs AC "Daily run adds zero net notebooks" are mutually unsatisfiable as written.
- Loosening the allowlist to admit new ids destroys the R0 protection it exists to provide.

## Proposed Solutions

1. **Two parameterized authorities (recommended).**
   - `ReclaimAuthority`: membership in the frozen file. Used only by `reclaim_notebooks.py`.
   - `DailyAuthority`: the `notebooks` row carries the current `run_id`, `origin='daily'`, and reached `captured` **inside this process**. Used only by the daily step.
   - Neither may fall back to the other. Enforced by type, not convention.
2. Append to the allowlist at creation time — rejected: a mutable delete authority reintroduces R0.

## Acceptance Criteria

- [ ] Two distinct authority types exist; each delete path accepts exactly one
- [ ] Test: a daily-created notebook is deletable by the daily path and **not** by the reclaim path
- [ ] Test: an allowlist id is deletable by reclaim and **not** by the daily path
