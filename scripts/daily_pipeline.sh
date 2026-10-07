#!/usr/bin/env bash
# Daily Tech Feed Pipeline
#
# Deterministic shell script for the daily-digest cron job.
# Runs all steps from SKILL.md (Step 0–10) in sequence.
# Each step continues on failure (never halts the pipeline).
#
# Usage:
#   bash scripts/daily_pipeline.sh              # Full pipeline
#   bash scripts/daily_pipeline.sh --skip-nlm   # Skip NotebookLM steps
#   bash scripts/daily_pipeline.sh --pdf-only   # Steps 0-1 only (no NLM)
#   bash scripts/daily_pipeline.sh --date 2026-02-20  # Specific date
#   bash scripts/daily_pipeline.sh --use-playwright   # Use Playwright instead of NLM CLI

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Source environment — try env.sh first, fall back to .env directly.
# env.sh may fail in restricted login shells (e.g., OpenClaw exec on external volumes).
source "$PROJECT_ROOT/config/env.sh" 2>/dev/null || {
    if [ -f "$PROJECT_ROOT/.env" ]; then
        set -a; source "$PROJECT_ROOT/.env"; set +a
    else
        echo "FATAL: Cannot source env.sh or .env"; exit 1
    fi
}

# Source Telegram Bot API helper
source "$PROJECT_ROOT/scripts/telegram_send.sh"

PYTHON="$PROJECT_ROOT/.venv/bin/python3"
NLM="$HOME/.local/bin/nlm"
FFMPEG="/opt/homebrew/bin/ffmpeg"
[[ -x "$FFMPEG" ]] || FFMPEG="ffmpeg"
DATE=$(date +%Y-%m-%d)
SKIP_NLM=false
PDF_ONLY=false
USE_PLAYWRIGHT=false
ERRORS=()
NOTEBOOK_ID=""

for arg in "$@"; do
    case "$arg" in
        --skip-nlm) SKIP_NLM=true ;;
        --pdf-only) PDF_ONLY=true; SKIP_NLM=true ;;
        --use-playwright) USE_PLAYWRIGHT=true ;;
        --date) :;; # handled below
    esac
done

# Parse --date VALUE
for i in "$@"; do
    if [[ "$prev" == "--date" ]]; then
        DATE="$i"
    fi
    prev="$i"
done

FEED_DIR="$PROJECT_ROOT/data/daily-feed"
MD_FILE="$FEED_DIR/$DATE.md"
HTML_FILE="$FEED_DIR/$DATE.html"
PDF_FILE="$FEED_DIR/$DATE.pdf"
REPORT_MD="$FEED_DIR/$DATE-report.md"
REPORT_PDF="$FEED_DIR/$DATE-report.pdf"
PODCAST_RAW="$FEED_DIR/$DATE-podcast-raw.mp4"
PODCAST="$FEED_DIR/$DATE-podcast.mp3"
SRC_MANIFEST="$FEED_DIR/$DATE-sources.txt"
DEEPDIVE_MD="$FEED_DIR/$DATE-deepdive.md"
DEEPDIVE_PDF="$FEED_DIR/$DATE-deepdive.pdf"
DEEPDIVE_PLAN="$FEED_DIR/$DATE-deepdive-plan.json"

PIPELINE_START=$(date +%s)
STEP_DURATIONS=""

log() { echo "[$(date +%H:%M:%S)] $*"; }
step_ok() { log "  OK: $1"; }
step_fail() { log "  FAIL: $1"; ERRORS+=("$1"); }
alert() {
    if [[ -f "$PROJECT_ROOT/scripts/pipeline_alert.sh" ]]; then
        bash "$PROJECT_ROOT/scripts/pipeline_alert.sh" "$1" 2>/dev/null || true
    fi
}
log_json() {
    local event="$1" step="$2" status="${3:-info}" dur="${4:-}" err="${5:-}"
    printf '{"ts":"%s","event":"%s","step":"%s","status":"%s"' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$event" "$step" "$status"
    [ -n "$dur" ] && printf ',"duration_ms":%s' "$dur"
    [ -n "$err" ] && printf ',"error":"%s"' "$err"
    printf '}\n' >&2
}
millis() { python3 -c 'import time;print(int(time.time()*1000))'; }
step_start() { eval "STEP_T_$1=$(millis)"; log_json "step_start" "$1"; }
step_end() {
    local step="$1" status="$2"
    local start_var="STEP_T_$step"
    local start=${!start_var:-0}
    local now=$(millis)
    local dur=$((now - start))
    log_json "step_end" "$step" "$status" "$dur"
    STEP_DURATIONS="$STEP_DURATIONS\"$step\":{\"status\":\"$status\",\"duration_ms\":$dur},"
}

ensure_auth_or_rotate() {
    $NLM login --check 2>/dev/null && return 0
    log "  Auth expired, rotating..."
    $PYTHON "$PROJECT_ROOT/src/nlm/nlm_rotate_cookies.py" --verify 2>/dev/null && return 0
    log "  Rotation failed, trying CDP..."
    $PYTHON "$PROJECT_ROOT/src/nlm/nlm_rotate_cookies.py" --cdp --verify 2>/dev/null && return 0
    return 1
}

# ── Pipeline lock (prevent daily + weekly + reclaim running simultaneously) ──
# Liveness-based, not clock-based: a reclaim pass over hundreds of notebooks
# legitimately outlives any fixed timeout, and the old mtime heuristic paired
# with an unconditional `trap rmdir` let a superseded process delete the new
# holder's lock. See scripts/pipeline_lock.sh.
source "$PROJECT_ROOT/scripts/pipeline_lock.sh"
acquire_pipeline_lock "$PROJECT_ROOT/data/.pipeline.lock" "daily" || exit 1

