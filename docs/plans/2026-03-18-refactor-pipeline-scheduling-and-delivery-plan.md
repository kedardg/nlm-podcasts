---
title: "refactor: Decouple pipeline from OpenClaw cron, use system cron + direct Telegram Bot API"
type: refactor
status: active
date: 2026-03-18
origin: docs/brainstorms/2026-03-18-pipeline-orchestration-and-web-publishing-brainstorm.md
---

# refactor: Decouple Pipeline from OpenClaw Cron

## Overview

Replace OpenClaw's LLM-mediated cron scheduling with system crontab inside Docker, and replace all `openclaw message send` calls in pipeline scripts with direct Telegram Bot API (`curl`). This eliminates the unreliable LLM middleman that caused missed deliveries March 12-18, while making the pipeline fully self-contained.

This is Phase 1 of the orchestration brainstorm. Phases 2-4 (Astro web publishing) are separate.

## Problem Statement / Motivation

OpenClaw cron creates an isolated LLM session (gpt-5-mini) and sends it a text message to interpret. The LLM then needs to call its bash tool to execute the pipeline script. This failed in two ways:

1. **LLM called `cron.run` instead of bash** — gpt-5-mini interpreted "Run this exact command" as a meta-action to re-trigger the cron job (March 17-18)
2. **"already-running" ghost state** — a previous manual run left stale session state, blocking the LLM's retry

The pipeline itself is a deterministic bash script that already handles its own Telegram delivery, error alerts, and logging. The LLM adds zero value to the scheduling path.

(see brainstorm: docs/brainstorms/2026-03-18-pipeline-orchestration-and-web-publishing-brainstorm.md — Decision #1: Scheduling)

## Proposed Solution

### 1. Docker entrypoint that starts crond + OpenClaw gateway

Replace `CMD ["openclaw", "gateway", "run"]` with a bash entrypoint that:
1. Installs the crontab from `/app/config/crontab`
2. Starts the cron daemon in background
3. Runs `openclaw gateway run` in foreground (PID 1 for Docker)

No `env >> /etc/environment` — scripts load their own env via `config/env.sh` which sources `.env`.

### 2. Direct Telegram Bot API helper

Create `scripts/telegram_send.sh` with three functions:
- `tg_send_message "$text"` — `curl` POST to `sendMessage` (HTML parse mode)
- `tg_send_file "$file_path" "$caption"` — `curl` POST to `sendDocument`
- `tg_send_audio "$file_path" "$caption"` — `curl` POST to `sendAudio` (inline player for podcasts)

All functions: validate `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set, check HTTP status code, log failures to stderr, return non-zero on error.

### 3. Migrate all `openclaw message send` calls

Replace in 6 files:

| File | Lines | Type | Migration |
|------|-------|------|-----------|
| `scripts/pipeline_alert.sh` | 21 | text | `tg_send_message` |
| `scripts/nlm_ensure_auth.sh` | 98 | text | `tg_send_message` |
| `scripts/daily_pipeline.sh` | 170, 566, 580, 589 | text + media | `tg_send_message`, `tg_send_file` (PDF), `tg_send_audio` (podcast) |
| `scripts/fetch_and_deliver.sh` | 52 | media | `tg_send_file` |
| `scripts/weekly_pipeline.sh` | 484, 495 | media (embedded Python) | Python `requests.post()` helper inline |

Also remove the `cp` to `$HOME/.openclaw/workspace/` pattern — Bot API accepts any local path directly.

### 4. Remove OpenClaw cron jobs

Delete the two OpenClaw cron entries. System crontab replaces them.

## Technical Considerations

### Telegram Bot API design choices

- **Error handling**: All `tg_*` functions check HTTP status code via `curl -w '%{http_code}'`, log failures to stderr, return non-zero. Callers already have `&& step_ok || step_fail` patterns.
- **Parse mode**: Use no `parse_mode` for messages with dynamic content (avoids Markdown escaping issues with `*`, `_`, `` ` ``). Use `parse_mode=HTML` only for static templates.
- **Podcasts**: Use `sendAudio` (not `sendDocument`) for MP3 — gives inline player in Telegram.

### Timezone handling

