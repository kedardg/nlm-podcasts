#!/bin/bash
# Send a pipeline alert to Telegram.
# Usage: ./pipeline_alert.sh "<message>"
# Example: ./pipeline_alert.sh "⚠️ Weekly pipeline: news-digest step failed. Continuing with digest-deliver."

# Source project environment (sets PROJECT_ROOT, TELEGRAM_CHAT_ID)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../config/env.sh" 2>/dev/null || {
    if [ -f "$SCRIPT_DIR/../.env" ]; then
        set -a; source "$SCRIPT_DIR/../.env"; set +a
    fi
}

if [ $# -lt 1 ]; then
  echo "Usage: pipeline_alert.sh <message>" >&2
  exit 1
fi

MSG="$1"

# Source Telegram Bot API helper
source "$SCRIPT_DIR/telegram_send.sh"

tg_send_message "$MSG" || echo "alert: failed to send Telegram notification" >&2
