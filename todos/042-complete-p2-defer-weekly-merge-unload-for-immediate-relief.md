---
status: complete
priority: p2
issue_id: "042"
tags: [code-review, architecture, scope]
dependencies: []
---

# The weekly→daily fold-in is a second project and buys nothing for the unblock

## Problem Statement

The plan folds x-monitor, `archive_old_news`, `hf_triage`, `data_triage`, and `content_processor` from `weekly_pipeline.sh` into `daily_pipeline.sh` — moving code between two bash scripts totalling 1,432 lines, each with its own locking, step numbering and failure handling. Weekly is **already dead** (it cannot create notebooks either), so `launchctl unload` achieves identical cap relief in one command.

## Findings

- `content_processor.py` folded into daily becomes a pure side-effect generator: its only runtime consumer is weekly Step 5 (`weekly_pipeline.sh:190`); no other reader of `content-processor-output.json` exists besides `scripts/_orchestrate_trimmed.py`.
- Worse, it flips every queued source to `processing` (`src/pipeline/content_processor.py:189-195`), which collides with `src/data_triage.py:134-158` resetting `processing`+no-`notebook_id` back to `queued` after 24 h. **Nothing in this repo ever populates `notebook_id` on a source** — only `blog_monitor.py:365`, `newsletter_parser.py:718`, `router.py:412,465` set it, always to `None`. Sources would oscillate queued→processing→queued forever.
- `data_triage.semantic_dedup` (`:82-121`) is an uncapped O(n²) pure-Python cosine over 1536-dim vectors. At 899 eligible sources that is ~404k comparisons — hours, inside the pipeline lock.
- Dead code the plan doesn't name: `magazine_generator.py` (529 lines; `weekly_pipeline.sh:341,345` is its only caller), `skills/{notebooklm-orchestrator,news-digest,digest-deliver,content-processor}/`, `_orchestrate_trimmed.py`, `tests/test_magazine.py`.
- In the fold-in's favour: `hf_triage.py` **should** move — `data/triage-results.json` is read by `deep_dive_generator.py:37` (a daily step) but produced only by weekly Step 2.5, an existing cross-pipeline coupling.

## Proposed Solutions

1. **`launchctl unload` now; fold in later as its own plan (recommended).** Move only `hf_triage` (fixes a real coupling). Don't fold `content_processor`; delete it and its outputs. Make `expire_sources` the single terminal transition over `{queued, processing}` and delete `reset_stale_processing()`. Split `data_triage` into `dedup` (keep, run after expiry so n is small) and `grouping` (delete).
2. Fold everything in now, as planned. Doubles the change surface during an outage.

## Acceptance Criteria

- [ ] Weekly launchd agent unloaded; cap relief confirmed independent of the fold-in
- [ ] `hf_triage` runs in daily; `deep_dive_generator.py:37` coupling resolved
- [ ] `content_processor` deleted, not relocated
- [ ] `semantic_dedup` capped or vectorized
- [ ] Orphaned modules/skills/tests enumerated and removed in a follow-up
