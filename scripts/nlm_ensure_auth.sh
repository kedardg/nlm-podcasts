#!/bin/bash
# Ensure nlm is authenticated with multi-layer recovery:
#   1. Quick auth check (fast — just tests cached credentials)
#   2. Cookie rotation via RotateCookies endpoint (no browser needed)
#   3. CDP extraction from running Chrome (if rotation fails)
#   4. Full nlm login (launches Chrome headless with saved profile)
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

# ── Layer 4: Full nlm login (headless Chrome with saved profile) ──
MAX_RETRIES=2
BASE_DELAY=5
attempt=1
while [ $attempt -le $MAX_RETRIES ]; do
  delay=$(( BASE_DELAY * (2 ** (attempt - 1)) ))
  echo "nlm auth: trying nlm login, attempt $attempt/$MAX_RETRIES (waiting ${delay}s)..."
  sleep "$delay"

  if $NLM login 2>&1; then
    if $NLM login --check >/dev/null 2>&1; then
      echo "nlm auth: refreshed via nlm login on attempt $attempt"
      exit 0
    fi
  fi

  attempt=$((attempt + 1))
done

# ── All recovery attempts failed ──
echo "ERROR: nlm auth failed after all recovery attempts"

ALERT_MSG="$(cat <<'ALERT'
NotebookLM auth failed

All recovery layers exhausted:
1. Cookie rotation (RotateCookies endpoint)
2. CDP extraction from Chrome
3. Headless nlm login (2 attempts)

To fix:
1. Open Chrome and log in to notebooklm.google.com
2. Run: ~/.local/bin/nlm login
3. Re-trigger: openclaw cron run daily-tech-feed

NLM-dependent steps will be skipped.
ALERT
)"

# Try to send alert — don't fail if this also fails
openclaw message send \
  --channel telegram \
  --target "$TELEGRAM_CHAT_ID" \
  --message "$ALERT_MSG" 2>/dev/null || true

exit 1
