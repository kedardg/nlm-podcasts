# 2026-02-19: /generate Redesign + Documentation System

## Summary

Redesigned the `/generate` command routing to fix failures when no queued sources exist, and created a structured documentation system for the project.

## Problem

`/generate report` was failing because the pipeline tried to create new NotebookLM notebooks from queued sources in `sources.json`. When there were no queued sources (e.g., between weekly runs), the command would error out with "no queued sources to process" instead of simply fetching the latest existing artifacts.

The fundamental issue: `/generate report` was conflating "create new content" with "fetch existing content." Users asking for a report just want the latest one delivered — they don't expect a full pipeline run.

## Solution

Split `/generate` into 3 distinct routes, each with a clear purpose:

| Command | Router Reason | Behavior |
|---------|--------------|----------|
| `/generate report\|podcast\|report+podcast` | `command_generate_fetch` | Fetch & deliver existing artifacts from the latest research notebook |
| `/generate news` | `command_generate_news_podcast` | Generate a new podcast from the latest news notebook's existing sources |
| `/generate daily` | `command_generate_daily` | Run `daily_feed_generator.py` to create today's PDF + podcast |

The key insight: `/generate report` should never create notebooks. It uses `fetch_latest.py` to find the most recent completed notebook and downloads its artifacts. Notebook creation is the job of `/process` and the Saturday cron.

## Changes Made

- **`scripts/fetch_latest.py`** (new) — Notebook lookup script. Reads `data/notebooks.json` (falls back to `nlm notebook list --json`), filters by category and artifact type, returns the latest matching notebook as JSON. Supports `--category news|research` and `--artifact report|podcast|both`.
- **`router.py`** — Updated `cmd_generate()` to return 3 different `reason` values based on the subcommand: `command_generate_fetch`, `command_generate_news_podcast`, `command_generate_daily`. Added `generate daily` as a new subcommand.
- **`~/.openclaw/workspace/TOOLS.md`** — Rewrote the generate section with 3 separate pipeline flows:
  1. **Fetch Latest Pipeline** — `fetch_latest.py` → `nlm download` → enhance → PDF → deliver
  2. **News Podcast Pipeline** — `fetch_latest.py --category news` → `nlm audio create` → deliver
  3. **Daily Feed Pipeline** — `newsletter_parser.py` → `daily_feed_generator.py` → deliver
- **`CLAUDE.md`** — Updated command table, added `fetch_latest.py` to triage section, updated troubleshooting for new "no queued sources" behavior.
- **`skills/digest-deliver/SKILL.md`** — Updated to handle the fetch-latest flow (download existing artifacts rather than expecting freshly-generated ones).
- **`skills/daily-digest/SKILL.md`** — Updated to match the new `command_generate_daily` route.

## Decisions & Rationale

- **Separate fetch from create**: The biggest source of confusion was `/generate report` sometimes creating notebooks and sometimes not, depending on queue state. Making it always fetch-only is predictable.
- **`fetch_latest.py` as a script, not inline logic**: Keeps TOOLS.md instructions simple. The LLM calls one script and gets JSON back, rather than parsing `nlm notebook list` output itself.
- **Fallback to `nlm notebook list --json`**: If `notebooks.json` is empty or stale, `fetch_latest.py` queries NotebookLM directly. This makes the command work even before the first weekly pipeline run.

## Also This Session

- Created the entire `docs/` documentation system (guide/, logs/, future/)
- Restructured 9 topic-specific subdirectories into flat guide files
- Created roadmap and platform features analysis

## Follow-up

- Monitor whether the fetch-latest flow works reliably via Telegram over the next few days
- The weekly orchestrator still uses LLM-generated date strings for notebook names — should migrate to deterministic dates
