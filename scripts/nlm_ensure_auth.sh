#!/bin/bash
# Ensure nlm is authenticated with multi-layer recovery:
#   1. Quick auth check (fast — just tests cached credentials)
#   2. Cookie rotation via RotateCookies endpoint (no browser needed)
#   3. CDP extraction from running Chrome (if rotation fails)
#   4. CDP-attach login — launch Chrome headless with nlm's dedicated profile
#      (persistent Google login) and run `nlm login --provider openclaw`.
#      This is the layer that recovers a fully dead session without a human.
#   5. nlm login (launches its own Chrome; fails if regular Chrome is running)
#   6. Playwright sync (launch headless Playwright, sync cookies to nlm CLI)
#
# Usage: ./nlm_ensure_auth.sh
# Exit 0 = auth is valid
# Exit 1 = auth failed after all recovery attempts (alert sent)

# Source project environment (sets PROJECT_ROOT, TELEGRAM_CHAT_ID)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../config/env.sh" 2>/dev/null || {
    if [ -f "$SCRIPT_DIR/../.env" ]; then
        set -a; source "$SCRIPT_DIR/../.env"; set +a
    fi
}

NLM="$HOME/.local/bin/nlm"
PYTHON="$PROJECT_ROOT/.venv/bin/python3"
ROTATE_SCRIPT="$PROJECT_ROOT/src/nlm/nlm_rotate_cookies.py"
PLAYWRIGHT_SCRIPT="$PROJECT_ROOT/src/nlm/nlm_playwright.py"

# Check if nlm exists
if [ ! -f "$NLM" ]; then
  echo "ERROR: nlm not found at $NLM"
  exit 1
fi

# ── Layer 1: Quick auth check ──
if $NLM login --check >/dev/null 2>&1; then
  echo "nlm auth: valid"
  exit 0
fi

echo "nlm auth: expired, attempting recovery..."

# ── Layer 2: Cookie rotation (RotateCookies endpoint) ──
if [ -f "$ROTATE_SCRIPT" ]; then
  echo "nlm auth: trying cookie rotation..."
  if $PYTHON "$ROTATE_SCRIPT" --verify 2>&1; then
    echo "nlm auth: recovered via cookie rotation"
    exit 0
  fi
  echo "nlm auth: rotation failed, trying next layer..."
fi

# ── Layer 3: CDP extraction from running Chrome ──
if [ -f "$ROTATE_SCRIPT" ]; then
  echo "nlm auth: trying CDP extraction from Chrome..."
  if $PYTHON "$ROTATE_SCRIPT" --cdp --verify 2>&1; then
    echo "nlm auth: recovered via CDP extraction"
    exit 0
  fi
  echo "nlm auth: CDP extraction failed, trying next layer..."
fi

# ── Layer 4: CDP-attach login via nlm's dedicated Chrome profile ──
# The profile at ~/.notebooklm-mcp-cli/chrome-profiles/default keeps a
# persistent Google login. Launch it headless with a debug port (only if no
# CDP endpoint is already up) and let nlm extract cookies + session tokens.
NLM_CHROME_PROFILE="$HOME/.notebooklm-mcp-cli/chrome-profiles/default"
CHROME_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CDP_URL="http://127.0.0.1:9222"
echo "nlm auth: trying CDP-attach login..."
LAUNCHED_CHROME_PID=""
if ! curl -s -m 2 "$CDP_URL/json/version" >/dev/null 2>&1; then
  if [ -x "$CHROME_BIN" ] && [ -d "$NLM_CHROME_PROFILE" ]; then
    "$CHROME_BIN" --remote-debugging-port=9222 --no-first-run --no-default-browser-check \
      --disable-extensions --user-data-dir="$NLM_CHROME_PROFILE" --remote-allow-origins='*' \
      --headless=new "about:blank" >/dev/null 2>&1 &
    LAUNCHED_CHROME_PID=$!
    sleep 6
  fi
fi
if curl -s -m 2 "$CDP_URL/json/version" >/dev/null 2>&1; then
  if $NLM login --provider openclaw --cdp-url "$CDP_URL" 2>&1 && $NLM login --check >/dev/null 2>&1; then
    echo "nlm auth: recovered via CDP-attach login"
    [ -n "$LAUNCHED_CHROME_PID" ] && kill "$LAUNCHED_CHROME_PID" 2>/dev/null
    exit 0
  fi
fi
[ -n "$LAUNCHED_CHROME_PID" ] && kill "$LAUNCHED_CHROME_PID" 2>/dev/null
echo "nlm auth: CDP-attach login failed, trying next layer..."

# ── Layer 5: nlm login (launches its own Chrome; fails if Chrome already running) ──
echo "nlm auth: trying nlm login..."
if $NLM login 2>&1; then
  if $NLM login --check >/dev/null 2>&1; then
    echo "nlm auth: recovered via nlm login"
    exit 0
  fi
fi
echo "nlm auth: nlm login failed, trying next layer..."

# ── Layer 6: Playwright sync (headless browser + auto-login from credentials) ──
if [ -f "$PLAYWRIGHT_SCRIPT" ]; then
  echo "nlm auth: trying Playwright auth sync..."
  if $PYTHON "$PLAYWRIGHT_SCRIPT" --sync-auth 2>&1; then
    # Verify nlm CLI can use the synced cookies
    if $NLM login --check >/dev/null 2>&1; then
      echo "nlm auth: recovered via Playwright sync"
      exit 0
    fi
    echo "nlm auth: Playwright sync succeeded but nlm check still fails"
  fi
  echo "nlm auth: Playwright sync failed"
fi

# ── All recovery attempts failed ──
echo "ERROR: nlm auth failed after all recovery attempts"

ALERT_MSG="$(cat <<'ALERT'
NotebookLM auth failed

All recovery layers exhausted:
1. Cookie rotation (RotateCookies endpoint)
2. CDP extraction from Chrome
3. CDP-attach login (dedicated Chrome profile, headless)
4. nlm login (Chrome CDP)
5. Playwright auth sync (headless + auto-login)

To fix (the Google login in the dedicated Chrome profile has likely expired):
1. Quit Chrome, then run: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222 --user-data-dir="$HOME/.notebooklm-mcp-cli/chrome-profiles/default"
   Sign in to Google there, then run: ~/.local/bin/nlm login --provider openclaw --cdp-url http://127.0.0.1:9222
2. Or: .venv/bin/python3 scripts/nlm_playwright.py --login

NLM-dependent steps will be skipped.
ALERT
)"

# Try to send alert via direct Bot API — don't fail if this also fails
source "$SCRIPT_DIR/telegram_send.sh"
tg_send_message "$ALERT_MSG" 2>/dev/null || true

exit 1
