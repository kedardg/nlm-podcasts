---
status: complete
priority: p2
issue_id: "005"
tags: [code-review, architecture, simplicity]
dependencies: []
---

# Issue #10 (gateway plugin async) over-engineered — drop TypeScript interfaces

## Problem Statement

The plan's Issue #10 proposes 4 TypeScript interfaces (`HealthConfig`, `HealthState`, `HealthProbeResult`, `AuthResult`), Promise wrappers, and detailed error handling for what is a simple `execFileSync` → `execFile` conversion. The gateway plugin is ~239 lines total. This level of ceremony is disproportionate.

## Findings

- Plan specifies 4 new TypeScript interfaces for a plugin that's 239 lines
- The actual change is replacing `execFileSync` with `execFile` + promisify in 2-3 call sites
- `execFile` is already imported at line 1 but unused — just need to use it
- Dead `ENSURE_SCRIPT` variable at line 8 can be removed
- Simplicity reviewer estimated 60% of Issue #10's plan content is unnecessary

## Proposed Solutions

### Option 1: Reduce to 3-item checklist (Recommended)

**Approach:** Replace Issue #10 specification with:
1. Replace `execFileSync` calls with `execFile` + util.promisify
2. Remove dead `ENSURE_SCRIPT` variable
3. Read `PROJECT_ROOT` from `process.env` with current value as fallback

No new interfaces. No new types. Just the mechanical changes.

**Pros:**
- Matches actual complexity of the change
- Faster to implement
- Less code to maintain

**Cons:**
- Less "rigorous" TypeScript typing (but unnecessary for a 239-line plugin)

**Effort:** Small (1 hour)

**Risk:** Low

## Recommended Action

**To be filled during triage.**

## Technical Details

**Affected files:**
- `~/.openclaw/extensions/gateway-health/index.ts:1` — `execFile` already imported
- `~/.openclaw/extensions/gateway-health/index.ts:4` — hardcoded `PROJECT_ROOT`
- `~/.openclaw/extensions/gateway-health/index.ts:8` — dead `ENSURE_SCRIPT`
- `~/.openclaw/extensions/gateway-health/index.ts:12-88` — sync functions to convert

## Acceptance Criteria

- [ ] `execFileSync` replaced with async `execFile` in all call sites
- [ ] Dead `ENSURE_SCRIPT` variable removed
- [ ] `PROJECT_ROOT` reads from `process.env.PROJECT_ROOT` with fallback
- [ ] No new TypeScript interfaces added
- [ ] Plugin still functions correctly after changes

## Work Log

### 2026-03-05 - Code Review Discovery

**By:** Claude Code (`/ce:review`)

**Actions:**
- Simplicity reviewer identified over-engineering in Issue #10 specification
- Architecture strategist confirmed mechanical nature of the change
- Verified `execFile` already imported but unused

## Resources

- **Review target:** `docs/plans/2026-03-05-fix-repo-wide-reliability-sweep-plan.md`
