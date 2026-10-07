---
status: complete
priority: p1
issue_id: "036"
tags: [code-review, data-integrity, security]
dependencies: ["033"]
---

# The gate ignores the drift evidence the allowlist already stores

## Problem Statement

Gate 2 compares three numbers that all derive from the same `nlm source list` call, so a uniform truncation makes all three agree and all three wrong. Meanwhile the allowlist already freezes `{title, source_count, updated_at}` per id and the gate never reads them.

## Findings

- Gate 2's three values (enumeration, `len(source list)`, DB rows) share one upstream call — not independent evidence.
- `backfill-allowlist.json` stores `source_count` and `updated_at` at freeze time. Comparing live values against frozen ones is free, mechanical, and does not depend on human review.
- This is also the cheapest protection against a personal notebook having slipped into the allowlist: if the user has touched it since the freeze, `updated_at` or `source_count` moved.
- Title drift is a separate free tripwire — catches an operator renaming a notebook onto a reclaimed id. It is a tripwire, not a delete authority, so it does not reintroduce the pattern-matching risk the plan correctly rejected.

## Proposed Solutions

1. **Add condition 1b (recommended).** Live `title` must equal frozen `title` byte-for-byte; live `source_count` must equal frozen `source_count`; `updated_at` must not have advanced past the freeze. Any drift → `needs_human`.
2. Leave gate 2 as-is. Leaves the strongest available evidence unused.

## Acceptance Criteria

- [ ] Condition 1b implemented against frozen allowlist fields
- [ ] Test: mutating a frozen field causes refusal
- [ ] `needs_human` triage records all three observed counts, not just one
