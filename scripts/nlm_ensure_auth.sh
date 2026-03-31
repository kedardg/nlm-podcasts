#!/bin/bash
# Ensure nlm is authenticated with multi-layer recovery:
#   1. Quick auth check (fast — just tests cached credentials)
#   2. Cookie rotation via RotateCookies endpoint (no browser needed)
#   3. CDP extraction from running Chrome (if rotation fails)
#   4. nlm login (connects to Chrome CDP if running)
#   5. Playwright sync (launch headless Playwright, sync cookies to nlm CLI)
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
ROTATE_SCRIPT="$PROJECT_ROOT/scripts/nlm_rotate_cookies.py"
PLAYWRIGHT_SCRIPT="$PROJECT_ROOT/scripts/nlm_playwright.py"

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

# ── Layer 4: nlm login (connects to Chrome CDP if running) ──
echo "nlm auth: trying nlm login..."
if $NLM login 2>&1; then
  if $NLM login --check >/dev/null 2>&1; then
    echo "nlm auth: recovered via nlm login"
    exit 0
  fi
fi
echo "nlm auth: nlm login failed, trying next layer..."

# ── Layer 5: Playwright sync (headless browser + auto-login from credentials) ──
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
3. nlm login (Chrome CDP)
4. Playwright auth sync (headless + auto-login)

To fix:
1. Run: .venv/bin/python3 scripts/nlm_playwright.py --login
2. Or: open Chrome, log in to notebooklm.google.com, then run ~/.local/bin/nlm login

NLM-dependent steps will be skipped.
ALERT
)"

# Try to send alert via direct Bot API — don't fail if this also fails
source "$SCRIPT_DIR/telegram_send.sh"
tg_send_message "$ALERT_MSG" 2>/dev/null || true

exit 1