Set `TZ=America/New_York` in Dockerfile AND symlink `/etc/localtime`:
```dockerfile
ENV TZ=America/New_York
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
```

Debian Bookworm ships ISC Cron (Vixie-derived) which reads `/etc/localtime` and handles DST transitions correctly. The `TZ` env var is belt-and-suspenders.

Crontab entries use local time:
```
0 11 * * * bash /app/scripts/daily_pipeline.sh >> /app/data/daily-pipeline.log 2>&1
0 9  * * 6 bash /app/scripts/weekly_pipeline.sh >> /app/data/weekly-pipeline.log 2>&1
```

### Cron environment — no /etc/environment needed

Scripts already source `config/env.sh` (which loads `.env`) at startup. No need to dump Docker env to `/etc/environment` (risks mangling values with special characters, exposes secrets).

### Weekly pipeline embedded Python

Replace `subprocess.run(['openclaw', 'message', 'send', ...])` with Python `requests.post()` — avoids bash quoting issues with file paths and captions containing special characters. See Phase B for details.

### Healthcheck — include crond

Update Docker healthcheck to verify both processes:
```yaml
healthcheck:
  test: ["CMD-SHELL", "openclaw daemon status && pgrep cron"]
```

### Log rotation

Add a simple size check at the top of each pipeline script: if log file > 10MB, rotate to `.log.old`:
```bash
LOG_FILE="$PROJECT_ROOT/data/daily-pipeline.log"
[[ -f "$LOG_FILE" && $(stat -c%s "$LOG_FILE" 2>/dev/null || stat -f%z "$LOG_FILE" 2>/dev/null || echo 0) -gt 10485760 ]] && mv "$LOG_FILE" "${LOG_FILE}.old"
```

### Concurrent pipeline lock

Both scripts already implement a `LOCKDIR` pattern (`data/.pipeline.lock`) with stale lock detection (>2 hours). No changes needed.

## Acceptance Criteria

- [ ] System crontab runs `daily_pipeline.sh` at 11 AM ET and `weekly_pipeline.sh` at Saturday 9 AM ET
- [ ] `crontab -l` inside container shows both entries
- [ ] No OpenClaw cron jobs exist (`openclaw cron list` returns empty)
- [ ] All pipeline Telegram messages (text + files) delivered via direct Bot API
- [ ] Telegram delivery errors logged to stderr (not silently discarded)
- [ ] Podcasts delivered via `sendAudio` with inline player
- [ ] `TELEGRAM_BOT_TOKEN` added to `.env.example` and `.env`
- [ ] Docker healthcheck verifies both gateway and crond (`pgrep cron`)
- [ ] Pipeline alert on failure still works via direct Bot API
- [ ] Cron logs in `data/daily-pipeline.log` and `data/weekly-pipeline.log` with rotation
- [ ] `$WORKSPACE` copy pattern removed from all pipeline scripts
- [ ] Manual `bash scripts/daily_pipeline.sh` on macOS host still works (with `TELEGRAM_BOT_TOKEN` in `.env`)

## Implementation Phases

### Phase A: Telegram helper + .env changes

**Create `scripts/telegram_send.sh`** with three functions:
- `tg_send_message "$text"` — sendMessage, no parse_mode (safe for dynamic content)
- `tg_send_file "$path" "$caption"` — sendDocument for PDFs and generic files
- `tg_send_audio "$path" "$caption"` — sendAudio for MP3 podcasts (inline player)

