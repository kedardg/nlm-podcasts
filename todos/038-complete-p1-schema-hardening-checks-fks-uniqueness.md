---
status: complete
priority: p1
issue_id: "038"
tags: [code-review, data-integrity, database]
dependencies: []
---

# Schema under-specifies exactly the constraints that prevent an erroneous delete

## Problem Statement

The ERD's integrity guarantees are comments, not constraints. Verified on this machine: `PRAGMA foreign_keys` defaults **0**, and `UNIQUE(a,b)` admits **3 duplicate rows** when `b IS NULL`.

## Findings

- **FKs inert.** SQLite defaults `foreign_keys` OFF *per connection*. The three ERD relationships do nothing unless every connection sets the pragma.
- **No `CHECK` constraints** on `notebooks.state`, `notebooks.origin`, `artifacts.status`, `sources.content_status`, `runs.status`, `runs.kind`. A typo writes a state no resume branch handles — stranded, or worse, treated as deletable by one query and not another. This is the bug class that deletes things.
- **`UNIQUE` does not give the claimed idempotency.** `nlm_artifact_id` is frequently NULL: `services/studio.py:712-714` sets `artifact_id` only if it is a string; `downloads.py` propagates `artifact.get("artifact_id")` → `None` into `items[]`. Every id-less artifact duplicates on every re-capture while gate 4 still passes.
- **No `protected` state.** The 34 protected notebooks carry `origin='protected'` but sit in `pending` — the same state as a deletable notebook awaiting processing. Protection then depends on every query joining on `origin`; one `WHERE state='pending'` without it and protection evaporates.
- **`verified_at` is not invalidated on re-capture.** A row verified on run N, re-entering `capturing` on N+1 and crashing, resumes at `captured` carrying a stale `verified_at`.
- **No migration strategy.** No `PRAGMA user_version`. The ACs require a `has_local_text` column that is not in the ERD — Phase 2 would add a column to a DB already holding 465 captured notebooks with no migration path.
- **Transactions.** No `BEGIN IMMEDIATE` specified (Python's `sqlite3` begins deferred; a read-then-write can take `SQLITE_BUSY` at commit, where `busy_timeout` does not help). And the flow must never hold a write transaction across an 83 MB download.

## Proposed Solutions

1. **Harden (recommended).** `CHECK (... IN (...))` on all six enum columns; `PRAGMA foreign_keys=ON` on every connection with explicit `ON DELETE` behaviour; `nlm_artifact_id NOT NULL` with a deterministic surrogate `sha256(type|title|created_at)` when absent; add a terminal `protected` state *and* keep `origin` so both must be wrong; clear `verified_at`/`manifest_sha` on every entry into `capturing`, plus `CHECK`s tying state to timestamp presence; pin `user_version` and write migration 001 before Phase 1; `isolation_level=None` + explicit `BEGIN IMMEDIATE`; state the invariant **no transaction spans an I/O or subprocess call**.

## Acceptance Criteria

- [ ] All six enum columns have `CHECK` constraints
- [ ] `PRAGMA foreign_keys=ON` asserted on every connection
- [ ] `nlm_artifact_id NOT NULL`; surrogate id when NLM omits one
- [ ] Terminal `protected` state exists; test that a protected row is unselectable by the delete query
- [ ] `user_version` pinned; migration 001 written
- [ ] Test: no transaction is open across a subprocess call
