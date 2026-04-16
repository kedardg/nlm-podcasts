---
status: complete
priority: p1
issue_id: "028"
tags: [codex-review, data-integrity]
dependencies: []
---

# Normalize URLs in magazine NLM lookup + dedup

## Problem

`magazine_generator.py` had two URL-normalization gaps:

1. **NLM lookup miss (P1):** `build_magazine_items()` probed `nlm_url_map` with
   the raw triage URL, but `parse_nlm_report()` (in daily_feed_generator.py)
   stored keys via `normalize_url_deep()`. arXiv abs/pdf variants, GitHub casing,
   and newsletter tracking parameters caused lookups to miss, silently
   downgrading research items from `featured` to empty `standard`.

2. **Cross-section dedup leak (P2):** `group_items_by_section()` deduped on raw
   URL strings, so equivalent URLs differing by canonical form survived into
   multiple sections.

## Fix Applied

1. Added `from lib.url_utils import normalize_url_deep` import.
2. **build_magazine_items:199** — NLM lookup now uses
   `normalize_url_deep(url)` as key.
3. **group_items_by_section:100** — Dedup `seen_urls` set now stores and checks
   canonical URLs.

## Source

Codex review (2026-04-15) — P1 + P2 findings.
