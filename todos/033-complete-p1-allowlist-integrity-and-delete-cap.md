---
status: complete
priority: p1
issue_id: "033"
tags: [code-review, security, data-integrity]
dependencies: ["032"]
---

# Allowlist has no integrity mechanism, and a count check is not a membership check

## Problem Statement

The sole authority for 465 irreversible deletions is an untracked file with no digest and no history, guarded only by a cardinality assertion. A regeneration bug that swaps 10 personal notebooks for 10 pipeline notebooks still yields 465 and passes.

## Findings

- Verified: `git check-ignore -v data/backfill-allowlist.json` → `.gitignore:9:data/`. No history, no diff, no recovery.
- `backup.sh:26-32` copies a hardcoded list of 7 JSONs; the allowlist is not among them.
- R0 itself concedes that 5 of the 34 excluded notebooks are topically indistinguishable from pipeline output and that eyeballing will not catch an error — so the count check is the only automated barrier, and it does not check membership.
- Nothing caps deletes per run: `--execute` with no `--limit` destroys all 465 on a mistaken invocation.

## Proposed Solutions

1. **Pin the set, cap the blast radius (recommended).**
   - Commit `sha256(canonical_json(sorted_ids))` to a tracked path **outside** `data/` (e.g. `config/backfill-allowlist.sha256`); verify at load, refuse on mismatch.
   - Add `frozen_at` and `generator_git_sha` inside the file.
   - Assert every id matches the UUID regex; `allowlist ∩ excluded == ∅`; `|allowlist ∪ excluded| == 499`; a live `nlm notebook list` contains no id absent from both files (drift halts the run).
   - Compile-time `MAX_DELETES_PER_RUN` (25) that `--limit` may only lower.
   - `--execute` refuses unless a byte-identical `delete-manifest.json` from a prior `--no-delete` pass exists — every deletion approved as a concrete list, never as a policy.
   - Commit the 34 excluded ids under `docs/` so protection survives loss of `data/`.
2. Count assertion only — current plan. Insufficient, as above.

## Acceptance Criteria

- [ ] Digest committed outside `data/` and verified at load
- [ ] Set-partition invariants asserted; drift since freeze halts the run
- [ ] `MAX_DELETES_PER_RUN` enforced and not raisable by flag
- [ ] `--execute` requires a matching pre-approved manifest
- [ ] Test: mutating one id in the allowlist causes a refusal
