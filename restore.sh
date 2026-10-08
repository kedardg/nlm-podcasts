#!/bin/bash
# ─────────────────────────────────────────────────────────────
# restore.sh — Restore OpenClaw Research Automation from backup
#
# Assumes a fresh machine with:
#   - macOS or Linux
#   - Internet access
#   - The project directory (auto-detected from script location,
#     or set PROJECT_DIR env var to override)
#
# Usage:
#   ./restore.sh /path/to/backup
# ─────────────────────────────────────────────────────────────

set -euo pipefail

if [ -z "${1:-}" ]; then
  echo "Usage: ./restore.sh /path/to/backup"
  echo ""
  echo "The backup directory should contain:"
  echo "  data/           — research data JSON files"
  echo "  openclaw-config/ — OpenClaw configuration"
  echo "  plugin/         — research-router plugin"
  echo "  skills/         — router.py and skill files"
  echo "  chroma_db/      — (optional) RAG vector store"
  exit 1
fi

BACKUP_DIR="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$SCRIPT_DIR}"
OPENCLAW_DIR="$HOME/.openclaw"

echo "=== OpenClaw Research Restore ==="
echo "Backup:   $BACKUP_DIR"
echo "Project:  $PROJECT_DIR"
echo "OpenClaw: $OPENCLAW_DIR"
echo ""

# ── Step 0: Verify backup ──
if [ ! -d "$BACKUP_DIR/data" ]; then
  echo "ERROR: $BACKUP_DIR/data not found. Is this a valid backup?"
  exit 1
fi

# ── Step 1: Install dependencies ──
echo "[1/7] Checking dependencies..."

# Node.js
if ! command -v node &>/dev/null; then
  echo "  Installing Node.js via Homebrew..."
  if command -v brew &>/dev/null; then
    brew install node
  else
    echo "  ERROR: Node.js not found and Homebrew not available."
    echo "  Install Node.js 22+ manually: https://nodejs.org"
    exit 1
  fi
fi
echo "  node $(node --version)"

# npm
if ! command -v npm &>/dev/null; then
  echo "  ERROR: npm not found. Install Node.js properly."
  exit 1
fi

# Python3
if ! command -v python3 &>/dev/null; then
  echo "  ERROR: python3 not found. Install Python 3.10+."
  exit 1
fi
echo "  python3 $(python3 --version 2>&1 | awk '{print $2}')"

# uv
if ! command -v uv &>/dev/null; then
  echo "  Installing uv..."
  if command -v brew &>/dev/null; then
    brew install uv
  else
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
fi
echo "  uv $(uv --version 2>&1 | head -1)"

# ── Step 2: Install OpenClaw ──
echo "[2/7] Installing OpenClaw..."
if ! command -v openclaw &>/dev/null; then
  npm install -g openclaw@latest
  echo "  Installed openclaw $(openclaw --version 2>&1 | head -1)"
else
  echo "  Already installed: $(openclaw --version 2>&1 | head -1)"
fi

# ── Step 3: Restore project files ──
echo "[3/7] Restoring project structure..."
mkdir -p "$PROJECT_DIR/data"
mkdir -p "$PROJECT_DIR/rag"

# Data files
for f in sources.json accounts.json categories.json notebooks.json processing-log.json newsletters.json news-queue.json; do
  if [ -f "$BACKUP_DIR/data/$f" ]; then
    cp "$BACKUP_DIR/data/$f" "$PROJECT_DIR/data/$f"
    echo "  + data/$f"
  fi
done

# Router + scripts
if [ -f "$BACKUP_DIR/skills/router.py" ]; then
  cp "$BACKUP_DIR/skills/router.py" "$PROJECT_DIR/src/router.py"
  echo "  + router.py"
fi
if [ -f "$BACKUP_DIR/skills/newsletter_parser.py" ]; then
  cp "$BACKUP_DIR/skills/newsletter_parser.py" "$PROJECT_DIR/newsletter_parser.py"
  echo "  + newsletter_parser.py"
fi
if [ -f "$BACKUP_DIR/skills/daily_feed_generator.py" ]; then
  cp "$BACKUP_DIR/skills/daily_feed_generator.py" "$PROJECT_DIR/daily_feed_generator.py"
  echo "  + daily_feed_generator.py"
