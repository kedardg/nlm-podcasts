---
status: complete
priority: p2
issue_id: "027"
tags: [codex-review, pipeline]
dependencies: []
---

# Scope weekly delivery to current run_id

## Problem

Weekly pipeline delivery phase selected every notebook with
`status == "generating"`, with no filter on run_id or date. If a prior run
timed out before marking entries complete, the next run would re-deliver stale
notebooks, duplicating or cross-pollinating weekly content.

## Fix Applied

**scripts/weekly_pipeline.sh** — Delivery now:
1. Marks stale `generating` entries from prior `run_id`s as `abandoned`.
2. Filters pending notebooks to `run_id == weekly_$DATE` only.

## Source

Codex adversarial review (2026-04-15) — MEDIUM finding.
