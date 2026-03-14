---
title: "Fix cron pipeline 'Operation not permitted' via Docker deployment"
type: fix
status: completed
date: 2026-03-14
---

# Fix: Cron Pipeline "Operation not permitted" — Dockerize the System

## Overview

The daily and weekly cron pipelines fail with `Operation not permitted` when the OpenClaw daemon tries to execute scripts on the external volume `/Volumes/ext`. Rather than fighting macOS TCC permissions, dockerize the entire system — it's already standalone with an existing Dockerfile and docker-compose.yml.

**Error evidence:**
```
# Mar 12
bash: /Volumes/ext/projects/openclaw-research/config/env.sh: Operation not permitted

# Mar 13
zsh:source:1: operation not permitted: /Volumes/ext/projects/openclaw-research/config/env.sh
```

## Root Cause

macOS TCC (Transparency, Consent, and Control) blocks the OpenClaw daemon process from accessing the external APFS volume `/Volumes/ext` (mounted with `noowners,nosuid,nodev`). The daemon lacks Full Disk Access. This is an OS-level privacy gate, not a Unix permissions issue.

## Proposed Solution: Run in Docker

The project already has a working `Dockerfile` and `docker-compose.yml`. The approach is to close the remaining gaps so the entire system — daemon, cron, pipelines — runs inside Docker where there are no TCC restrictions.

### Gap Analysis: What Exists vs What's Missing

**Already working:**
- Multi-stage Dockerfile (builder + runtime)
- System deps: Chromium, ffmpeg, pandoc, pango
- Python venv with core packages
- OpenClaw + nlm CLI installed
- `PROJECT_ROOT=/app`, `CHROME_PATH=/usr/bin/chromium`
- Data + ChromaDB bind mounts
- OpenClaw config mounted read-only

**Gaps to close:**

| Gap | Issue | Fix |
|-----|-------|-----|
| Missing Python packages | Dockerfile installs 7 packages; `requirements.txt` has 14 (missing: `feedparser`, `requests`, `websocket-client`, `pydantic`, `playwright`, `ural`, `pytest`) | Use `requirements.txt` in Dockerfile |
| `mlx-lm` is macOS-only | Apple Silicon MLX won't work in Linux container | Exclude from Docker build; classifier falls back to domain rules + title heuristics |
| No workspace mount | TOOLS.md, IDENTITY.md, SOUL.md etc. not in container | Mount `~/.openclaw/workspace/` |
| `RESEARCH.md` symlink | Points to `/Volumes/ext/.../CLAUDE.md` — broken in container | Copy CLAUDE.md into workspace instead of symlink |
| No extensions mount | `gateway-health` plugin not available in container | Mount `~/.openclaw/extensions/` |
| No cron configuration | Cron jobs reference `/Volumes/ext/...` paths | Update to `/app/...` paths; add to Docker entrypoint or use OpenClaw cron |
| NLM auth bootstrapping | `nlm login` needs interactive browser session | Mount `~/.notebooklm-mcp/` as named volume; run `docker compose exec ... nlm login` once |
| Playwright not installed | `playwright` package needs browser binaries | Add `playwright install chromium` to Dockerfile |
| No healthcheck | No Docker healthcheck defined | Add healthcheck using OpenClaw status |
| Restart policy | No restart policy in compose | Add `restart: unless-stopped` |

### Part 1: Update Dockerfile

```dockerfile
# In builder stage — use requirements.txt (minus mlx-lm)
COPY requirements.txt /tmp/requirements.txt
RUN grep -v 'mlx-lm' /tmp/requirements.txt > /tmp/requirements-docker.txt && \
    uv pip install --python /app/.venv/bin/python3 -r /tmp/requirements-docker.txt

# In runtime stage — install Playwright browsers
RUN /app/.venv/bin/python3 -m playwright install --with-deps chromium
```

**Files:** `Dockerfile`, create `requirements-docker.txt` (or filter inline)

### Part 2: Update docker-compose.yml

```yaml
services:
  openclaw-research:
    build: .
    command: openclaw daemon start --foreground
    restart: unless-stopped
    env_file: .env
    environment:
      - PROJECT_ROOT=/app
      - CHROME_PATH=/usr/bin/chromium
    volumes:
      # Persistent data
      - ./data:/app/data
      - ./rag/chroma_db:/app/rag/chroma_db
      # OpenClaw runtime
      - openclaw-home:/root/.openclaw
      - nlm-auth:/root/.notebooklm-mcp
      # Read-only config from host
      - ~/.openclaw/openclaw.json:/root/.openclaw/openclaw.json:ro
      - ~/.config/openclaw-research:/root/.config/openclaw-research:ro
      # Workspace files (TOOLS.md, IDENTITY.md, etc.)
      - ~/.openclaw/workspace/TOOLS.md:/root/.openclaw/workspace/TOOLS.md:ro
      - ~/.openclaw/workspace/IDENTITY.md:/root/.openclaw/workspace/IDENTITY.md:ro
      - ~/.openclaw/workspace/SOUL.md:/root/.openclaw/workspace/SOUL.md:ro
      - ~/.openclaw/workspace/AGENTS.md:/root/.openclaw/workspace/AGENTS.md:ro
      - ~/.openclaw/workspace/USER.md:/root/.openclaw/workspace/USER.md:ro
      - ~/.openclaw/workspace/SHIELD.md:/root/.openclaw/workspace/SHIELD.md:ro
      - ~/.openclaw/workspace/HEARTBEAT.md:/root/.openclaw/workspace/HEARTBEAT.md:ro
      - ~/.openclaw/workspace/BOOTSTRAP.md:/root/.openclaw/workspace/BOOTSTRAP.md:ro
      # RESEARCH.md — copy of CLAUDE.md, not symlink (see Part 3)
      # Extensions (gateway-health plugin)
      - ~/.openclaw/extensions:/root/.openclaw/extensions:ro
    healthcheck:
      test: ["CMD", "openclaw", "daemon", "status"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 30s

volumes:
  openclaw-home:
  nlm-auth:
```

