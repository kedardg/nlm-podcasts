---
status: complete
priority: p2
issue_id: "030"
tags: [codex-review, pipeline]
dependencies: []
---

# Respect ARTIFACT in daily fetch_and_deliver

## Problem

`fetch_and_deliver.sh` daily path always sent both report and podcast whenever
either existed, regardless of the `$ARTIFACT` argument (report/podcast/both).
`/generate daily` is routed as report-only, so users would unexpectedly receive
audio.

## Fix Applied

Added `WANT_REPORT` / `WANT_PODCAST` flags gated on `$ARTIFACT`, matching the
research/news path's existing behavior.

## Source

Codex review (2026-04-15) — P2 finding.