fi
if [ -f "$BACKUP_DIR/skills/data_triage.py" ]; then
  cp "$BACKUP_DIR/skills/data_triage.py" "$PROJECT_DIR/data_triage.py"
  echo "  + data_triage.py"
fi
if [ -d "$BACKUP_DIR/lib" ]; then
  mkdir -p "$PROJECT_DIR/lib"
  cp "$BACKUP_DIR/lib/"*.py "$PROJECT_DIR/lib/" 2>/dev/null
  echo "  + lib/ (utility modules)"
fi
if [ -d "$BACKUP_DIR/scripts" ]; then
  mkdir -p "$PROJECT_DIR/scripts"
  cp "$BACKUP_DIR/scripts/"*.py "$PROJECT_DIR/scripts/" 2>/dev/null
  chmod +x "$PROJECT_DIR/scripts/"*.py
  echo "  + scripts/ (pipeline helper scripts)"
fi
if [ -d "$BACKUP_DIR/templates" ]; then
  mkdir -p "$PROJECT_DIR/templates"
  cp -r "$BACKUP_DIR/templates/"* "$PROJECT_DIR/templates/" 2>/dev/null
  echo "  + templates/"
fi
if [ -d "$BACKUP_DIR/data/daily-feed" ]; then
  mkdir -p "$PROJECT_DIR/data/daily-feed"
  cp "$BACKUP_DIR/data/daily-feed/"* "$PROJECT_DIR/data/daily-feed/" 2>/dev/null
  echo "  + data/daily-feed/"
fi

# RAG store
if [ -d "$BACKUP_DIR/chroma_db" ]; then
  cp -r "$BACKUP_DIR/chroma_db" "$PROJECT_DIR/rag/chroma_db"
  echo "  + rag/chroma_db/"
fi

# ── Step 4: Install Python deps ──
echo "[4/7] Setting up Python virtual environment..."
if [ ! -d "$PROJECT_DIR/.venv" ]; then
  python3 -m venv "$PROJECT_DIR/.venv"
fi
if command -v uv &>/dev/null; then
  uv pip install --python "$PROJECT_DIR/.venv/bin/python3" chromadb openai beautifulsoup4 lxml trafilatura ural weasyprint markdown-it-py pydantic pytest requests websocket-client feedparser 2>/dev/null
else
  "$PROJECT_DIR/.venv/bin/python3" -m pip install -q chromadb openai beautifulsoup4 lxml trafilatura ural weasyprint markdown-it-py pydantic pytest requests websocket-client feedparser 2>/dev/null
fi
echo "  Installed all Python dependencies in .venv"

# Pango (required by WeasyPrint for PDF generation)
if ! brew list pango &>/dev/null 2>&1; then
  echo "  Installing pango (required by WeasyPrint)..."
  if command -v brew &>/dev/null; then
    brew install pango 2>/dev/null
  else
    echo "  WARNING: pango not installed. Install manually: brew install pango"
  fi
fi

# NotebookLM CLI
if ! command -v nlm &>/dev/null; then
  echo "  Installing notebooklm-mcp-cli..."
  uv tool install notebooklm-mcp-cli 2>/dev/null || echo "  (nlm install skipped — may need manual setup)"
fi

# ── Step 5: Restore OpenClaw config ──
echo "[5/7] Restoring OpenClaw configuration..."
mkdir -p "$OPENCLAW_DIR"
mkdir -p "$OPENCLAW_DIR/workspace"
mkdir -p "$OPENCLAW_DIR/extensions"
mkdir -p "$OPENCLAW_DIR/skills"
mkdir -p "$OPENCLAW_DIR/agents/main/sessions"

# Config
if [ -f "$BACKUP_DIR/openclaw-config/openclaw.json" ]; then
  if [ -f "$OPENCLAW_DIR/openclaw.json" ]; then
    echo "  WARNING: openclaw.json already exists. Backing up to openclaw.json.bak"
    cp "$OPENCLAW_DIR/openclaw.json" "$OPENCLAW_DIR/openclaw.json.bak"
  fi
  cp "$BACKUP_DIR/openclaw-config/openclaw.json" "$OPENCLAW_DIR/openclaw.json"
  echo "  + openclaw.json (REVIEW THIS — contains bot token and API keys)"
