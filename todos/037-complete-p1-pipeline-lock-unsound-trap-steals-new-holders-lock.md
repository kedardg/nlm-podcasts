---
status: complete
priority: p1
issue_id: "037"
tags: [code-review, architecture, concurrency]
dependencies: []
---

# The pipeline lock is unsound, and the plan builds irreversible deletes on top of it

## Problem Statement

`data/.pipeline.lock` is a `mkdir` lockdir with a stale-timeout heuristic and an unconditional release trap. A reclaim run over 465 notebooks will exceed both thresholds, and the race has irreversible deletes on one side.

## Findings

- `scripts/daily_pipeline.sh:114-137`, `scripts/weekly_pipeline.sh:72-90`.
- **Nothing is written inside the lockdir**, so the plan's acceptance criterion "staleness decided by PID liveness" is not implementable against it.
- Stale detection is `rmdir` then `mkdir` — two processes can both observe stale and both succeed.
- **Every process installs `trap 'rmdir "$LOCKDIR"' EXIT` unconditionally.** A process whose lock was stolen deletes the *new holder's* lock on exit. This is the dangerous half and the plan does not mention it.
- Timeouts are asymmetric: verified `daily_pipeline.sh:122` = 14400 s, `weekly_pipeline.sh:78` = 7200 s.
- The plan's AC also asks for per-notebook lock hold (<300 s) *and* "ChromaDB writes serialized behind the same lock" — mutually unsatisfiable, since releasing between notebooks lets a daily run start mid-reclaim and both then write Chroma, whose `PersistentClient` is not multi-process-safe.

## Proposed Solutions

1. **Replace with `flock(2)` (recommended).** Kernel-released on process death; no staleness heuristic, no trap. Then three distinct locks: a coarse pipeline lock, a per-notebook work lock, and a dedicated short-held ChromaDB write lock acquired **before** the client is constructed. `research.db` (WAL + `busy_timeout`) needs none of them.
2. Patch the trap and equalize timeouts. Cheaper, but keeps the double-acquire race.

## Acceptance Criteria

- [ ] `flock`-based locking in both pipelines; unconditional `trap rmdir` removed
- [ ] ChromaDB writes hold an OS-level file lock taken before client construction
- [ ] Test: reclaim + `daily_pipeline.sh --skip-nlm` concurrently — both complete, no interleaved Chroma writes
- [ ] Lands in Phase 1, not Phase 4 — reclaim is the first thing to exceed 4 h
