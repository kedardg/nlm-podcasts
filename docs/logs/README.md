# Logs

Session notes, design decisions, and change history. Each entry documents a significant change or debugging session.

## Conventions

- **Filename**: `YYYY-MM-DD-short-description.md`
- **One entry per session** (not per commit). A session might span multiple commits.

## What to Log

- Design decisions and their rationale
- Major pipeline redesigns or refactors
- Debugging sessions that uncovered non-obvious issues
- New subsystem additions
- Architecture changes

## Template

```markdown
# YYYY-MM-DD: Short Title

## Summary
One-paragraph overview of what happened and why.

## Problem
What was broken or needed to change.

## Solution
What was done to fix/improve it.

## Changes Made
- `file.py` — description of change
- `other-file.md` — description of change

## Decisions & Rationale
- **Decision**: Why we chose this approach over alternatives.

## Follow-up
- Any remaining work or things to watch.
```

## Entries

| Date | Entry | Summary |
|------|-------|---------|
| 2026-02-19 | [generate-redesign](2026-02-19-generate-redesign.md) | Split `/generate` into 3 routes + created docs system |
| 2026-02-19 | [resilience-and-fetch-only](2026-02-19-resilience-and-fetch-only.md) | Retry/backoff + alerts + fetch-only generate |
| 2026-02-19 | [script-wrappers](2026-02-19-script-wrappers.md) | Move orchestration out of LLM into deterministic scripts |