**File:** `docker-compose.yml`

### Part 3: Fix the RESEARCH.md symlink

The workspace `RESEARCH.md` is a symlink to `/Volumes/ext/.../CLAUDE.md`. In Docker, paths are `/app/...`. Two options:

**Option A (recommended):** Replace the symlink with a copy and add a sync step:
```bash
# One-time: replace symlink with copy
rm ~/.openclaw/workspace/RESEARCH.md
cp /Volumes/ext/projects/openclaw-research/CLAUDE.md ~/.openclaw/workspace/RESEARCH.md
```

Add a note to the docs that RESEARCH.md should be updated when CLAUDE.md changes (or add a pre-build hook).

**Option B:** Mount CLAUDE.md directly:
```yaml
- ./CLAUDE.md:/root/.openclaw/workspace/RESEARCH.md:ro
```
This is cleaner but couples the compose file to the workspace layout.

### Part 4: Update cron messages

Cron messages must reference `/app/...` paths since they run inside the container:

**Daily:**
```
"message": "Run this exact command: bash /app/scripts/daily_pipeline.sh"
```

**Weekly:**
```
"message": "Run this exact command: bash /app/scripts/weekly_pipeline.sh"
```

Update `config/cron-jobs.json` and register via `docker compose exec openclaw-research openclaw cron add ...`.

**File:** `config/cron-jobs.json`

### Part 5: Update TOOLS.md

Change `PROJECT_ROOT` reference from `/Volumes/ext/projects/openclaw-research` to `/app`:

```bash
export PROJECT_ROOT=/app
```

The rest of TOOLS.md uses `$PROJECT_ROOT` already, so this single change cascades everywhere.

**File:** `~/.openclaw/workspace/TOOLS.md`

### Part 6: NLM auth bootstrap

After first `docker compose up`:
```bash
# Interactive NLM login inside the container
docker compose exec openclaw-research nlm login

# Verify
docker compose exec openclaw-research nlm doctor --verbose
```

The `nlm-auth` named volume persists auth tokens across container restarts.

### Part 7: Update docs + BUGS.md

- Add bug entry for the TCC/external volume issue
- Update CLAUDE.md Docker section with the new volume mounts
- Document the `RESEARCH.md` symlink → copy change

## Acceptance Criteria

- [x] `Dockerfile` installs all packages from `requirements.txt` (minus `mlx-lm`)
- [x] `Dockerfile` installs Playwright + Chromium browser binaries
- [x] `docker-compose.yml` mounts workspace files, extensions, and `openclaw.json`
- [x] `docker-compose.yml` has `restart: unless-stopped` and healthcheck
- [x] `RESEARCH.md` symlink replaced with copy (or direct mount in compose)
- [x] `config/cron-jobs.json` updated with `/app/...` paths
- [ ] `TOOLS.md` updated with `PROJECT_ROOT=/app`
- [x] `docker compose build` succeeds
- [ ] `docker compose up -d` starts the daemon
- [ ] `docker compose exec ... nlm login` completes (manual, one-time)
- [ ] Daily pipeline runs successfully inside Docker
- [x] `docs/BUGS.md` updated with TCC bug entry

## Files to Modify

| File | Change |
|------|--------|
| `Dockerfile` | Use `requirements.txt`, add Playwright install, filter `mlx-lm` |
| `docker-compose.yml` | Add workspace, extensions mounts; healthcheck; restart policy |
| `config/cron-jobs.json` | Update paths from `/Volumes/ext/...` to `/app/...`; add daily entry |
| `config/env.sh` | `chmod +x` (defensive, still useful for local dev) |
| `~/.openclaw/workspace/TOOLS.md` | Change `PROJECT_ROOT` to `/app` |
| `~/.openclaw/workspace/RESEARCH.md` | Replace symlink with copy of CLAUDE.md |
| `docs/BUGS.md` | New entry: BUG-00X external volume TCC issue |
| `CLAUDE.md` | Update Docker section with new mounts |

## Risk Analysis

- **Low risk**: Docker setup already exists and works; this is incremental
- **`mlx-lm` exclusion**: Classifier falls back to domain rules + title heuristics (55%+ coverage per CLAUDE.md). No LLM classification in Docker — acceptable tradeoff since Qwen2.5-7B requires Apple Silicon
- **Workspace mount complexity**: Many individual file mounts are brittle; consider mounting the entire workspace dir instead (but it accumulates delivered artifacts — 70MB+ of PDFs/podcasts)
- **NLM auth**: Named volume `nlm-auth` persists cookies, but Google rotates them. The `gateway-health` plugin's auth recovery should handle this inside the container (Chromium is available)
- **Data continuity**: `./data` and `./rag/chroma_db` are bind-mounted, so existing data carries over seamlessly