log "=== Daily Tech Feed Pipeline — $DATE ==="
log "Project: $PROJECT_ROOT"
log "Skip NLM: $SKIP_NLM | PDF Only: $PDF_ONLY | Playwright: $USE_PLAYWRIGHT"
echo ""

# ── Step 0: Fetch fresh newsletter emails ──────────────────────
log "Step 0: Fetch newsletters"
step_start "newsletter_fetch"
$PYTHON "$PROJECT_ROOT/src/newsletter_parser.py" 2>&1 && { step_ok "newsletter-fetch"; step_end "newsletter_fetch" "ok"; } || { step_fail "newsletter-fetch"; step_end "newsletter_fetch" "fail"; }
echo ""

# ── Step 0.5: Blog monitor ───────────────────────────────────
log "Step 0.5: Blog monitor"
step_start "blog_monitor"
$PYTHON "$PROJECT_ROOT/src/blog_monitor.py" 2>&1 && { step_ok "blog-monitor"; step_end "blog_monitor" "ok"; } || { step_fail "blog-monitor"; step_end "blog_monitor" "fail"; }
echo ""

# ── Step 1: Generate daily feed HTML ────────────────────────────
log "Step 0.7: Expire sources outside the collection window"
# Weekly used to drain everything older than the daily window into its own
# notebooks. With weekly notebook creation retired, anything that falls out of
# the two-day window would otherwise accumulate in sources.json forever. Rows
# are moved to data/archive/, not deleted.
$PYTHON "$PROJECT_ROOT/src/pipeline/expire_sources.py" --days 2 2>&1 && \
    step_ok "expire-sources" || step_fail "expire-sources"
echo ""

log "Step 1: Generate daily feed (markdown + HTML)"
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib $PYTHON "$PROJECT_ROOT/src/daily_feed_generator.py" \
    --pdf-only --date "$DATE" 2>&1
STEP1_EXIT=$?

if [[ $STEP1_EXIT -ne 0 ]]; then
    step_fail "daily-feed-generator"
    # Check if "no items" scenario
    if [[ ! -f "$MD_FILE" ]]; then
        log "  No items for $DATE — nothing to digest."
        alert "No items received for $DATE — no daily digest generated."
        exit 0
    fi
else
    step_ok "daily-feed-generator"
fi
echo ""

# Stop here if --pdf-only
if $PDF_ONLY; then
    log "=== PDF-only mode — skipping NLM steps ==="
    # Deliver HTML (or fall back to PDF)
    if [[ -f "$HTML_FILE" ]] && [[ -n "$TELEGRAM_CHAT_ID" ]]; then
        log "Delivering HTML via Telegram..."
        tg_send_file "$HTML_FILE" "Daily Tech Feed — $DATE" 2>&1 && step_ok "telegram-html" || step_fail "telegram-html"
    elif [[ -f "$PDF_FILE" ]] && [[ -n "$TELEGRAM_CHAT_ID" ]]; then
        log "Delivering PDF via Telegram..."
        tg_send_file "$PDF_FILE" "Daily Tech Feed — $DATE" 2>&1 && step_ok "telegram-pdf" || step_fail "telegram-pdf"
    fi
    log "=== Pipeline Complete (PDF-only mode) ==="
    exit 0
fi

