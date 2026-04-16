#!/bin/bash
# Bootstrap the OpenClaw Research Automation System
set -e

echo "=== OpenClaw Research Automation System Setup ==="
echo ""

# ── OS Detection ──────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
    Darwin) OS_TYPE="macos" ;;
    Linux)  OS_TYPE="linux" ;;
    *)      echo "Warning: Unsupported OS '$OS'. Proceeding with best-effort." ; OS_TYPE="other" ;;
esac
echo "[info] Detected OS: $OS_TYPE"
echo ""

# ── Step 0: Prerequisites ──────────────────────────────────────────

# Node.js / npm
if ! command -v npm >/dev/null 2>&1; then
    echo "[prereq] npm not found. Installing Node.js..."
    if [ "$OS_TYPE" = "linux" ]; then
        if command -v apt-get >/dev/null 2>&1; then
            curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
            sudo apt-get install -y nodejs
        else
            echo "Error: apt-get not found. Install Node.js 22+ manually."
            exit 1
        fi
    elif command -v brew >/dev/null 2>&1; then
        brew install node
    else
        echo "Error: npm is not installed and Homebrew is not available."
        echo "Install Node.js from https://nodejs.org/ or install Homebrew first:"
        echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        exit 1
    fi
else
    echo "[prereq] npm — already installed ($(npm --version))"
fi

# Python 3
if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 is required."
    if [ "$OS_TYPE" = "linux" ]; then
        echo "Install via: sudo apt-get install python3 python3-venv"
    else
        echo "Install via: brew install python3"
    fi
    exit 1
else
    echo "[prereq] python3 — already installed ($(python3 --version 2>&1))"
fi

# uv
if ! command -v uv >/dev/null 2>&1; then
    echo "[prereq] uv not found. Installing uv..."
    if [ "$OS_TYPE" = "macos" ] && command -v brew >/dev/null 2>&1; then
        brew install uv
    else
        curl -LsSf https://astral.sh/uv/install.sh | sh
        [ -f "$HOME/.local/bin/env" ] && . "$HOME/.local/bin/env"
    fi
else
    echo "[prereq] uv — already installed ($(uv --version 2>&1))"
fi

# Pandoc
if ! command -v pandoc >/dev/null 2>&1; then
    echo "[prereq] pandoc not found. Installing..."
    if [ "$OS_TYPE" = "linux" ] && command -v apt-get >/dev/null 2>&1; then
        sudo apt-get install -y pandoc
    elif command -v brew >/dev/null 2>&1; then
        brew install pandoc
    else
        echo "Warning: pandoc is not installed (needed for report format conversion)."
    fi
else
    echo "[prereq] pandoc — already installed ($(pandoc --version | head -1))"
fi

# FFmpeg
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "[prereq] ffmpeg not found. Installing..."
    if [ "$OS_TYPE" = "linux" ] && command -v apt-get >/dev/null 2>&1; then
        sudo apt-get install -y ffmpeg
    elif command -v brew >/dev/null 2>&1; then
        brew install ffmpeg
    else
        echo "Warning: ffmpeg is not installed (needed for podcast compression)."
    fi
else
    echo "[prereq] ffmpeg — already installed ($(ffmpeg -version 2>&1 | head -1))"
fi

# Pango (WeasyPrint dependency)
if [ "$OS_TYPE" = "linux" ]; then
    if ! dpkg -s libpango-1.0-0 >/dev/null 2>&1; then
        echo "[prereq] Installing pango libraries for WeasyPrint..."
        sudo apt-get install -y libpango-1.0-0 libpangocairo-1.0-0 libpangoft2-1.0-0
    else
        echo "[prereq] pango — already installed"
    fi
elif [ "$OS_TYPE" = "macos" ]; then
    if ! brew list pango >/dev/null 2>&1; then
        echo "[prereq] Installing pango for WeasyPrint..."
        brew install pango
    else
        echo "[prereq] pango — already installed"
    fi
fi

echo ""

# ── Step 1: OpenClaw ───────────────────────────────────────────────

echo "Step 1: OpenClaw..."
if command -v openclaw >/dev/null 2>&1; then
    echo "  ✓ Already installed ($(openclaw --version 2>&1 || echo 'unknown version')). Skipping."
else
    echo "  Installing openclaw globally..."
    npm install -g openclaw@latest
fi

echo ""

# ── Step 2: notebooklm-mcp-cli ────────────────────────────────────