fi

# Workspace files
if [ -d "$BACKUP_DIR/openclaw-config/workspace" ]; then
  cp "$BACKUP_DIR/openclaw-config/workspace/"*.md "$OPENCLAW_DIR/workspace/" 2>/dev/null || true
  echo "  + workspace/*.md"
fi

# Ensure RESEARCH.md symlink points to CLAUDE.md
ln -sf "$PROJECT_DIR/CLAUDE.md" "$OPENCLAW_DIR/workspace/RESEARCH.md"
echo "  + workspace/RESEARCH.md -> CLAUDE.md"

# Empty sessions
echo '{}' > "$OPENCLAW_DIR/agents/main/sessions/sessions.json"
echo "  + fresh sessions.json"

# ── Step 6: Restore plugin ──
echo "[6/7] Restoring research-router plugin..."
if [ -d "$BACKUP_DIR/plugin" ]; then
  mkdir -p "$OPENCLAW_DIR/extensions/research-router"
  cp "$BACKUP_DIR/plugin/"* "$OPENCLAW_DIR/extensions/research-router/"
  echo "  + extensions/research-router/"
fi

# Gateway health plugin
if [ -d "$BACKUP_DIR/openclaw-extensions/gateway-health" ]; then
  mkdir -p "$OPENCLAW_DIR/extensions/gateway-health"
  cp "$BACKUP_DIR/openclaw-extensions/gateway-health/"* "$OPENCLAW_DIR/extensions/gateway-health/"
  echo "  + extensions/gateway-health/"
fi

# Skill (backup for LLM fallback)
if [ -d "$BACKUP_DIR/skills/research-router-skill" ]; then
  mkdir -p "$OPENCLAW_DIR/skills/research-router"
  cp -r "$BACKUP_DIR/skills/research-router-skill/"* "$OPENCLAW_DIR/skills/research-router/"
  echo "  + skills/research-router/"
fi

# Recreate skill symlinks
echo "  Creating skill symlinks..."
for skill in research-ingest x-monitor content-processor notebooklm-orchestrator digest-deliver chat-rag newsletter-monitor news-digest daily-digest; do
  if [ -d "$PROJECT_DIR/skills/$skill" ]; then
    ln -sf "$PROJECT_DIR/skills/$skill" "$OPENCLAW_DIR/skills/$skill"
    echo "  + skills/$skill -> project"
  fi
done

# ── Step 7: Verify ──
echo "[7/7] Verifying..."

echo ""
echo "=== Restore Summary ==="
echo ""
echo "Data files:"
ls -la "$PROJECT_DIR/data/"*.json 2>/dev/null | awk '{print "  " $NF " (" $5 " bytes)"}'

echo ""
echo "Plugin:"
openclaw plugins list 2>&1 | grep -i "research" || echo "  (check manually with: openclaw plugins list)"

echo ""
echo "=== Next Steps ==="
echo ""
echo "1. Review and update API keys in ~/.openclaw/openclaw.json:"
echo "   - Telegram bot token (channels.telegram.botToken)"
echo "   - OpenAI API key (set OPENAI_API_KEY env var)"
echo ""
echo "2. Start the gateway:"
echo "   openclaw gateway --port 18789 --bind loopback"
echo ""
echo "3. If Telegram pairing is needed:"
echo "   - Open Telegram, send any message to the bot"
echo "   - Get the pairing code, then run:"
echo "   openclaw pairing approve telegram <code>"
echo ""
echo "4. (Optional) Install as daemon for auto-start:"
echo "   openclaw gateway install"
echo ""
echo "5. (Optional) Set up email credentials for newsletter monitoring:"
echo "   mkdir -p ~/.config/openclaw-research"
echo "   cp $PROJECT_DIR/email-credentials.json.template ~/.config/openclaw-research/email-credentials.json"
echo "   # Edit the file with your Gmail address and App Password"
echo ""
echo "6. (Optional) Set up the weekly cron job:"
echo "   openclaw cron add --name weekly-research-digest --cron '0 9 * * 6' \\"
echo "     --tz America/New_York --session isolated \\"
echo "     --message 'Run the weekly research digest pipeline.' \\"
echo "     --announce --channel telegram"
echo ""
echo "Done!"
