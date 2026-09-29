---
status: complete
priority: p2
issue_id: "043"
tags: [code-review, data-integrity]
dependencies: []
---

# Resume-at-`capturing` can destroy the only good copy

## Problem Statement

The plan's resume table says `capturing` → "discard partial files, restart this notebook." If a **re-capture** (reached via `captured` → re-verify) crashes mid-way and the implementation clears `data/archive/<id>/`, a previously good capture is destroyed to redo a bad one. If the redo then fails — artifact expired, URL no longer propagating — neither copy exists. The archive is the only copy.

## Findings

- Plan resume table, state `capturing`.
- `data/` is gitignored and unbacked (see todo 034), so there is no second copy anywhere.

## Proposed Solutions

1. **Never mutate in place (recommended).** Capture into `data/archive/notebooks/<id>/.staging-<run_id>/`, fsync, then atomically rename into place after the DB commit. Discarding partials then only ever discards staging. This also gives todo 039's "freshly emptied directory per attempt" for free.

## Acceptance Criteria

- [ ] All capture writes land in a per-run staging dir
- [ ] Promotion to the live path is a single atomic rename after commit
- [ ] Test: crash during re-capture leaves the prior good capture intact