echo "Step 2: notebooklm-mcp-cli..."
if command -v nlm >/dev/null 2>&1; then
    echo "  ✓ Already installed ($(nlm --version 2>&1 || echo 'unknown version')). Skipping."
else
    echo "  Installing notebooklm-mcp-cli via uv..."
    uv tool install notebooklm-mcp-cli
fi

echo ""

# ── Step 3: X/Twitter research skill ──────────────────────────────

echo "Step 3: X/Twitter research skill (aisa-twitter-api)..."
# Check if the skill directory exists in common OpenClaw skill locations
SKILL_INSTALLED=false
for dir in "$HOME/.openclaw/skills/aisa-twitter-api" "./skills/aisa-twitter-api"; do
    if [ -d "$dir" ]; then
        SKILL_INSTALLED=true
        break
    fi
done
# Also check if clawhub reports it as installed
if $SKILL_INSTALLED; then
    echo "  ✓ Already installed. Skipping."
else
    echo "  Installing aisa-twitter-api via clawhub..."
    MAX_RETRIES=3
    RETRY_DELAY=10
    for i in $(seq 1 $MAX_RETRIES); do
        if npx clawhub@latest install aisa-twitter-api; then
            break
        else
            if [ $i -lt $MAX_RETRIES ]; then
                echo "  ⚠ Attempt $i failed (rate limit?). Retrying in ${RETRY_DELAY}s..."
                sleep $RETRY_DELAY
                RETRY_DELAY=$((RETRY_DELAY * 2))
            else
                echo "  ⚠ Failed after $MAX_RETRIES attempts. You can retry later with:"
                echo "    npx clawhub@latest install aisa-twitter-api"
            fi
        fi
    done
fi

echo ""

# ── Step 4: Python dependencies ───────────────────────────────────

echo "Step 4: Python dependencies..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# Create project venv if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "  Creating Python virtual environment at .venv..."
    uv venv "$VENV_DIR"
fi

# Check within the venv
VENV_PYTHON="$VENV_DIR/bin/python3"
MISSING_PKGS=()
"$VENV_PYTHON" -c "import chromadb" 2>/dev/null || MISSING_PKGS+=("chromadb")
"$VENV_PYTHON" -c "import openai" 2>/dev/null || MISSING_PKGS+=("openai")
"$VENV_PYTHON" -c "import weasyprint" 2>/dev/null || MISSING_PKGS+=("weasyprint")
"$VENV_PYTHON" -c "import bs4" 2>/dev/null || MISSING_PKGS+=("beautifulsoup4")
"$VENV_PYTHON" -c "import trafilatura" 2>/dev/null || MISSING_PKGS+=("trafilatura")
"$VENV_PYTHON" -c "import imapclient" 2>/dev/null || MISSING_PKGS+=("imapclient")
"$VENV_PYTHON" -c "import markdown_it" 2>/dev/null || MISSING_PKGS+=("markdown-it-py")
"$VENV_PYTHON" -c "import pydantic" 2>/dev/null || MISSING_PKGS+=("pydantic")
"$VENV_PYTHON" -c "import pytest" 2>/dev/null || MISSING_PKGS+=("pytest")
"$VENV_PYTHON" -c "import requests" 2>/dev/null || MISSING_PKGS+=("requests")
"$VENV_PYTHON" -c "import websocket" 2>/dev/null || MISSING_PKGS+=("websocket-client")
"$VENV_PYTHON" -c "import feedparser" 2>/dev/null || MISSING_PKGS+=("feedparser")

