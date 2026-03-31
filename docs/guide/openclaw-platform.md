# OpenClaw Platform

## What is OpenClaw?

OpenClaw is the runtime platform that powers this research automation system. It provides a WebSocket gateway, Telegram bot integration, cron scheduling, multi-agent routing, session management, and an LLM inference layer. Think of it as the operating system — our Python scripts and SKILL.md files are the applications running on top.

## Architecture Overview

```
                         OpenClaw Gateway (port 18789)
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
              Telegram Bot    Cron Jobs     Web Dashboard
                    │              │              │
                    └──────────────┼──────────────┘
                                   │
                            Agent Runtime
                         (session + context)
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
              Workspace Files  LLM Provider   MCP Servers
            (TOOLS.md, etc.)  (OpenAI GPT)  (notebooklm-mcp)
```

## Key Components We Use

### Gateway & Daemon

The gateway is a background daemon process (launchd on macOS) that:
- Listens on port 18789 (loopback only)
- Bridges Telegram messages ↔ LLM agent turns
- Manages sessions per chat/DM
- Executes cron jobs in isolated sessions
- Runs the gateway-health plugin for automatic memory monitoring and NLM auth rotation

```bash
# Start/stop
openclaw gateway start
openclaw gateway stop
openclaw gateway --port 18789 --bind loopback  # manual start with options

# Check health
openclaw health --json --verbose
openclaw daemon status

# Force restart via launchd
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
```

**Gateway Health Plugin** (`~/.openclaw/extensions/gateway-health/`): Probes RSS every 10 min, auto-restarts on memory leaks (hard limit 800MB or growth >200MB from baseline), rotates NLM auth on startup and before cron sessions. Config in `openclaw.plugin.json` alongside `index.ts`.

### Workspace Files

The LLM agent reads these files from `~/.openclaw/workspace/` at session start. They are injected into the system prompt:

| File | Purpose | Our Usage |
|------|---------|-----------|
| `TOOLS.md` | Pipeline instructions | The brain of our system — tells the LLM how to execute `generate`, `process`, and all pipeline commands |
| `RESEARCH.md` | Project context | Symlink to `CLAUDE.md` — gives the LLM understanding of our data model and architecture |
| `IDENTITY.md` | Agent personality | Configurable persona |
| `SOUL.md` | Behavior guidelines | Tone, boundaries, response style |
| `AGENTS.md` | Multi-agent config | Agent directives |
| `HEARTBEAT.md` | Heartbeat behavior | Periodic pulse behavior (currently default/unused) |
| `BOOTSTRAP.md` | First-run behavior | Initial setup instructions |

**TOOLS.md is the most critical file.** When pipeline commands fail, check TOOLS.md first — it's the single source of truth for how the LLM agent executes `nlm` commands, downloads artifacts, and delivers files via Telegram.

### Session Management

Each Telegram conversation gets its own session. Sessions accumulate context (message history, tool results) until they're reset.

- Sessions persist in `~/.openclaw/agents/main/sessions/sessions.json`
- Each session also has a `.jsonl` transcript file in the sessions directory
- Sessions auto-compact when context approaches the token limit
- Manual reset: `/reset` command via Telegram, or:

```bash
# Nuclear reset — clears ALL sessions (use when stale context causes errors)
echo '{}' > ~/.openclaw/agents/main/sessions/sessions.json
# Also remove stale .jsonl transcripts
rm ~/.openclaw/agents/main/sessions/*.jsonl
```

**Common issue**: If the LLM gave wrong answers previously, the session caches those bad responses and the LLM repeats them in subsequent turns. Fix by clearing sessions and restarting the gateway.

### Cron System

Two scheduled jobs drive the daily and weekly pipelines:

```bash
# Daily Tech Feed — 6 PM ET every day
openclaw cron add \
  --name "daily-tech-feed" \
  --cron "0 18 * * *" \
  --tz "America/New_York" \
  --session isolated \
  --message "Run the daily tech feed: ..." \
  --announce --channel telegram

# Weekly Research Digest — Saturday 9 AM ET
openclaw cron add \
  --name "weekly-research-digest" \
  --cron "0 9 * * 6" \
  --tz "America/New_York" \
  --session isolated \
  --message "Run the weekly research digest: ..." \
  --announce --channel telegram
```

Key details:
- `--session isolated` creates a fresh agent turn (no prior context pollution)
- `--announce` delivers results to the Telegram channel
- Jobs persist across reboots in `~/.openclaw/cron/jobs.json`
- Manage: `openclaw cron list`, `openclaw cron enable/disable <name>`, `openclaw cron remove <name>`
- Manual trigger: `openclaw cron run <name>`

### Model Provider

We use OpenAI GPT-4o as the primary LLM. Configuration lives in `~/.openclaw/openclaw.json`:

```json
{
  "agents": {
    "defaults": {
      "model": {
        "primary": "openai/gpt-4o"
      }
    }
  }
}
```

The `/model` Telegram command switches between `gpt-4o-mini` (fast/cheap), `gpt-4o` (capable), and `gpt-5.1` (most capable). The change requires a gateway restart.

## Features We Could Leverage More

### Heartbeat / Thinking Clock

A periodic agent turn (every 30-60 min) where the agent can proactively check for new items and alert via Telegram. Currently not configured for our use case.

### Sub-Agents (`sessions_spawn`)

Could parallelize independent steps in the weekly pipeline (e.g., newsletter-monitor + x-monitor running simultaneously). Up to 8 concurrent sub-agents.

### Webhooks

External services can POST to `http://localhost:18789/hooks/<name>` to trigger the agent. Could enable event-driven newsletter processing instead of cron-based polling.

### Model Fallback Chains

```bash
openclaw models fallbacks add openai/gpt-4o anthropic/claude-sonnet-4-5-20250929
```

Provides resilience if OpenAI has an outage during the Saturday cron job.

### Built-in Web Search

The agent has access to `web_search` tool (Brave, Tavily, Perplexity, Exa) for real-time web discovery. Could augment research discovery beyond X scraping + newsletters.

### Control UI Dashboard

Web-based monitoring at `http://localhost:18789/` — real-time session visibility, cron status, exec approvals.

## CLI Reference (Most Used)

```bash
# Daemon
openclaw onboard --install-daemon
openclaw daemon status
openclaw daemon restart

# Gateway
openclaw gateway start|stop|restart
openclaw health --json

# Sessions
openclaw sessions --json

# Cron
openclaw cron list
openclaw cron add --name <name> --cron "<expr>" --tz "<tz>" --session isolated --message "<msg>" --announce --channel telegram
openclaw cron enable|disable <name>
openclaw cron run <name>
openclaw cron runs <name>  # view run history

# Channels
openclaw channels status --probe
openclaw channels add --token <telegram-bot-token>

# Messages
openclaw message send --channel telegram --target <chat_id> --media <file> --message "<caption>"

# Models
openclaw models list
openclaw models set openai/gpt-4o

# Diagnostics
openclaw doctor --deep --yes --fix
openclaw logs --follow --json
```
