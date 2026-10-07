---
status: complete
priority: p2
issue_id: "046"
tags: [code-review, architecture]
dependencies: []
---

# Daily idempotency is keyed on an alias the plan deletes

## Problem Statement

`scripts/daily_pipeline.sh:313` guards against duplicate work with `nlm alias get "daily-$DATE"`. After capture + `nlm delete alias`, a rerun or a user `/process` on the same date creates a **second** notebook and regenerates the full studio set.

## Findings

- `daily_pipeline.sh:312-318` idempotency guard; `:333` alias set.
- Related: `/process` and the LLM skill path (`skills/notebooklm-orchestrator/SKILL.md`, `~/.openclaw/workspace/TOOLS.md`) create notebooks outside the lifecycle entirely — no DB row, and not in the frozen allowlist, so permanently unreclaimable and free to re-fill the cap.
- Verification condition 5 interacts badly: Step 6.6b (`:608`, `:647`) tolerates a 1200 s timeout, leaving an artifact generating server-side → condition 5 fails → notebook survives. On any slow day the run leaks a notebook. The ACs assert both "zero net notebooks" and condition 5.

## Proposed Solutions

1. **Move idempotency to the DB and close the creation paths (recommended).** `SELECT … WHERE run_date=? AND state IN ('captured','deleted')` → skip generation, deliver from disk. Repoint `/process` at `daily_pipeline.sh` or delete the orchestrator skill. For condition 5, either give it a bounded extra wait plus an explicit `discarded_timeout` transition that permits delete, or accept a 2-notebook steady state and add a "sweep yesterday's captured-but-undeleted notebooks" step at the top of each run. Pick one and write it down.

## Acceptance Criteria

- [ ] Idempotency keyed on the DB, not an alias
- [ ] AC added: "no notebook-creating code path exists outside `daily_pipeline.sh`"
- [ ] Condition-5 timeout behaviour explicitly specified and consistent with the zero-net-notebooks AC