if [ ${#MISSING_PKGS[@]} -eq 0 ]; then
    echo "  ✓ All Python packages already installed. Skipping."
else
    echo "  Installing: ${MISSING_PKGS[*]}..."
    uv pip install --python "$VENV_PYTHON" "${MISSING_PKGS[@]}"
fi

echo ""

# ── Step 5: Initialize data files ─────────────────────────────────

echo "Step 5: Initializing data files..."
DATA_DIR="$SCRIPT_DIR/data"
RAG_DIR="$SCRIPT_DIR/rag"

mkdir -p "$DATA_DIR" "$DATA_DIR/daily-feed" "$RAG_DIR"

init_json() {
    local file="$1"
    local content="$2"
    local name
    name="$(basename "$file")"
    if [ -f "$file" ]; then
        echo "  ✓ $name already exists. Skipping."
    else
        echo "$content" > "$file"
        echo "  Created $name"
    fi
}

init_json "$DATA_DIR/sources.json" '{"sources":[]}'
init_json "$DATA_DIR/accounts.json" '{"accounts":[]}'
init_json "$DATA_DIR/notebooks.json" '{"notebooks":[]}'
init_json "$DATA_DIR/processing-log.json" '{"runs":[]}'
init_json "$DATA_DIR/news-queue.json" '{"articles":[]}'
init_json "$DATA_DIR/newsletters.json" '{"newsletters":[]}'

if [ -f "$DATA_DIR/categories.json" ]; then
    echo "  ✓ categories.json already exists. Skipping."
else
    cat > "$DATA_DIR/categories.json" << 'CATEOF'
{
  "categories": [
    {"name": "Machine Learning", "slug": "ml", "keywords": ["transformer", "neural network", "LLM", "training", "fine-tuning", "deep learning", "GPT", "diffusion"], "url_patterns": ["arxiv.org", "openreview.net", "huggingface.co"]},
    {"name": "Robotics", "slug": "robotics", "keywords": ["ROS", "robot", "SLAM", "manipulation", "control", "autonomous"], "url_patterns": []},
    {"name": "GitHub Repos", "slug": "github", "keywords": [], "url_patterns": ["github.com"]},
    {"name": "General Research", "slug": "general", "keywords": [], "url_patterns": []}
  ]
}
CATEOF
    echo "  Created categories.json"
fi

# Verify prompt templates exist
echo ""
echo "Step 6: Verifying prompt templates..."
PROMPTS_OK=true
for pfile in \
    "$SCRIPT_DIR/config/prompts/daily-podcast-focus.txt" \
    "$SCRIPT_DIR/config/prompts/weekly-podcast-focus.txt" \
    "$SCRIPT_DIR/config/prompts/daily-report-prompt-v3.txt" \
    "$SCRIPT_DIR/templates/report-prompt.txt"; do
    pname="$(echo "$pfile" | sed "s|$SCRIPT_DIR/||")"
    if [ -f "$pfile" ]; then
        chars=$(wc -c < "$pfile" | tr -d ' ')
        echo "  ✓ $pname ($chars chars)"
    else
        echo "  ✗ $pname — MISSING"
        PROMPTS_OK=false
    fi
done
if ! $PROMPTS_OK; then
    echo "  ⚠ Some prompt templates are missing. Podcast/report generation will use defaults."
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo ""
echo "1. Authenticate NotebookLM:"
echo "   nlm login"
echo "   nlm setup add claude-code"
echo ""
echo "2. Create a Telegram bot via @BotFather and configure:"
echo "   - Copy config/openclaw-channel.json to ~/.openclaw/openclaw.json"
echo "   - Replace <YOUR_BOT_TOKEN> with your Telegram bot token"
echo "   - Replace <YOUR_OPENAI_API_KEY> with your OpenAI API key"
echo ""
echo "3. Set your OpenAI API key for RAG embeddings:"
echo "   export OPENAI_API_KEY='your-key-here'"
echo ""
echo "4. Install the OpenClaw daemon:"
echo "   openclaw onboard --install-daemon"
echo ""
echo "5. Set up cron jobs:"
echo ""
echo "   # Daily tech feed (6 PM ET)"
echo "   openclaw cron add \\"
echo "     --name \"daily-tech-feed\" \\"
echo "     --cron \"0 18 * * *\" \\"
echo "     --tz \"America/New_York\" \\"
echo "     --session isolated \\"
echo "     --message \"Execute the daily-digest skill. Follow every step in the Daily Digest Pipeline section of TOOLS.md — Step 0 through Step 9. Do not skip NLM steps. If a step fails, continue to the next step. Reply with a summary of what was collected and delivered.\" \\"
echo "     --announce \\"
echo "     --channel telegram"
echo ""
echo "   # Weekly research digest (Saturday 9 AM ET)"
echo "   openclaw cron add \\"
echo "     --name \"weekly-research-digest\" \\"
echo "     --cron \"0 9 * * 6\" \\"
echo "     --tz \"America/New_York\" \\"
echo "     --session isolated \\"
echo "     --message \"Run the weekly research digest: 1) Execute newsletter-monitor. 2) Execute x-monitor. 3) Run data triage: .venv/bin/python3 data_triage.py --auto. 4) Execute content-processor. 5) Execute notebooklm-orchestrator. 6) Execute news-digest for weekly roundup. 7) Execute digest-deliver.\" \\"
echo "     --announce \\"
echo "     --channel telegram"
echo ""
echo "6. Start using the system via Telegram! Send PDFs, URLs, or commands."
