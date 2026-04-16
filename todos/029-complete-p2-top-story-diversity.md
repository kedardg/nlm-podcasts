---
status: complete
priority: p2
issue_id: "029"
tags: [codex-review, feature]
dependencies: ["028"]
---

# Make top-story section-diversity iterative

## Problem

`select_top_stories()` sorted featured items by `diversity_key` once before
`used_sections` was populated. Because all sections start unseen, high-confidence
items from one section could fill the entire front page, defeating the intended
one-item-per-section behavior.

## Fix Applied

Replaced single `sorted()` with `pick_with_diversity()` — a round-based helper
that re-sorts by `diversity_key` after each pick, so `used_sections` is current
on every iteration. Applied to both featured and standard-research fill pools.

## Source

Codex review (2026-04-15) — P2 finding.
