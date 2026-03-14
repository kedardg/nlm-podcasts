# 2026-02-19: Pipeline Resilience + Fetch-Only Generate

## Summary

Added retry logic with exponential backoff, Telegram alerts on failures, and continue-on-failure rules across all pipeline skills. Also unified all `/generate` commands to be fetch-only (no creation), with content creation reserved exclusively for cron jobs and `/process`.

## Problem

Two issues:

1. **No retry logic**: If NLM auth expired or a network request failed, the entire pipeline stopped dead. The Saturday cron would silently fail with no notification.

2. **`/generate` inconsistency**: `/generate news` created a new podcast, `/generate daily` ran the full daily generator. Users expected all `/generate` commands to just deliver existing content, with creation being the cron's job.

## Solution

### Retry + Alerts

- `scripts/retry.sh` — generic retry wrapper with exponential backoff (5s, 15s, 45s)
- `scripts/nlm_ensure_auth.sh` — now retries 3 times with backoff, sends Telegram alert on final failure
- `scripts/pipeline_alert.sh` — sends Telegram alerts for any pipeline step failure
- All SKILL.md files updated: "NEVER stop the pipeline. Always continue to the next step."
- NLM auth failure skips NLM-dependent steps but continues with non-NLM work (triage, PDF generation, status updates)

### Fetch-Only Generate

- All `/generate` subcommands now route to `command_generate_fetch` with `category` + `artifact` fields
- `fetch_latest.py` gained `--category daily` support (looks up latest PDF from `data/daily-feed/`)
- Removed `command_generate_news_podcast` and `command_generate_daily` routes
- TOOLS.md has one unified Fetch Latest Pipeline instead of three separate sections

## Changes Made

- `scripts/retry.sh` — new generic retry wrapper
- `scripts/pipeline_alert.sh` — new Telegram alert helper
- `scripts/nlm_ensure_auth.sh` — added 3 retries with backoff + Telegram alert on failure
- `scripts/fetch_latest.py` — added `--category daily` (filesystem lookup for daily-feed PDFs)
- `router.py` — unified all `/generate` routes to `command_generate_fetch`
- `~/.openclaw/workspace/TOOLS.md` — replaced 3 generate pipelines with 1 unified fetch pipeline, added resilience rules to process pipeline
- `skills/notebooklm-orchestrator/SKILL.md` — retry on source addition, continue on auth failure, alert on errors
- `skills/digest-deliver/SKILL.md` — retry on downloads/sends, continue on auth failure
- `skills/news-digest/SKILL.md` — retry on source addition, continue on auth failure
- `skills/newsletter-monitor/SKILL.md` — alert on IMAP failure, continue pipeline
- `skills/x-monitor/SKILL.md` — alert on API failure, continue pipeline
- `CLAUDE.md` — updated commands table, added new scripts to components section
- `docs/guide/setup.md` — updated auth troubleshooting
- `docs/guide/generation.md` — updated to reflect fetch-only design

## Decisions & Rationale

- **Exponential backoff (5s, 15s, 45s)**: Covers transient failures without waiting too long. 3 retries = ~65s total max wait, reasonable for a background cron.
- **Alert via Telegram**: The user gets notified in the same channel where results arrive. No separate monitoring needed.
- **Continue on failure**: A failed NLM auth shouldn't prevent newsletter parsing, triage, or daily PDF delivery. Partial results are better than no results.
- **Fetch-only generate**: Clear separation of concerns. Users always get instant results from `/generate`. Creation is always scheduled.
- **`openclaw cron run` for re-trigger**: Built into OpenClaw, no custom re-trigger mechanism needed.
