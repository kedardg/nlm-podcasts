#!/bin/bash
# ─────────────────────────────────────────────────────────────
# backup.sh — Backup all OpenClaw Research Automation state
#
# Usage:
#   ./backup.sh                    # Creates timestamped backup
#   ./backup.sh /path/to/output    # Backup to specific location
# ─────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="${1:-$SCRIPT_DIR/backups/$TIMESTAMP}"

echo "=== OpenClaw Research Backup ==="
echo "Timestamp: $TIMESTAMP"
echo "Output:    $BACKUP_DIR"
echo ""

mkdir -p "$BACKUP_DIR"

# ── 1. Research data files ──
echo "[1/5] Backing up research data..."
mkdir -p "$BACKUP_DIR/data"
for f in sources.json accounts.json categories.json notebooks.json processing-log.json newsletters.json news-queue.json; do
  src="$SCRIPT_DIR/data/$f"
  if [ -f "$src" ]; then
    cp "$src" "$BACKUP_DIR/data/$f"
    echo "  + data/$f"
  fi
done

# ── 2. RAG vector store ──
echo "[2/5] Backing up RAG store..."
if [ -d "$SCRIPT_DIR/rag/chroma_db" ]; then
  cp -r "$SCRIPT_DIR/rag/chroma_db" "$BACKUP_DIR/chroma_db"
  echo "  + chroma_db/"
else
  echo "  (no chroma_db yet — skipping)"
fi

# ── 3. OpenClaw config ──
echo "[3/5] Backing up OpenClaw config..."
mkdir -p "$BACKUP_DIR/openclaw-config"
for f in openclaw.json; do
  src="$HOME/.openclaw/$f"
  if [ -f "$src" ]; then
    cp "$src" "$BACKUP_DIR/openclaw-config/$f"
    echo "  + openclaw-config/$f"
  fi
done

# Workspace files (TOOLS.md is critical for pipeline commands)
if [ -d "$HOME/.openclaw/workspace" ]; then
  mkdir -p "$BACKUP_DIR/openclaw-config/workspace"
  cp "$HOME/.openclaw/workspace/"*.md "$BACKUP_DIR/openclaw-config/workspace/" 2>/dev/null || true
  echo "  + openclaw-config/workspace/ (includes TOOLS.md)"
fi

# ── 4. Plugin ──
echo "[4/5] Backing up research-router plugin..."
if [ -d "$HOME/.openclaw/extensions/research-router" ]; then
  mkdir -p "$BACKUP_DIR/plugin"
  cp -r "$HOME/.openclaw/extensions/research-router/"* "$BACKUP_DIR/plugin/"
  echo "  + plugin/"
fi

# ── 5. Skills + scripts + templates ──
echo "[5/6] Backing up skills, scripts, and templates..."
mkdir -p "$BACKUP_DIR/skills"
cp $SCRIPT_DIR/router.py "$BACKUP_DIR/skills/"
echo "  + router.py"
if [ -f "$SCRIPT_DIR/newsletter_parser.py" ]; then
  cp $SCRIPT_DIR/newsletter_parser.py "$BACKUP_DIR/skills/"
  echo "  + newsletter_parser.py"
fi
if [ -f "$SCRIPT_DIR/daily_feed_generator.py" ]; then
  cp $SCRIPT_DIR/daily_feed_generator.py "$BACKUP_DIR/skills/"
  echo "  + daily_feed_generator.py"
fi
if [ -f "$SCRIPT_DIR/data_triage.py" ]; then
  cp $SCRIPT_DIR/data_triage.py "$BACKUP_DIR/skills/"
  echo "  + data_triage.py"
fi
if [ -f "$SCRIPT_DIR/blog_monitor.py" ]; then
  cp "$SCRIPT_DIR/blog_monitor.py" "$BACKUP_DIR/skills/"
  echo "  + blog_monitor.py"
fi
if [ -d "$SCRIPT_DIR/lib" ]; then
  mkdir -p "$BACKUP_DIR/lib"
  cp "$SCRIPT_DIR/lib/"*.py "$BACKUP_DIR/lib/" 2>/dev/null
  echo "  + lib/ (utility modules)"
fi
if [ -d "$SCRIPT_DIR/tests" ]; then
  mkdir -p "$BACKUP_DIR/tests"
  cp -r $SCRIPT_DIR/tests/*.py "$BACKUP_DIR/tests/" 2>/dev/null
  cp -r $SCRIPT_DIR/tests/fixtures "$BACKUP_DIR/tests/" 2>/dev/null
  echo "  + tests/"
fi
if [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
  cp $SCRIPT_DIR/pyproject.toml "$BACKUP_DIR/skills/"
  echo "  + pyproject.toml"
fi
if [ -d "$SCRIPT_DIR/scripts" ]; then
  mkdir -p "$BACKUP_DIR/scripts"
  cp $SCRIPT_DIR/scripts/*.py "$BACKUP_DIR/scripts/" 2>/dev/null
  echo "  + scripts/ (pipeline helper scripts)"
fi
if [ -d "$SCRIPT_DIR/templates" ]; then
  mkdir -p "$BACKUP_DIR/templates"
  cp -r $SCRIPT_DIR/templates/* "$BACKUP_DIR/templates/" 2>/dev/null
  echo "  + templates/"
fi
if [ -d "$SCRIPT_DIR/data/daily-feed" ]; then
  mkdir -p "$BACKUP_DIR/data/daily-feed"
  cp $SCRIPT_DIR/data/daily-feed/*.pdf "$BACKUP_DIR/data/daily-feed/" 2>/dev/null
  cp $SCRIPT_DIR/data/daily-feed/*.md "$BACKUP_DIR/data/daily-feed/" 2>/dev/null
  echo "  + data/daily-feed/"
fi
if [ -d "$HOME/.openclaw/skills/research-router" ]; then
  cp -r "$HOME/.openclaw/skills/research-router" "$BACKUP_DIR/skills/research-router-skill"
fi
if [ -d "$HOME/.openclaw/extensions/gateway-health" ]; then
  mkdir -p "$BACKUP_DIR/openclaw-extensions"
  cp -r "$HOME/.openclaw/extensions/gateway-health" "$BACKUP_DIR/openclaw-extensions/"
  echo "  + gateway-health plugin"
fi

# ── 6. Email credentials (redacted) ──
echo "[6/6] Backing up email config (template only — no passwords)..."
if [ -f "$HOME/.config/openclaw-research/email-credentials.json" ]; then
  # Store a redacted version (don't backup actual passwords)
  mkdir -p "$BACKUP_DIR/email-config"
  python3 -c "
import json
with open('$HOME/.config/openclaw-research/email-credentials.json') as f:
    c = json.load(f)
c['app_password'] = '*** REDACTED ***'
with open('$BACKUP_DIR/email-config/email-credentials.json.redacted', 'w') as f:
    json.dump(c, f, indent=2)
" 2>/dev/null && echo "  + email-config/email-credentials.json.redacted" || echo "  (skipped — parse error)"
fi

# ── Summary ──
TOTAL_SIZE=$(du -sh "$BACKUP_DIR" | cut -f1)
echo ""
echo "=== Backup complete ==="
echo "Location: $BACKUP_DIR"
echo "Size:     $TOTAL_SIZE"
echo ""
echo "To restore on a new machine, run:"
echo "  ./restore.sh $BACKUP_DIR"
