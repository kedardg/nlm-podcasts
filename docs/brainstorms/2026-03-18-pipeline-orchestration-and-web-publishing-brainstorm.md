# Pipeline Orchestration & Web Publishing Brainstorm

**Date**: 2026-03-18
**Status**: Complete

## What We're Building

Decouple the daily/weekly pipeline from OpenClaw's LLM-based cron (which caused missed deliveries March 12-18) and establish a reliable, self-contained orchestration layer. Simultaneously, add an Astro-based web publishing step that deploys daily reports to GitHub Pages.

## Why This Approach

The pipeline is a deterministic bash script. Routing it through gpt-5-mini to interpret a message and call a bash tool is an unnecessary failure point — and it failed. The pipeline already handles its own Telegram delivery, error alerts, and logging. OpenClaw's value is in interactive Telegram commands (chat, generate, track), not in scheduling bash scripts.

## Target Architecture

```
Docker Container
├── OpenClaw Gateway (always running)
│   ├── Telegram bot polling (interactive commands only)
│   ├── router.py → chat, generate, track, list, status
│   └── chat-rag (LLM-powered queries)
│
├── System Cron (crontab — no LLM involved)
│   ├── 0 11 * * * (ET) → daily_pipeline.sh
│   │   └── Steps 0-10 + Step 11: Astro build + deploy
│   └── 0 9 * * 6 (ET) → weekly_pipeline.sh
│
├── Telegram Delivery (direct Bot API)
│   ├── Text: curl sendMessage
│   ├── Files: curl sendDocument
│   └── Alerts: curl sendMessage (pipeline_alert.sh)
│
└── Web Publishing (pipeline step 11)
    ├── Copy report MD to Astro content dir (separate repo: openclaw-digest)
    ├── astro build → static HTML
    └── git push to openclaw-digest → GitHub Pages auto-deploys
```

## Key Decisions

### 1. Scheduling → System cron (drop OpenClaw cron)
- **Decision**: Use `crontab` inside Docker container
- **Why**: Direct bash execution is bulletproof. OpenClaw cron adds LLM interpretation risk with zero benefit — the pipeline already handles announcements, delivery, error alerts, and logging
- **Implementation**: Docker entrypoint starts both `crond` and `openclaw gateway run`. Crontab installed via Dockerfile

### 2. Telegram delivery → Direct Bot API
- **Decision**: Replace `openclaw message send` in pipeline scripts with direct `curl` to Telegram Bot API
- **Why**: Makes the pipeline fully self-contained. No dependency on OpenClaw gateway being responsive. Simpler debugging
- **Implementation**: Helper function in `config/env.sh` or a small `scripts/telegram_send.sh` that wraps `curl -F` calls
- **Keep OpenClaw for**: Interactive Telegram commands (router.py, chat-rag). The gateway still polls for incoming messages

### 3. Web publishing → Astro + MDX in separate repo, built as pipeline step 11
- **Decision**: Separate repo (`openclaw-digest` on GitHub), Astro with content collections consuming report `.md` files
- **Why**: Clean separation between private pipeline code and public-facing site. GitHub Pages auto-deploys on push
- **Implementation**: Pipeline step 11 copies today's report to a local clone of `openclaw-digest`, runs `astro build`, commits and pushes
- **Node.js already in Docker image** (OpenClaw requires it), so Astro deps add minimal overhead

### 4. Docker entrypoint → supervisord or simple bash
- **Decision**: Bash entrypoint that starts crond + OpenClaw gateway
- **Why**: Simplest approach. Supervisord is overkill for two processes
- **Implementation**:
  ```bash
  #!/bin/bash
  # Install crontab
  crontab /app/config/crontab
  # Start cron daemon in background
  crond
  # Run OpenClaw gateway in foreground (Docker tracks this PID)
  exec openclaw gateway run
  ```

## Resolved Questions

1. **Astro build location** → Inside Docker as pipeline step 11. Node.js already present. All-in-one is simpler
2. **Telegram delivery** → Direct Bot API. Pipeline becomes independent of OpenClaw runtime
3. **Announce feature** → Not needed. Pipeline already sends its own summary messages
4. **crond in Docker** → Standard approach. Simple bash entrypoint starts crond + gateway
5. **Astro site repo** → Separate repo (`openclaw-digest`). Keeps public site isolated from private pipeline code
6. **Reading pattern** → Skim editorial synthesis, drill into articles. Needs section nav + collapsible articles
7. **Audience** → Start private, designed to go public later. Professional design from the start
8. **Framework** → Astro + MDX content collections. Report markdown is the source of truth

## Implementation Order

1. **Phase 1: Reliable scheduling** — System cron in Docker, direct Telegram Bot API, remove OpenClaw cron dependency
2. **Phase 2: Astro site** — Set up `openclaw-digest` repo, Astro project, MDX content collections, responsive design
3. **Phase 3: Pipeline integration** — Add step 11 to `daily_pipeline.sh` for Astro build + deploy
4. **Phase 4: Polish** — Dark mode, section nav, collapsible articles, archive index, mobile optimization

## What NOT to Change

- OpenClaw gateway stays for interactive Telegram commands
- Pipeline scripts stay as bash (not rewritten)
- NLM integration stays as-is
- Report markdown format stays (Astro consumes it via MDX)
