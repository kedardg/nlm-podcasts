---
status: complete
priority: p1
issue_id: "025"
tags: [codex-review, data-integrity]
dependencies: []
---

# Fix news-queue.json bootstrap schema + loader migration

## Problem

`setup.sh` initializes `data/news-queue.json` as `{"items":[]}`, but all runtime
code (newsletter_parser, blog_monitor) reads and appends via
`news_queue["articles"]`. On a fresh install, the first news ingestion raises
`KeyError`, silently breaking daily and weekly news flows.

## Fix Applied

1. **setup.sh:238** — Changed init schema from `{"items":[]}` to `{"articles":[]}`.
2. **lib/file_utils.py** — Added `load_news_queue(path)` migration helper that
   normalizes `{"items":...}` → `{"articles":...}` and guarantees an `articles`
   list for append safety.
3. **lib/__init__.py** — Re-exported `load_news_queue`.
4. **newsletter_parser.py:666** — Switched to `load_news_queue()`.
5. **blog_monitor.py:402** — Switched to `load_news_queue()`.

## Source

Codex adversarial review (2026-04-15) — HIGH finding.
