#!/bin/bash
# Fetch latest artifacts and deliver to Telegram.
# Encapsulates the entire /generate pipeline — the LLM just runs this and relays output.
#
# Usage:
#   ./fetch_and_deliver.sh <category> <artifact>
#
# Arguments:
#   category: research | news | daily
#   artifact: report | podcast | both
#
# Exit codes:
#   0 = delivered successfully
#   1 = nothing found or delivery failed
#
# Output: Human-readable summary to stdout (relay directly to user).
# Errors/progress go to stderr.

set -uo pipefail

# Source project environment (sets PROJECT_ROOT, TELEGRAM_CHAT_ID)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../config/env.sh" 2>/dev/null || {
    if [ -f "$SCRIPT_DIR/../.env" ]; then
        set -a; source "$SCRIPT_DIR/../.env"; set +a
    fi
}

PROJECT="$PROJECT_ROOT"
WORKSPACE="$HOME/.openclaw/workspace"
NLM="$HOME/.local/bin/nlm"
VENV_PYTHON="$PROJECT/.venv/bin/python3"

# --- Argument parsing ---

if [ $# -lt 2 ]; then
  echo "Usage: fetch_and_deliver.sh <category> <artifact>" >&2
  echo "  category: research | news | daily" >&2
  echo "  artifact: report | podcast | both" >&2
  exit 1
fi

CATEGORY="$1"
ARTIFACT="$2"

# --- Helper functions ---

deliver_file() {
  local file="$1"
  local caption="$2"
  if [ -f "$file" ]; then
    openclaw message send --channel telegram --target "$TELEGRAM_CHAT_ID" --media "$file" --message "$caption" 2>/dev/null
    return $?
  fi
  return 1
}

# --- Step 1: Find latest artifact ---

echo "Looking up latest $CATEGORY $ARTIFACT..." >&2

FETCH_OUTPUT=$($VENV_PYTHON "$PROJECT/scripts/fetch_latest.py" --category "$CATEGORY" --artifact "$ARTIFACT" 2>/tmp/fetch_stderr)
FETCH_EXIT=$?

if [ $FETCH_EXIT -ne 0 ]; then
  ERROR_MSG=$(cat /tmp/fetch_stderr 2>/dev/null || echo "Unknown error")
  echo "$ERROR_MSG"
  exit 1
fi

# --- Branch: Daily feed (filesystem) vs Research/News (NotebookLM) ---

if [ "$CATEGORY" = "daily" ]; then
  # --- DAILY FEED PATH ---
  # Parse JSON with python (avoid jq dependency)
  REPORT_PATH=$($VENV_PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('report_path',''))" <<< "$FETCH_OUTPUT")
  PODCAST_PATH=$($VENV_PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('podcast_path','') or '')" <<< "$FETCH_OUTPUT")
  DATE=$($VENV_PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('date',''))" <<< "$FETCH_OUTPUT")
  HAS_PODCAST=$($VENV_PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); print('true' if d.get('has_podcast') else 'false')" <<< "$FETCH_OUTPUT")

  DELIVERED=""

  # Copy and deliver report
  if [ -n "$REPORT_PATH" ] && [ -f "$REPORT_PATH" ]; then
    cp "$REPORT_PATH" "$WORKSPACE/daily-feed.pdf" 2>/dev/null || cp "$REPORT_PATH" "$WORKSPACE/daily-feed.md" 2>/dev/null
    if [ -f "$WORKSPACE/daily-feed.pdf" ]; then
      deliver_file "$WORKSPACE/daily-feed.pdf" "Daily Tech Feed — $DATE" && DELIVERED="report"
    elif [ -f "$WORKSPACE/daily-feed.md" ]; then
      deliver_file "$WORKSPACE/daily-feed.md" "Daily Tech Feed — $DATE" && DELIVERED="report"
    fi
  fi

  # Copy and deliver podcast
  if [ "$HAS_PODCAST" = "true" ] && [ -n "$PODCAST_PATH" ] && [ -f "$PODCAST_PATH" ]; then
    cp "$PODCAST_PATH" "$WORKSPACE/daily-podcast.mp3"
    deliver_file "$WORKSPACE/daily-podcast.mp3" "Daily Podcast — $DATE" && DELIVERED="$DELIVERED podcast"
  fi

  if [ -z "$DELIVERED" ]; then
    echo "Daily feed files found but delivery failed."
    exit 1
  fi

  echo "Daily Tech Feed ($DATE) delivered: $DELIVERED"
  exit 0
fi

# --- RESEARCH / NEWS PATH (NotebookLM) ---

# Parse JSON
NOTEBOOK_ID=$($VENV_PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('notebook_id',''))" <<< "$FETCH_OUTPUT")
ALIAS=$($VENV_PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('alias',''))" <<< "$FETCH_OUTPUT")
CREATED_AT=$($VENV_PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('created_at',''))" <<< "$FETCH_OUTPUT")
SOURCE_COUNT=$($VENV_PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('source_count',0))" <<< "$FETCH_OUTPUT")
HAS_REPORT=$($VENV_PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); print('true' if d.get('has_report') else 'false')" <<< "$FETCH_OUTPUT")
HAS_PODCAST=$($VENV_PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); print('true' if d.get('has_podcast') else 'false')" <<< "$FETCH_OUTPUT")

NB_REF="$NOTEBOOK_ID"
[ -n "$ALIAS" ] && NB_REF="$ALIAS"

echo "Found notebook: $NB_REF (created $CREATED_AT, $SOURCE_COUNT sources)" >&2

# --- Step 2: NLM auth ---

echo "Checking NotebookLM auth..." >&2
if ! bash "$PROJECT/scripts/nlm_ensure_auth.sh" >/dev/null 2>&1; then
  echo "NotebookLM authentication failed. Please run: ~/.local/bin/nlm login"
  exit 1
fi

DELIVERED=""
SHARE_LINK=""

# --- Step 3: Download and process report ---

if [ "$ARTIFACT" = "report" ] || [ "$ARTIFACT" = "both" ]; then
  if [ "$HAS_REPORT" = "true" ]; then
    echo "Downloading report..." >&2
    if $NLM download report "$NB_REF" --output "$WORKSPACE/report.md" 2>/dev/null; then
      # Enhance report
      echo "Enhancing report..." >&2
      $VENV_PYTHON "$PROJECT/scripts/enhance_report.py" --input "$WORKSPACE/report.md" >/dev/null 2>&1 || true

      # Convert to PDF
      echo "Converting to PDF..." >&2
      DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib $VENV_PYTHON "$PROJECT/lib/pdf_renderer.py" \
        --input "$WORKSPACE/report.md" --output "$WORKSPACE/report.pdf" >/dev/null 2>&1

      # Deliver PDF (fall back to .md)
      if [ -f "$WORKSPACE/report.pdf" ]; then
        deliver_file "$WORKSPACE/report.pdf" "Research Report — $NB_REF" && DELIVERED="report(PDF)"
      fi
      if [ -z "$DELIVERED" ] && [ -f "$WORKSPACE/report.md" ]; then
        deliver_file "$WORKSPACE/report.md" "Research Report — $NB_REF" && DELIVERED="report(MD)"
      fi
    else
      echo "  Report download failed" >&2
    fi
  else
    echo "  Notebook has no report" >&2
  fi
fi

# --- Step 4: Download podcast ---

if [ "$ARTIFACT" = "podcast" ] || [ "$ARTIFACT" = "both" ]; then
  if [ "$HAS_PODCAST" = "true" ]; then
    echo "Downloading podcast..." >&2
    PYTHON="${PROJECT_ROOT:-.}/.venv/bin/python3"
    if $PYTHON "${PROJECT_ROOT:-.}/scripts/nlm_download_audio.py" "$NB_REF" --output "$WORKSPACE/podcast-raw.mp4" 2>/dev/null && [ -s "$WORKSPACE/podcast-raw.mp4" ]; then
      /opt/homebrew/bin/ffmpeg -y -i "$WORKSPACE/podcast-raw.mp4" -ac 1 -b:a 48k -ar 22050 "$WORKSPACE/podcast.mp3" 2>/dev/null
      if [ -s "$WORKSPACE/podcast.mp3" ]; then
        deliver_file "$WORKSPACE/podcast.mp3" "Podcast — $NB_REF" && DELIVERED="$DELIVERED podcast"
      else
        deliver_file "$WORKSPACE/podcast-raw.mp4" "Podcast — $NB_REF" && DELIVERED="$DELIVERED podcast"
      fi
      rm -f "$WORKSPACE/podcast-raw.mp4"
    else
      echo "  Podcast download failed" >&2
    fi
  else
    echo "  Notebook has no podcast" >&2
  fi
fi

# --- Step 5: Share link ---

SHARE_LINK=$($NLM share public "$NOTEBOOK_ID" 2>/dev/null | grep -oE 'https://[^ ]+' | head -1)

# --- Output summary ---

if [ -z "$DELIVERED" ]; then
  echo "Found notebook $NB_REF but could not download or deliver artifacts."
  exit 1
fi

DELIVERED=$(echo "$DELIVERED" | xargs)  # trim whitespace
SUMMARY="Delivered: $DELIVERED from $NB_REF"
[ -n "$CREATED_AT" ] && SUMMARY="$SUMMARY (created $CREATED_AT)"
[ -n "$SOURCE_COUNT" ] && [ "$SOURCE_COUNT" != "0" ] && SUMMARY="$SUMMARY — $SOURCE_COUNT sources"
[ -n "$SHARE_LINK" ] && SUMMARY="$SUMMARY\nNotebook: $SHARE_LINK"

echo -e "$SUMMARY"
exit 0
