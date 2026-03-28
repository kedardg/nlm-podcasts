#!/usr/bin/env bash
# Telegram Bot API helper functions for pipeline scripts.
# Replaces openclaw message send with direct curl calls.
#
# Usage: source this file after sourcing config/env.sh
#   source "$PROJECT_ROOT/scripts/telegram_send.sh"
#
# Requires: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in environment.
#
# Functions:
#   tg_send_message "text"              — sendMessage (no parse_mode, safe for dynamic content)
#   tg_send_file "/path/to/file" "cap"  — sendDocument (PDFs, generic files)
#   tg_send_audio "/path/to/file" "cap" — sendAudio (MP3 podcasts — inline player in Telegram)

_tg_check_env() {
    if [[ -z "$TELEGRAM_BOT_TOKEN" ]]; then
        echo "ERROR: TELEGRAM_BOT_TOKEN is not set" >&2
        return 1
    fi
    if [[ -z "$TELEGRAM_CHAT_ID" ]]; then
        echo "ERROR: TELEGRAM_CHAT_ID is not set" >&2
        return 1
    fi
    return 0
}

_tg_check_response() {
    local http_code="$1"
    local body="$2"
    local action="$3"

    if [[ "$http_code" -ge 200 && "$http_code" -lt 300 ]]; then
        return 0
    fi

    echo "ERROR: Telegram $action failed (HTTP $http_code)" >&2
    echo "Response: $body" >&2
    return 1
}

# Send a text message. No parse_mode — safe for dynamic content with *, _, ` characters.
tg_send_message() {
    local text="$1"
    _tg_check_env || return 1

    if [[ -z "$text" ]]; then
        echo "ERROR: tg_send_message requires a text argument" >&2
        return 1
    fi

    local response http_code
    response=$(curl -s -w '\n%{http_code}' \
        -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -F "chat_id=${TELEGRAM_CHAT_ID}" \
        -F "text=${text}" \
        2>/dev/null)

    http_code=$(echo "$response" | tail -1)
    local body
    body=$(echo "$response" | sed '$d')

    _tg_check_response "$http_code" "$body" "sendMessage"
}

# Send a file (PDF, image, etc.) via sendDocument.
tg_send_file() {
    local file_path="$1"
    local caption="${2:-}"
    _tg_check_env || return 1

    if [[ -z "$file_path" || ! -f "$file_path" ]]; then
        echo "ERROR: tg_send_file: file not found: ${file_path:-<empty>}" >&2
        return 1
    fi

    local cmd=(curl -s -w '\n%{http_code}' \
        -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendDocument" \
        -F "chat_id=${TELEGRAM_CHAT_ID}" \
        -F "document=@${file_path}")

    [[ -n "$caption" ]] && cmd+=(-F "caption=${caption}")

    local response http_code
    response=$("${cmd[@]}" 2>/dev/null)

    http_code=$(echo "$response" | tail -1)
    local body
    body=$(echo "$response" | sed '$d')

    _tg_check_response "$http_code" "$body" "sendDocument"
}

# Send an audio file via sendAudio (gives inline player in Telegram for MP3s).
tg_send_audio() {
    local file_path="$1"
    local caption="${2:-}"
    _tg_check_env || return 1

    if [[ -z "$file_path" || ! -f "$file_path" ]]; then
        echo "ERROR: tg_send_audio: file not found: ${file_path:-<empty>}" >&2
        return 1
    fi

    local cmd=(curl -s -w '\n%{http_code}' \
        -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendAudio" \
        -F "chat_id=${TELEGRAM_CHAT_ID}" \
        -F "audio=@${file_path}")

    [[ -n "$caption" ]] && cmd+=(-F "caption=${caption}")

    local response http_code
    response=$("${cmd[@]}" 2>/dev/null)

    http_code=$(echo "$response" | tail -1)
    local body
    body=$(echo "$response" | sed '$d')

    _tg_check_response "$http_code" "$body" "sendAudio"
}
