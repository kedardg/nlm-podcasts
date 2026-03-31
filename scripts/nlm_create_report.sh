#!/bin/bash
# Wrapper script for creating NotebookLM reports with a custom prompt template.
# Usage: bash nlm_create_report.sh <notebook-id-or-alias> [prompt-file]
#
# If prompt-file is not provided, defaults to templates/report-prompt.txt.
# Reads the prompt and passes it to:
#   nlm report create --format "Create Your Own" --prompt "..."

set -euo pipefail

# Source project environment (sets PROJECT_ROOT, TELEGRAM_CHAT_ID)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../config/env.sh" 2>/dev/null || {
    if [ -f "$SCRIPT_DIR/../.env" ]; then
        set -a; source "$SCRIPT_DIR/../.env"; set +a
    fi
}
DEFAULT_PROMPT="$PROJECT_ROOT/templates/report-prompt.txt"

if [ -z "${1:-}" ]; then
    echo "Usage: bash $0 <notebook-id-or-alias> [prompt-file]" >&2
    exit 1
fi

PROMPT_FILE="${2:-$DEFAULT_PROMPT}"

if [ ! -f "$PROMPT_FILE" ]; then
    echo "ERROR: Prompt template not found: $PROMPT_FILE" >&2
    exit 1
fi

PROMPT=$(cat "$PROMPT_FILE")

~/.local/bin/nlm report create "$1" --format "Create Your Own" --prompt "$PROMPT" --confirm