# ── Playwright mode: replace NLM CLI steps 1.5–7 with single script ──
if $USE_PLAYWRIGHT && ! $SKIP_NLM; then
    log "=== Using Playwright for NLM steps ==="
    step_start "playwright_nlm"

    # Build command
    PW_CMD=("$PYTHON" "$PROJECT_ROOT/src/nlm/nlm_playwright.py"
        --create "Daily Feed — $DATE"
        --headless
        --download-dir "$FEED_DIR"
        --output-prefix "$DATE"
        --share
    )
    # Use Chrome CDP if available (e.g., Chrome running with --remote-debugging-port)
    if [[ -n "${NLM_CDP_URL:-}" ]]; then
        PW_CMD+=(--cdp-url "$NLM_CDP_URL")
    fi

    # Add URL sources
    URLS=$($PYTHON -c "
import json
from datetime import datetime, timedelta
sources = json.load(open('$PROJECT_ROOT/data/sources.json'))
dt = datetime.strptime('$DATE', '%Y-%m-%d')
# Include DATE+1 to handle items fetched after UTC midnight during late-night runs
valid = [(dt + timedelta(days=1)).strftime('%Y-%m-%d')] + [(dt - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(2)]
urls = []
for s in sources.get('sources', []):
    added = s.get('added_at', '')
    if any(added.startswith(d) for d in valid) and s.get('url'):
        urls.append(s['url'])
seen = set()
for u in urls:
    if u not in seen:
        seen.add(u)
        print(u)
    if len(seen) >= 15:
        break
" 2>/dev/null)

    URL_ARGS=()
    while IFS= read -r url; do
        [[ -z "$url" ]] && continue
        URL_ARGS+=("$url")
    done <<< "$URLS"
    if [[ ${#URL_ARGS[@]} -gt 0 ]]; then
        PW_CMD+=(--add-urls "${URL_ARGS[@]}")
    fi

    # Add briefing text as source
    if [[ -f "$MD_FILE" ]]; then
        PW_CMD+=(--add-text "$MD_FILE" --text-title "Daily Feed Briefing — $DATE")
    fi

    # Report prompt
    REPORT_PROMPT="$PROJECT_ROOT/config/prompts/daily-report-prompt-v3.txt"
    if [[ -f "$REPORT_PROMPT" ]]; then
        PW_CMD+=(--report-prompt "$REPORT_PROMPT")
    fi

    # Podcast
    FOCUS_FILE="$PROJECT_ROOT/config/prompts/daily-podcast-focus.txt"
    PW_CMD+=(--podcast-format deep_dive --podcast-length long)
    if [[ -f "$FOCUS_FILE" ]]; then
        PW_CMD+=(--podcast-focus "$FOCUS_FILE")
    fi

    # Run Playwright automation
    PW_OUTPUT=$("${PW_CMD[@]}" 2>&1)
    PW_EXIT=$?

    if [[ $PW_EXIT -eq 0 ]]; then
        step_ok "playwright-nlm"
        step_end "playwright_nlm" "ok"

        # Extract notebook ID and share link from output
        NOTEBOOK_ID=$(echo "$PW_OUTPUT" | grep '^NOTEBOOK_ID=' | cut -d= -f2)
        SHARE_LINK=$(echo "$PW_OUTPUT" | grep '^SHARE_LINK=' | cut -d= -f2-)

        # Check artifacts
        [[ -f "$REPORT_MD" ]] && step_ok "report-download" || step_fail "report-download (Playwright)"
        [[ -f "$PODCAST_RAW" ]] && step_ok "podcast-download" || step_fail "podcast-download (Playwright)"
    else
        step_fail "playwright-nlm"
        step_end "playwright_nlm" "fail"
        log "  Playwright output: $(echo "$PW_OUTPUT" | tail -5)"
    fi

    echo ""

    # Compress podcast for Telegram (same as CLI path)
    if [[ -f "$PODCAST_RAW" ]] && [[ -s "$PODCAST_RAW" ]]; then
        $FFMPEG -y -i "$PODCAST_RAW" -ac 1 -b:a 48k -ar 22050 "$PODCAST" 2>&1 && \
            step_ok "podcast-compress" || step_fail "podcast-compress"
    fi

    # Magazine PDF (same as CLI path)
    if [[ -f "$REPORT_MD" ]]; then
        log "Step 6.5: Generate magazine HTML"
        DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib $PYTHON "$PROJECT_ROOT/src/daily_feed_generator.py" \
            --date "$DATE" --pdf-only 2>&1 && \
            step_ok "magazine-html" || step_fail "magazine-html"
        echo ""
    fi

    # Skip the NLM CLI steps below — jump straight to Telegram delivery
    SKIP_NLM=true
fi

# ── Step 1.5: Ensure NotebookLM auth ──────────────────────────
if ! $SKIP_NLM; then
    log "Step 1.5: NLM auth check"
    bash "$PROJECT_ROOT/scripts/nlm_ensure_auth.sh" 2>&1
    if [[ $? -ne 0 ]]; then
        step_fail "nlm-auth"
        alert "Daily pipeline: NLM auth failed, skipping NLM steps"
        SKIP_NLM=true
    else
        step_ok "nlm-auth"
    fi
    echo ""
fi

# ── Step 2: Create NotebookLM notebook ─────────────────────────
if ! $SKIP_NLM; then
    log "Step 2: Create NLM notebook"

    # Capacity guard. The account is hard-capped at 500 notebooks; creating
    # blindly into a full account is what took this pipeline down for months.
    if ! $PYTHON "$PROJECT_ROOT/src/pipeline/daily_capture_delete.py" \
            --capacity-check >/dev/null 2>&1; then
        log "  ERROR: notebook capacity exhausted — run reclaim_notebooks.py"
        alert "Daily pipeline: NotebookLM capacity exhausted; run reclaim_notebooks.py"
        SKIP_NLM=true
    fi
fi

if ! $SKIP_NLM; then
    # Idempotency, first against the archive. The alias check below cannot be
    # the only guard any more: Step 11 deletes the alias along with the
    # notebook, so a rerun would sail past it and build a second notebook —
    # and regenerate the whole studio set — for a date already finished.
    if $PYTHON "$PROJECT_ROOT/src/pipeline/daily_capture_delete.py" \
            --already-done --run-id "daily_$DATE" 2>/dev/null; then
        log "  Already archived for $DATE; artifacts are on disk"
        SKIP_NLM=true
    fi
fi

if ! $SKIP_NLM; then
    # Idempotency: check if notebook already exists for this date
    EXISTING_NB=$($NLM alias get "daily-$DATE" 2>/dev/null | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)
    if [[ -n "$EXISTING_NB" ]]; then
        log "  Notebook daily-$DATE already exists: $EXISTING_NB"
        NOTEBOOK_ID="$EXISTING_NB"
        step_ok "notebook-create (existing $NOTEBOOK_ID)"
    else

    NB_OUTPUT=$($NLM notebook create "Daily Feed — $DATE" 2>&1)
    NB_EXIT=$?

    if [[ $NB_EXIT -eq 0 ]]; then
        # Extract notebook ID (UUID pattern)
        NOTEBOOK_ID=$(echo "$NB_OUTPUT" | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)
        if [[ -z "$NOTEBOOK_ID" ]]; then
            # Try JSON parsing
            NOTEBOOK_ID=$(echo "$NB_OUTPUT" | $PYTHON -c "import sys,json; d=json.load(sys.stdin); print(d.get('notebook_id','') or d.get('id',''))" 2>/dev/null)
        fi

        if [[ -n "$NOTEBOOK_ID" ]]; then
            step_ok "notebook-create ($NOTEBOOK_ID)"
            $NLM alias set "daily-$DATE" "$NOTEBOOK_ID" 2>/dev/null || true
        else
            step_fail "notebook-create (no ID in output)"
            log "  Output: $NB_OUTPUT"
            SKIP_NLM=true
        fi
    else
        step_fail "notebook-create"
        log "  Output: $NB_OUTPUT"
        SKIP_NLM=true
    fi

    fi  # end of else (notebook didn't exist)
    echo ""
fi

# ── Step 3: Add sources to notebook ────────────────────────────
if ! $SKIP_NLM && [[ -n "$NOTEBOOK_ID" ]]; then
    log "Step 3: Add sources to notebook"
    ensure_auth_or_rotate || { step_fail "nlm-auth-step3"; SKIP_NLM=true; }
    ADDED=0
    FAILED=0

    # Reset the deterministic manifest of URLs actually added to the notebook
    # (SRC_MANIFEST is defined at top). Consumed by enhance_report.py to build the
    # report's ## Source Links section from ground truth.
    : > "$SRC_MANIFEST"

    # Collect top URLs from today's feed (max 15)
    URLS=$($PYTHON -c "
import json, sys
from datetime import datetime, timedelta
sources = json.load(open('$PROJECT_ROOT/data/sources.json'))
dt = datetime.strptime('$DATE', '%Y-%m-%d')
# Include DATE+1 to handle items fetched after UTC midnight during late-night runs
valid = [(dt + timedelta(days=1)).strftime('%Y-%m-%d')] + [(dt - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(2)]
urls = []
for s in sources.get('sources', []):
    added = s.get('added_at', '')
    if any(added.startswith(d) for d in valid) and s.get('url'):
        urls.append(s['url'])
# Deduplicate, limit to 15
seen = set()
for u in urls:
    if u not in seen:
        seen.add(u)
        print(u)
    if len(seen) >= 15:
        break
" 2>/dev/null)

    while IFS= read -r url; do
        [[ -z "$url" ]] && continue
        $NLM source add "$NOTEBOOK_ID" --url "$url" --wait 2>&1 >/dev/null
        if [[ $? -eq 0 ]]; then
            ADDED=$((ADDED + 1))
            echo "$url" >> "$SRC_MANIFEST"
        else
            FAILED=$((FAILED + 1))
        fi
    done <<< "$URLS"

    # Add briefing text as source
    if [[ -f "$MD_FILE" ]]; then
        BRIEFING=$(cat "$MD_FILE")
        $NLM source add "$NOTEBOOK_ID" --text "$BRIEFING" --title "Daily Feed Briefing — $DATE" --wait 2>&1 >/dev/null
        [[ $? -eq 0 ]] && ADDED=$((ADDED + 1)) || FAILED=$((FAILED + 1))
    fi

    step_ok "sources ($ADDED added, $FAILED failed)"
    echo ""
fi

# ── Step 4: Generate report and podcast ────────────────────────
if ! $SKIP_NLM && [[ -n "$NOTEBOOK_ID" ]]; then
    log "Step 4: Generate NLM report + podcast"
    ensure_auth_or_rotate || { step_fail "nlm-auth-step4"; SKIP_NLM=true; }

    # Report (v3 markdown prompt for magazine layout)
    REPORT_PROMPT="$PROJECT_ROOT/config/prompts/daily-report-prompt-v3.txt"
    if [[ -f "$REPORT_PROMPT" ]]; then
        bash "$PROJECT_ROOT/scripts/nlm_create_report.sh" "$NOTEBOOK_ID" "$REPORT_PROMPT" 2>&1 && \
            step_ok "report-create" || step_fail "report-create"
    else
        step_fail "report-create (missing prompt: $REPORT_PROMPT)"
    fi

    # Podcast
    FOCUS_FILE="$PROJECT_ROOT/config/prompts/daily-podcast-focus.txt"
    PODCAST_CMD=("$NLM" audio create "$NOTEBOOK_ID" --format deep_dive --length long --confirm)
    if [[ -f "$FOCUS_FILE" ]]; then
        FOCUS=$(cat "$FOCUS_FILE")
        PODCAST_CMD+=(--focus "$FOCUS")
    fi
    "${PODCAST_CMD[@]}" 2>&1 && step_ok "podcast-create" || step_fail "podcast-create"
    echo ""
fi

# ── Step 5: Poll for completion ────────────────────────────────
if ! $SKIP_NLM && [[ -n "$NOTEBOOK_ID" ]]; then
    log "Step 5: Polling studio status (max 60 min)..."
    # 60 min headroom for generation. The stale-lock threshold (4 h) is sized to
    # cover this + Step 6 (60 min) + Step 6.6 deep dives so a slow run can't let a
    # manual /process double-run and create duplicate notebooks/audios.
    MAX_WAIT=3600
    WAITED=0
    POLL_INTERVAL=30
    AUTH_FAILURES=0

    while [[ $WAITED -lt $MAX_WAIT ]]; do
        STATUS_OUT=$($NLM studio status "$NOTEBOOK_ID" 2>&1)
        STATUS_EXIT=$?

        # Check for auth failure (exit code != 0 and no valid JSON)
        if [[ $STATUS_EXIT -ne 0 ]] || echo "$STATUS_OUT" | grep -qi "authentication\|no auth\|login"; then
            AUTH_FAILURES=$((AUTH_FAILURES + 1))
            if [[ $AUTH_FAILURES -le 2 ]]; then
                log "  Auth expired during poll — rotating cookies (attempt $AUTH_FAILURES)..."
                $PYTHON "$PROJECT_ROOT/src/nlm/nlm_rotate_cookies.py" --verify 2>&1 || true
                sleep 5
                WAITED=$((WAITED + 5))
                continue
            else
                log "  Auth recovery failed after $AUTH_FAILURES attempts"
                break
            fi
        fi

        AUTH_FAILURES=0  # Reset on success
        break
    done

    # Wait for positive confirmation that each artifact is finished.
    #
    # The old poll asked "does the status mention in_progress?" and treated
    # "no" as done. In the seconds after `report create` the artifact is not
    # registered yet, so the status mentions nothing — the poll exited
    # immediately, logged "completed in 0s", and Step 6 then tried to download
    # a report that did not exist. Absence of evidence was read as evidence.
    if $PYTHON "$PROJECT_ROOT/src/pipeline/daily_capture_delete.py" \
            --wait-for report,audio --notebook-id "$NOTEBOOK_ID" \
            --timeout "$MAX_WAIT" --poll-interval "$POLL_INTERVAL"; then
        step_ok "studio-poll"
    else
        step_fail "studio-poll (timeout or generation failure)"
    fi
    echo ""
fi

# ── Step 6: Download report and podcast ────────────────────────
if ! $SKIP_NLM && [[ -n "$NOTEBOOK_ID" ]]; then
    log "Step 6: Download artifacts"
    ensure_auth_or_rotate || { step_fail "nlm-auth-step6"; SKIP_NLM=true; }

    # Download report (skip if already exists and non-empty)
    if [[ -s "$REPORT_MD" ]]; then
        log "  Report already downloaded: $REPORT_MD"
        step_ok "report-download (cached)"
    else
        # Step 5 already waited for report+audio to report 'completed', via
        # daily_capture_delete.py --wait-for, so the artifact is ready here.
        # That replaced an inline loop that grepped `studio status` text; it
        # fixed the same race (BUG-012) but matched on absence of in_progress,
        # which reads an unregistered artifact as finished.
        $NLM download report "$NOTEBOOK_ID" --output "$REPORT_MD" 2>&1
        # One retry after a short wait if the download came back empty
        if [[ ! -s "$REPORT_MD" ]]; then
            log "  Report download empty — retrying in 60s..."
            sleep 60
            $NLM download report "$NOTEBOOK_ID" --output "$REPORT_MD" 2>&1
        fi
    fi
    if [[ -f "$REPORT_MD" ]] && [[ -s "$REPORT_MD" ]]; then
        step_ok "report-download"

        # Enhance report — deterministic ## Source Links from the add-time manifest
        if [[ -f "$PROJECT_ROOT/src/pipeline/enhance_report.py" ]]; then
            ENHANCE_ARGS=(--input "$REPORT_MD")
            [[ -s "$SRC_MANIFEST" ]] && ENHANCE_ARGS+=(--manifest "$SRC_MANIFEST")
            $PYTHON "$PROJECT_ROOT/src/pipeline/enhance_report.py" "${ENHANCE_ARGS[@]}" 2>&1 || true
        fi

        # Render report PDF
        DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib $PYTHON "$PROJECT_ROOT/src/lib/pdf_renderer.py" \
            --input "$REPORT_MD" --output "$REPORT_PDF" 2>&1 && \
            step_ok "report-pdf" || step_fail "report-pdf"
    else
        step_fail "report-download"
    fi

    # Download podcast (skip if already exists and non-empty)
    if [[ -s "$PODCAST_RAW" ]]; then
        log "  Podcast already downloaded: $PODCAST_RAW"
        step_ok "podcast-download (cached)"
    else
        # Wait for audio to be ready (poll up to 60 min with 30s intervals)
        AUDIO_WAIT=0
        AUDIO_MAX=3600
        while [[ $AUDIO_WAIT -lt $AUDIO_MAX ]]; do
            ensure_auth_or_rotate || true
            AUDIO_STATUS=$($NLM studio status "$NOTEBOOK_ID" 2>&1)
            if echo "$AUDIO_STATUS" | grep -q '"type": "audio"' && echo "$AUDIO_STATUS" | grep -A2 '"type": "audio"' | grep -q '"status": "completed"'; then
                log "  Audio ready (waited ${AUDIO_WAIT}s)"
                break
            fi
            log "  Audio not ready yet... (${AUDIO_WAIT}s elapsed)"
            sleep 30
            AUDIO_WAIT=$((AUDIO_WAIT + 30))
        done

        # Primary: nlm CLI (fixed in nlm 0.4.9+). For long audio the media URL
        # can keep "propagating" for 10+ min after status=completed and the
        # CLI's built-in ~4 min retry budget gives up too early — keep retrying.
        DL_WAIT=0
        DL_MAX=1200
        while true; do
            $NLM download audio "$NOTEBOOK_ID" --output "$PODCAST_RAW" 2>&1
            [[ -s "$PODCAST_RAW" ]] && break
            [[ $DL_WAIT -ge $DL_MAX ]] && break
            log "  Audio download failed (URL likely still propagating) — retrying in 120s... (${DL_WAIT}s elapsed)"
            sleep 120
            DL_WAIT=$((DL_WAIT + 120))
        done
        # Fallback 1: CDP (uses Chrome session cookies)
        if [[ ! -s "$PODCAST_RAW" ]]; then
            log "  nlm download failed — trying CDP..."
            $PYTHON "$PROJECT_ROOT/src/nlm/nlm_download_audio.py" "$NOTEBOOK_ID" --output "$PODCAST_RAW" 2>&1
        fi
        # Fallback 2: Playwright (works without Chrome CDP)
        if [[ ! -s "$PODCAST_RAW" ]]; then
            log "  CDP download failed — falling back to Playwright..."
            ensure_auth_or_rotate || true
            $PYTHON "$PROJECT_ROOT/src/nlm/nlm_playwright.py" \
                --open "$NOTEBOOK_ID" \
                --headless \
                --download-dir "$FEED_DIR" \
                --output-prefix "$DATE" 2>&1
        fi
    fi
    if [[ -f "$PODCAST_RAW" ]] && [[ -s "$PODCAST_RAW" ]]; then
        step_ok "podcast-download"

        # Compress for Telegram (48kbps mono)
        $FFMPEG -y -i "$PODCAST_RAW" -ac 1 -b:a 48k -ar 22050 "$PODCAST" 2>&1 && \
            step_ok "podcast-compress" || step_fail "podcast-compress"
    else
        step_fail "podcast-download"
    fi
    echo ""

    # ── Step 6.5: Magazine PDF (NLM-first layout) ──────────────
    if [[ -f "$REPORT_MD" ]]; then
        log "Step 6.5: Generate magazine HTML"
        DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib $PYTHON "$PROJECT_ROOT/src/daily_feed_generator.py" \
            --date "$DATE" --pdf-only 2>&1 && \
            step_ok "magazine-html" || step_fail "magazine-html"
        echo ""
    fi

    # ── Step 6.6: Deep Dive Review (per-paper explainers; Phase 1 = text-only) ──
    # Writes DATE-deepdive.{md,pdf} and a one-way DATE-deepdive-plan.json naming the
    # top<=3 papers for per-paper podcasts (Phase 2 consumes the plan). Needs the
    # notebook (nlm notebook query) so it lives inside the !SKIP_NLM guard.
    if [[ -f "$REPORT_MD" ]]; then
        log "Step 6.6: Generate Deep Dive Review (per-paper explainers)"
        if [[ -s "$DEEPDIVE_PDF" ]]; then
            step_ok "deepdive (cached)"   # idempotent rerun — don't re-query NLM
        else
            DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib $PYTHON "$PROJECT_ROOT/src/pipeline/deep_dive_generator.py" \
                --date "$DATE" --notebook-id "$NOTEBOOK_ID" --report-md "$REPORT_MD" 2>&1 && \
                step_ok "deepdive" || step_fail "deepdive"
        fi
        echo ""
    fi

    # ── Step 6.6b: Deep Dive per-paper podcasts (Phase 2) ──────
    # From DATE-deepdive-plan.json: fire one source-scoped `audio create` per top
    # paper (verified to parallelize on one notebook), poll studio status BY ARTIFACT
    # ID (the notebook also holds the daily podcast), then download (--id) + compress
    # each as it completes. Per-paper deadline + isolation: one failure never aborts
    # the batch, the PDF, or the rest of the pipeline.
    DD_PAPER_COUNT=0
    [[ -f "$DEEPDIVE_PLAN" ]] && DD_PAPER_COUNT=$($PYTHON -c "import json; print(len(json.load(open('$DEEPDIVE_PLAN')).get('papers', [])))" 2>/dev/null || echo 0)
    if [[ "$DD_PAPER_COUNT" =~ ^[0-9]+$ ]] && [[ "$DD_PAPER_COUNT" -gt 0 ]]; then
        log "Step 6.6b: Deep Dive per-paper podcasts ($DD_PAPER_COUNT)"
        DD_SLUGS=(); DD_AIDS=(); DD_DEADLINES=()
        # 1. Fire all scoped audio creates (staggered to avoid a burst), capture ids
        for ((di=0; di<DD_PAPER_COUNT; di++)); do
            DD_SID=$($PYTHON -c "import json; print(json.load(open('$DEEPDIVE_PLAN'))['papers'][$di].get('source_id') or '')" 2>/dev/null)
            DD_SLUG=$($PYTHON -c "import json; print(json.load(open('$DEEPDIVE_PLAN'))['papers'][$di].get('slug') or 'paper$di')" 2>/dev/null)
            DD_FOCUS=$($PYTHON -c "import json; print(json.load(open('$DEEPDIVE_PLAN'))['papers'][$di].get('focus_prompt') or '')" 2>/dev/null)
            [[ -z "$DD_SID" ]] && { log "  skip (no source_id): $DD_SLUG"; continue; }
            # Idempotent rerun: don't re-create a podcast whose MP3 already exists
            # (avoids duplicate NLM audio artifacts on the notebook).
            [[ -s "$FEED_DIR/$DATE-deepdive-$DD_SLUG-podcast.mp3" ]] && { log "  cached: $DD_SLUG"; continue; }
            ensure_auth_or_rotate || true
            DD_CREATE=$($NLM audio create "$NOTEBOOK_ID" --source-ids "$DD_SID" --focus "$DD_FOCUS" --format deep_dive --length short --confirm 2>&1)
            # Anchor to the labelled "Artifact ID:" line so we never grab a notebook/trace UUID.
            DD_AID=$(echo "$DD_CREATE" | grep -oiE 'Artifact ID:[[:space:]]*[a-f0-9-]{36}' | grep -oiE '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}' | head -1)
            if [[ -n "$DD_AID" ]]; then
                DD_SLUGS+=("$DD_SLUG"); DD_AIDS+=("$DD_AID"); DD_DEADLINES+=($(( $(date +%s) + 1200 )))
                log "  fired: $DD_SLUG ($DD_AID)"
            else
                log "  FAILED to start: $DD_SLUG"
            fi
            sleep 8
        done
        # 2. Poll by artifact id; download + compress each as it completes
        DD_REMAIN=${#DD_AIDS[@]}
        DD_DONE=0
        while [[ $DD_REMAIN -gt 0 ]]; do
            DD_SJSON=$($NLM studio status "$NOTEBOOK_ID" --json 2>/dev/null)
            # Reactive auth recovery: only rotate when status came back empty (likely
            # cookie expiry over a long poll) — not every tick (avoids rate-limit churn).
            [[ -z "$DD_SJSON" ]] && { ensure_auth_or_rotate || true; }
            DD_REMAIN=0
            for di in "${!DD_AIDS[@]}"; do
                DD_AID="${DD_AIDS[$di]}"; DD_SLUG="${DD_SLUGS[$di]}"
                [[ -z "$DD_AID" ]] && continue   # already handled
                DD_ST=$(printf '%s' "$DD_SJSON" | $PYTHON -c "import json,sys
try:
    d=json.load(sys.stdin); a=d if isinstance(d,list) else d.get('artifacts', d)
    a=a if isinstance(a,list) else [a]
    print(next((x.get('status','') for x in a if x.get('id')=='$DD_AID'), ''))
except Exception:
    print('')" 2>/dev/null)
                if [[ "$DD_ST" == "completed" ]]; then
                    DD_RAW="$FEED_DIR/$DATE-deepdive-$DD_SLUG-podcast-raw.mp4"
                    DD_MP3="$FEED_DIR/$DATE-deepdive-$DD_SLUG-podcast.mp3"
                    $NLM download audio "$NOTEBOOK_ID" --id "$DD_AID" --output "$DD_RAW" 2>/dev/null
                    [[ ! -s "$DD_RAW" ]] && $PYTHON "$PROJECT_ROOT/src/nlm/nlm_download_audio.py" "$NOTEBOOK_ID" --id "$DD_AID" --output "$DD_RAW" 2>/dev/null
                    if [[ -s "$DD_RAW" ]]; then
                        $FFMPEG -y -i "$DD_RAW" -ac 1 -b:a 48k -ar 22050 "$DD_MP3" 2>/dev/null && DD_DONE=$((DD_DONE + 1))
                        log "  OK: $DD_SLUG"
                    else
                        log "  download failed: $DD_SLUG"
                    fi
                    DD_AIDS[$di]=""
                elif [[ $(date +%s) -gt ${DD_DEADLINES[$di]} ]]; then
                    log "  TIMEOUT: $DD_SLUG"
                    DD_AIDS[$di]=""
                else
                    DD_REMAIN=$((DD_REMAIN + 1))
                fi
            done
            [[ $DD_REMAIN -gt 0 ]] && sleep 30
        done
        step_ok "deepdive-podcasts ($DD_DONE/${#DD_SLUGS[@]})"
        echo ""
    fi

fi

# ── Step 7: retired — share links do not survive Step 11 ──────
# A public notebook link stops resolving the moment the notebook is deleted,
# so emitting one into a Telegram message that stays in the user's history
# just manufactures a dead link. The PDF and MP3 are attached directly.
SHARE_LINK=""

# ── Step 8: Deliver via Telegram ───────────────────────────────
log "Step 8: Deliver via Telegram"
if [[ -z "$TELEGRAM_CHAT_ID" ]]; then
    step_fail "telegram (no TELEGRAM_CHAT_ID)"
else
    # Count items in the feed
    ITEM_COUNT=0
    if [[ -f "$MD_FILE" ]]; then
        ITEM_COUNT=$(grep -c '^\- \*\*' "$MD_FILE" 2>/dev/null || echo "0")
    fi

    DAY_NAME=$(date -j -f "%Y-%m-%d" "$DATE" "+%A, %B %d" 2>/dev/null || echo "$DATE")

    # Message 1: Summary text
    SUMMARY_MSG="Daily Tech Feed — $DAY_NAME"
    [[ $ITEM_COUNT -gt 0 ]] && SUMMARY_MSG="$SUMMARY_MSG
$ITEM_COUNT items collected"
    [[ -n "$SHARE_LINK" ]] && SUMMARY_MSG="$SUMMARY_MSG
Notebook: $SHARE_LINK"

    tg_send_message "$SUMMARY_MSG" 2>&1 && step_ok "telegram-summary" || step_fail "telegram-summary"

    # Message 2: HTML magazine (prefer HTML > PDF > NLM report PDF)
    DELIVER_FILE=""
    if [[ -f "$HTML_FILE" ]]; then
        DELIVER_FILE="$HTML_FILE"
    elif [[ -f "$PDF_FILE" ]]; then
        DELIVER_FILE="$PDF_FILE"
    elif [[ -f "$REPORT_PDF" ]]; then
        DELIVER_FILE="$REPORT_PDF"
    fi

    if [[ -n "$DELIVER_FILE" ]]; then
        tg_send_file "$DELIVER_FILE" "Daily Feed Report — $DATE" 2>&1 && \
            step_ok "telegram-report" || step_fail "telegram-report"
    fi

    # Message 3: Podcast (sendAudio for inline player)
    if [[ -f "$PODCAST" ]]; then
        tg_send_audio "$PODCAST" "Daily Feed Podcast — $DATE" 2>&1 && \
            step_ok "telegram-podcast" || step_fail "telegram-podcast"
    fi

    # Message 4: Deep Dive Review PDF (per-paper explainers)
    if [[ -f "$DEEPDIVE_PDF" ]]; then
        tg_send_file "$DEEPDIVE_PDF" "Deep Dive Review — $DATE" 2>&1 && \
            step_ok "telegram-deepdive" || step_fail "telegram-deepdive"
    fi

    # Message 5+: Deep Dive per-paper podcast episodes (Phase 2)
    for DD_MP3 in "$FEED_DIR/$DATE"-deepdive-*-podcast.mp3; do
        [[ -e "$DD_MP3" ]] || continue   # no matches → glob stays literal
        DD_NAME="${DD_MP3##*/}"; DD_NAME="${DD_NAME#"$DATE"-deepdive-}"; DD_NAME="${DD_NAME%-podcast.mp3}"
        tg_send_audio "$DD_MP3" "Deep Dive Podcast — $DD_NAME" 2>&1 && \
            step_ok "telegram-deepdive-podcast" || step_fail "telegram-deepdive-podcast"
    done
fi
echo ""

# ── Step 9: Mark items as processed ────────────────────────────
# daily_feed_generator.py already marks news-queue items as "daily-digested"
# Sources remain "queued" for the weekly deep-dive pipeline
log "Step 9: Items already marked by daily_feed_generator.py"

# ── Step 10: Record in notebooks.json ──────────────────────────
if [[ -n "$NOTEBOOK_ID" ]]; then
    log "Step 10: Update notebooks.json"
    $PYTHON -c "
import json, os
registry_path = '$PROJECT_ROOT/data/notebooks.json'
if os.path.exists(registry_path):
    registry = json.load(open(registry_path))
else:
    registry = {'notebooks': []}

# Skip if alias already exists (avoid duplicates on re-runs)
existing = [nb for nb in registry.get('notebooks', []) if nb.get('alias') == 'daily-$DATE']
if not existing:
  registry.setdefault('notebooks', []).append({
    'id': '$NOTEBOOK_ID',
    'alias': 'daily-$DATE',
    'category': 'daily',
    'category_name': 'Daily Feed',
    'created_at': '${DATE}T18:00:00Z',
    'source_count': ${ADDED:-0},
    'source_ids': [],
    'has_podcast': True,
    'has_report': True,
    'status': 'complete',
    'run_id': 'daily_${DATE//-/}',
  })

with open(registry_path, 'w') as f:
    json.dump(registry, f, indent=2)
    f.write('\n')
print('  Notebook registered')
" 2>&1 && step_ok "notebooks-json" || step_fail "notebooks-json"
fi

# ── Step 10.5: Index report into RAG (after notebook registration) ──
if [[ -n "$NOTEBOOK_ID" ]] && [[ -f "$REPORT_MD" ]] && [[ -f "$PROJECT_ROOT/src/pipeline/index_notebook.py" ]]; then
    log "Step 10.5: Index report in RAG"
    $PYTHON "$PROJECT_ROOT/src/pipeline/index_notebook.py" \
        --notebook-id "$NOTEBOOK_ID" --report-file "$REPORT_MD" 2>&1 && \
        step_ok "rag-index" || step_fail "rag-index"
    echo ""
fi

# ── Step 11: Capture the notebook to the archive, then delete it ──
# Everything the notebook was for has already been generated, downloaded and
# delivered by this point. Capturing and deleting keeps the account footprint
# flat instead of growing by one notebook a day until create fails.
if ! $SKIP_NLM && [[ -n "$NOTEBOOK_ID" ]]; then
    log "Step 11: Archive notebook and release the slot"
    $PYTHON "$PROJECT_ROOT/src/pipeline/daily_capture_delete.py" \
        --notebook-id "$NOTEBOOK_ID" \
        --run-id "daily_$DATE" \
        --alias "daily-$DATE" \
        --title "Daily Feed — $DATE" 2>&1 && \
        step_ok "notebook-archive" || step_fail "notebook-archive"
    echo ""
fi

# ── Summary ────────────────────────────────────────────────────
echo ""
PIPELINE_END=$(date +%s)
PIPELINE_DURATION=$((PIPELINE_END - PIPELINE_START))
log_json "pipeline_end" "daily" "$([ ${#ERRORS[@]} -eq 0 ] && echo ok || echo fail)" "$((PIPELINE_DURATION * 1000))"

log "=== Pipeline Complete ==="
if [[ ${#ERRORS[@]} -eq 0 ]]; then
    log "All steps completed successfully"
else
    log "Errors in ${#ERRORS[@]} step(s):"
    for err in "${ERRORS[@]}"; do
        log "  - $err"
    done
    alert "Daily pipeline completed with ${#ERRORS[@]} error(s): ${ERRORS[*]}"
fi

# Write run record to processing-log.json
$PYTHON -c "
import sys; sys.path.insert(0, '$PROJECT_ROOT/src')
from lib.file_utils import load_json, save_json, now_iso
log = load_json('$PROJECT_ROOT/data/processing-log.json')
log.setdefault('runs', []).append({
    'run_id': 'daily_$DATE',
    'pipeline': 'daily',
    'started_at': '$(date -u -r $PIPELINE_START +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)',
    'completed_at': now_iso(),
    'duration_s': $PIPELINE_DURATION,
    'errors': $(python3 -c "import json; print(json.dumps('${ERRORS[*]}'.split() if '${ERRORS[*]}' else []))"),
    'nlm_skipped': $($SKIP_NLM && echo True || echo False),
})
save_json('$PROJECT_ROOT/data/processing-log.json', log)
" 2>/dev/null || true