All functions must: validate `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are set (fail fast), check HTTP status via `curl -w '%{http_code}'`, log failures to stderr, return non-zero on 4xx/5xx

**Update `.env.example`:** add `TELEGRAM_BOT_TOKEN=your-bot-token`

**User action:** Copy bot token from `~/.openclaw/openclaw.json` (field: `channels.telegram.botToken`) → `.env`

### Phase B: Migrate pipeline scripts (6 files)

For each file:
1. Add `source "$PROJECT_ROOT/scripts/telegram_send.sh"` after the existing `config/env.sh` source
2. Replace `openclaw message send --message` → `tg_send_message`
3. Replace `openclaw message send --media` → `tg_send_file` (or `tg_send_audio` for `.mp3`)
4. Remove `mkdir -p "$WORKSPACE"` and `cp ... "$WORKSPACE/"` lines

For `weekly_pipeline.sh` embedded Python:
- Add `import requests, os` at top of Python block
- Replace `subprocess.run(['openclaw', 'message', 'send', ...])` with `requests.post()` calls
- Check `r.ok` and log failures

### Phase C: Docker cron setup

1. **Create `config/crontab`** — two entries, local ET times, log to `data/*.log`

2. **Create `scripts/docker-entrypoint.sh`** — install crontab, start crond, exec gateway

3. **Update `Dockerfile`:**
   - `ENV TZ=America/New_York`
   - `RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone`
   - `RUN apt-get update && apt-get install -y cron && rm -rf /var/lib/apt/lists/*`
   - `RUN chmod +x /app/scripts/docker-entrypoint.sh`
   - Replace `CMD` with `ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]`

4. **Update `docker-compose.yml`:**
   - Remove `command: openclaw gateway run`
   - Update healthcheck: `test: ["CMD-SHELL", "openclaw daemon status && pgrep cron"]`

### Phase D: Remove OpenClaw cron + cleanup

1. Remove OpenClaw cron jobs from container
2. Update `config/cron-jobs.json` to document system cron (reference only, not config)
3. Add log rotation to pipeline scripts

**Post-verification docs updates** (after Phase E confirms everything works):
- Update CLAUDE.md: replace `openclaw cron add` instructions with system cron docs
- Update `~/.openclaw/workspace/TOOLS.md`: remove OpenClaw cron references

### Phase E: Test

1. Rebuild Docker image: `docker-compose build && docker-compose up -d`
2. Verify crond: `docker-compose exec openclaw-research pgrep cron`
3. Verify crontab: `docker-compose exec openclaw-research crontab -l`
4. Test text delivery: `docker-compose exec openclaw-research bash -c 'source /app/.env && source /app/scripts/telegram_send.sh && tg_send_message "Test: system cron delivery working"'`
5. Test file delivery: `docker-compose exec openclaw-research bash -c 'source /app/.env && source /app/scripts/telegram_send.sh && tg_send_file /app/data/daily-feed/2026-03-17.pdf "Test PDF delivery"'`
6. Test audio delivery with a podcast MP3
7. Run full pipeline: `docker-compose exec openclaw-research bash /app/scripts/daily_pipeline.sh --pdf-only`
8. Verify error handling: test with invalid bot token, verify error logged to stderr
9. Verify OpenClaw cron is empty: `docker-compose exec openclaw-research openclaw cron list`
10. Wait for next 11 AM ET cron trigger and confirm automatic execution

## Dependencies & Risks

- **TELEGRAM_BOT_TOKEN** must be extracted from `openclaw.json` and added to `.env` — one-time manual step
- **macOS manual runs** require `TELEGRAM_BOT_TOKEN` in local `.env` (document in CLAUDE.md)
- **Cron daemon reliability** — ISC Cron is battle-tested. Healthcheck includes `pgrep cron` as safety net
- **DST transitions** — `/etc/localtime` symlink + `TZ` env var handles this. Verify during next DST change (Nov 2026)
- **No breaking change to interactive commands** — OpenClaw gateway still handles all Telegram commands

## Sources & References

- **Origin brainstorm:** [docs/brainstorms/2026-03-18-pipeline-orchestration-and-web-publishing-brainstorm.md](docs/brainstorms/2026-03-18-pipeline-orchestration-and-web-publishing-brainstorm.md) — Key decisions: system cron over OpenClaw cron, direct Bot API over openclaw CLI, bash entrypoint over supervisord
- **BUG-006**: Weekly pipeline stuck in processing (same class of cron message issue) — `docs/BUGS.md`
- **BUG-010**: TCC external volume issue that led to Docker deployment — `docs/BUGS.md`
- Telegram Bot API: sendMessage, sendDocument, sendAudio
- Files to modify: `scripts/telegram_send.sh` (new), `scripts/pipeline_alert.sh`, `scripts/nlm_ensure_auth.sh`, `scripts/daily_pipeline.sh`, `scripts/fetch_and_deliver.sh`, `scripts/weekly_pipeline.sh`, `Dockerfile`, `docker-compose.yml`, `.env.example`
