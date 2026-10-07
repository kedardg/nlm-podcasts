#!/usr/bin/env bash
# Weekly Research Digest Pipeline
#
# Runs all steps of the weekly pipeline in sequence.
# Each step continues on failure (never halts the pipeline).
#
# Usage:
#   bash scripts/weekly_pipeline.sh              # Full pipeline
#   bash scripts/weekly_pipeline.sh --skip-nlm   # Skip NotebookLM steps
#   bash scripts/weekly_pipeline.sh --dry-run    # Preview only

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
DATE=$(date +%Y-%m-%d)
SKIP_NLM=false
DRY_RUN=false
ERRORS=()

for arg in "$@"; do
    case "$arg" in
        --skip-nlm) SKIP_NLM=true ;;
        --dry-run) DRY_RUN=true ;;
    esac
done

PIPELINE_START=$(date +%s)

log() { echo "[$(date +%H:%M:%S)] $*"; }
step_ok() { log "  ✓ $1"; }
step_fail() { log "  ✗ $1"; ERRORS+=("$1"); }
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

ensure_auth_or_rotate() {
    $NLM login --check 2>/dev/null && return 0
    log "  Auth expired, rotating..."
    $PYTHON "$PROJECT_ROOT/src/nlm/nlm_rotate_cookies.py" --verify 2>/dev/null && return 0
    log "  Rotation failed, trying CDP..."
    $PYTHON "$PROJECT_ROOT/src/nlm/nlm_rotate_cookies.py" --cdp --verify 2>/dev/null && return 0
    return 1
}

# ── Pipeline lock (prevent daily + weekly from running simultaneously) ──
LOCKDIR="$PROJECT_ROOT/data/.pipeline.lock"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    # Check if stale (older than 2 hours)
    if [[ -d "$LOCKDIR" ]]; then
        LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$LOCKDIR" 2>/dev/null || stat -f %m "$LOCKDIR" 2>/dev/null || echo 0) ))
        if [[ $LOCK_AGE -gt 7200 ]]; then
            log "WARNING: Removing stale lock (${LOCK_AGE}s old)"
            rmdir "$LOCKDIR" 2>/dev/null
            mkdir "$LOCKDIR" 2>/dev/null || { log "ERROR: Cannot acquire lock"; exit 1; }
        else
            log "ERROR: Another pipeline is running (lock: $LOCKDIR, age: ${LOCK_AGE}s)"
            exit 1
        fi
    fi
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

log "═══ Weekly Research Digest Pipeline — $DATE ═══"
log "Project: $PROJECT_ROOT"
log "Skip NLM: $SKIP_NLM | Dry Run: $DRY_RUN"
echo ""

# ── Step 0: Archive old news ─────────────────────────────────
log "Step 0: Archive old news (>30 days)"
$PYTHON "$PROJECT_ROOT/src/pipeline/archive_old_news.py" --days 30 2>&1 && step_ok "archive-old-news" || step_fail "archive-old-news"
echo ""

# ── Step 1: Newsletter Monitor ────────────────────────────────
log "Step 1: Newsletter Monitor"
if $DRY_RUN; then
    $PYTHON "$PROJECT_ROOT/src/newsletter_parser.py" --check 2>&1 && step_ok "newsletter-monitor (dry run)" || step_fail "newsletter-monitor"
else
    $PYTHON "$PROJECT_ROOT/src/newsletter_parser.py" 2>&1 && step_ok "newsletter-monitor" || step_fail "newsletter-monitor"
fi
echo ""

# ── Step 1.5: Blog Monitor ───────────────────────────────────
log "Step 1.5: Blog Monitor"
if $DRY_RUN; then
    $PYTHON "$PROJECT_ROOT/src/blog_monitor.py" --check 2>&1 && step_ok "blog-monitor (dry run)" || step_fail "blog-monitor"
else
    $PYTHON "$PROJECT_ROOT/src/blog_monitor.py" 2>&1 && step_ok "blog-monitor" || step_fail "blog-monitor"
fi
echo ""

# ── Step 2: X/Twitter Monitor ─────────────────────────────────
log "Step 2: X/Twitter Monitor"
# x-monitor requires the aisa-twitter-api skill via OpenClaw agent
# For now, just check if accounts exist
if [[ -f "$PROJECT_ROOT/data/accounts.json" ]]; then
    ACTIVE=$($PYTHON -c "
import json
with open('$PROJECT_ROOT/data/accounts.json') as f:
    d = json.load(f)
accts = [a for a in d.get('accounts', []) if a.get('status') == 'active']
print(len(accts))
" 2>/dev/null)
    if [[ "$ACTIVE" -gt 0 ]]; then
        log "  $ACTIVE active accounts — x-monitor requires OpenClaw agent session (skipping in script mode)"
    else
        log "  No active accounts to scrape"
    fi
else
    log "  No accounts.json found"
fi
step_ok "x-monitor (checked)"
echo ""

# ── Step 2.5: HF Triage (content classification) ─────────────
log "Step 2.5: HF Triage"
if $DRY_RUN; then
    $PYTHON "$PROJECT_ROOT/src/pipeline/hf_triage.py" --dry-run --no-llm 2>&1 && step_ok "hf-triage (dry run)" || step_fail "hf-triage"
else
    $PYTHON "$PROJECT_ROOT/src/pipeline/hf_triage.py" 2>&1 && step_ok "hf-triage" || step_fail "hf-triage"
fi
echo ""

# ── Step 3: Data Triage ───────────────────────────────────────
log "Step 3: Data Triage"
if $DRY_RUN; then
    $PYTHON "$PROJECT_ROOT/src/data_triage.py" --dry-run 2>&1 && step_ok "data-triage (dry run)" || step_fail "data-triage"
else
    $PYTHON "$PROJECT_ROOT/src/data_triage.py" --auto 2>&1 && step_ok "data-triage" || step_fail "data-triage"
fi
echo ""

# ── Step 4: Content Processor ─────────────────────────────────
log "Step 4: Content Processor"
if $DRY_RUN; then
    $PYTHON "$PROJECT_ROOT/src/pipeline/content_processor.py" --dry-run 2>&1 && step_ok "content-processor (dry run)" || step_fail "content-processor"
else
    $PYTHON "$PROJECT_ROOT/src/pipeline/content_processor.py" 2>&1 && step_ok "content-processor" || step_fail "content-processor"
fi
echo ""

# ── Step 5: NotebookLM Orchestrator ───────────────────────────
log "Step 5: NotebookLM Orchestrator"
if $SKIP_NLM || $DRY_RUN; then
    log "  Skipped (--skip-nlm or --dry-run)"
else
    # Check NLM auth
    if [[ -f "$PROJECT_ROOT/scripts/nlm_ensure_auth.sh" ]]; then
        bash "$PROJECT_ROOT/scripts/nlm_ensure_auth.sh" 2>&1
        NLM_AUTH=$?
    else
        $NLM login --check 2>/dev/null
        NLM_AUTH=$?
    fi

    if [[ $NLM_AUTH -ne 0 ]]; then
        step_fail "nlm-auth (NotebookLM not authenticated — skipping NLM steps)"
        alert "⚠️ Weekly pipeline: NLM auth failed, skipping notebook creation"
        SKIP_NLM=true
    else
        step_ok "nlm-auth"

        # Read content-processor output manifest
        MANIFEST="$PROJECT_ROOT/data/content-processor-output.json"
        if [[ ! -f "$MANIFEST" ]]; then
            step_fail "orchestrator (no content-processor output manifest)"
        else
            # Process each group: create notebook, add sources, generate artifacts
            $PYTHON -c "
import json, subprocess, sys, os, time, re

manifest = json.load(open('$MANIFEST'))
sources_data = json.load(open('$PROJECT_ROOT/data/sources.json'))
notebooks_registry = json.load(open('$PROJECT_ROOT/data/notebooks.json')) if os.path.exists('$PROJECT_ROOT/data/notebooks.json') else {'notebooks': []}
nlm = '$NLM'
project = '$PROJECT_ROOT'
date = '$DATE'

src_map = {s['id']: s for s in sources_data.get('sources', [])}
created = []
used_slugs = {}

for group_name, info in manifest.get('groups', {}).items():
    # Include 'processing' too — content_processor marks sources as 'processing' after grouping
    queued_ids = [sid for sid in info.get('source_ids', [])
                  if src_map.get(sid, {}).get('status') in ('queued', 'daily-digested', 'processing')]
    if not queued_ids:
        print(f'\\n--- {group_name}: no queued sources, skipping ---')
        continue
    info['source_ids'] = queued_ids
    info['source_count'] = len(queued_ids)
    print(f'\\n--- {group_name} ({info[\"source_count\"]} sources) ---')
    # Sanitize: split names like 'NLP (Part 1/20)' otherwise leave '/' and '('
    # in the slug, corrupting file paths. Dedup: 30-char truncation collides
    # between parts, silently overwriting each other's files.
    slug = re.sub(r'[^a-z0-9._-]+', '-', group_name.lower().replace('&', 'and')).strip('-')[:30]
    used_slugs[slug] = used_slugs.get(slug, 0) + 1
    if used_slugs[slug] > 1:
        slug = f'{slug[:26]}-c{used_slugs[slug]}'
    alias = f'{slug}-{date}'

    # Create notebook
    result = subprocess.run([nlm, 'notebook', 'create', f'{group_name} - Week of {date}'],
                          capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f'  FAIL: notebook create: {result.stderr[:200]}')
        continue

    # Extract notebook ID from output
    nb_id = None
    for line in result.stdout.splitlines():
        if 'id' in line.lower() and ('notebook' in line.lower() or len(line.strip()) > 20):
            # Try to find UUID pattern
            import re
            match = re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', line)
            if match:
                nb_id = match.group(0)
                break
    if not nb_id:
        # Try parsing JSON output
        try:
            out = json.loads(result.stdout)
            nb_id = out.get('notebook_id') or out.get('id') or out.get('notebookId')
        except:
            pass
    if not nb_id:
        print(f'  FAIL: could not extract notebook ID from: {result.stdout[:200]}')
        continue

    print(f'  Notebook: {nb_id}')
    subprocess.run([nlm, 'alias', 'set', alias, nb_id], capture_output=True, timeout=10)

    # Add sources
    added = 0
    for sid in info.get('source_ids', []):
        src = src_map.get(sid, {})
        url = src.get('url', '')
        src_type = src.get('type', 'url')
        if not url:
            continue

        if src_type == 'youtube':
            cmd = [nlm, 'source', 'add', nb_id, '--youtube', url, '--wait']
        elif src.get('file_path'):
            cmd = [nlm, 'source', 'add', nb_id, '--file', src['file_path'], '--wait']
        else:
            cmd = [nlm, 'source', 'add', nb_id, '--url', url, '--wait']

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            added += 1
        else:
            print(f'  WARN: source add failed for {url[:60]}: {r.stderr[:100]}')

    print(f'  Added {added}/{len(info.get(\"source_ids\", []))} sources')

    # Add companion note
    note_path = info.get('companion_note_path', '')
    if note_path and os.path.exists(note_path):
        note_text = open(note_path).read()
        r = subprocess.run([nlm, 'source', 'add', nb_id, '--text', note_text,
                          '--title', f'{group_name} Digest Notes', '--wait'],
                         capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            print(f'  Companion note added')

    # Generate podcast
    focus_file = f'{project}/config/prompts/weekly-podcast-focus.txt'
    focus = open(focus_file).read().strip() if os.path.exists(focus_file) else ''
    podcast_cmd = [nlm, 'audio', 'create', nb_id, '--format', 'deep_dive', '--length', 'default', '--confirm']
    if focus:
        podcast_cmd.extend(['--focus', focus])
    r = subprocess.run(podcast_cmd, capture_output=True, text=True, timeout=30)
    podcast_ok = r.returncode == 0
    print(f'  Podcast: {\"requested\" if podcast_ok else \"FAILED\"}')

    # Generate report
    report_script = f'{project}/scripts/nlm_create_report.sh'
    if os.path.exists(report_script):
        r = subprocess.run(['bash', report_script, nb_id], capture_output=True, text=True, timeout=60)
        print(f'  Report: {\"requested\" if r.returncode == 0 else \"FAILED\"}')

    created.append({
        'notebook_id': nb_id,
        'alias': alias,
        'group_name': group_name,
        'source_count': added,
    })

    # Update notebook registry
    notebooks_registry.setdefault('notebooks', []).append({
        'id': nb_id, 'alias': alias,
        'category': slug, 'category_name': group_name,
        'created_at': '${DATE}T00:00:00Z',
        'source_count': added,
        'source_ids': info.get('source_ids', []),
        'has_podcast': podcast_ok, 'has_report': True,
        'status': 'generating', 'run_id': f'weekly_{date}',
    })

# Save notebook registry
json.dump(notebooks_registry, open(f'{project}/data/notebooks.json', 'w'), indent=2)

print(f'\\n=== Orchestrator complete: {len(created)} notebooks created ===')
for nb in created:
    print(f'  {nb[\"group_name\"]}: {nb[\"notebook_id\"]} ({nb[\"source_count\"]} sources)')
" 2>&1

            if [[ $? -eq 0 ]]; then
                step_ok "orchestrator"
            else
                step_fail "orchestrator"
            fi
        fi
    fi
fi
echo ""

# ── Step 5.5: Magazine Generator ─────────────────────────────
log "Step 5.5: Magazine Generator"
if $DRY_RUN; then
    DYLD_FALLBACK_LIBRARY_PATH="${DYLD_FALLBACK_LIBRARY_PATH:-}:/opt/homebrew/lib" \
        $PYTHON "$PROJECT_ROOT/src/pipeline/magazine_generator.py" --date "$DATE" --dry-run --no-openai 2>&1 \
        && step_ok "magazine-generator (dry run)" || step_fail "magazine-generator"
else
    DYLD_FALLBACK_LIBRARY_PATH="${DYLD_FALLBACK_LIBRARY_PATH:-}:/opt/homebrew/lib" \
        $PYTHON "$PROJECT_ROOT/src/pipeline/magazine_generator.py" --date "$DATE" 2>&1 \
        && step_ok "magazine-generator" || step_fail "magazine-generator"
fi
echo ""

# ── Step 6: News Digest ──────────────────────────────────────
log "Step 6: News Digest"
if $SKIP_NLM || $DRY_RUN; then
    log "  Skipped (--skip-nlm or --dry-run)"
else
    # Count queued news articles
    NEWS_COUNT=$($PYTHON -c "
import json
with open('$PROJECT_ROOT/data/news-queue.json') as f:
    d = json.load(f)
queued = [a for a in d.get('articles', []) if a.get('status') == 'queued']
print(len(queued))
" 2>/dev/null || echo "0")

    if [[ "$NEWS_COUNT" -lt 3 ]]; then
        log "  Not enough news articles for digest (need 3, have $NEWS_COUNT)"
        step_ok "news-digest (skipped — insufficient articles)"
    else
        log "  $NEWS_COUNT queued articles — creating news digest notebook"
        # News digest requires agent logic for briefing note creation
        # Mark articles so they're not re-processed
        log "  News digest requires agent session for briefing note creation (skipping in script mode)"
        step_ok "news-digest (deferred to agent)"
    fi
fi
echo ""

# ── Step 7: Digest Deliver ────────────────────────────────────
log "Step 7: Digest Deliver"
if $SKIP_NLM || $DRY_RUN; then
    log "  Skipped (--skip-nlm or --dry-run)"
else
    # Poll studio status for all created notebooks, download artifacts, deliver
    log "  Waiting for artifact generation (polling every 30s, max 10 min)..."

    # Refresh auth before polling/download phase
    $PYTHON "$PROJECT_ROOT/src/nlm/nlm_rotate_cookies.py" --verify 2>&1 || true

    MANIFEST="$PROJECT_ROOT/data/content-processor-output.json"
    if [[ -f "$PROJECT_ROOT/data/notebooks.json" ]]; then
        $PYTHON -c "
import json, subprocess, os, time

project = '$PROJECT_ROOT'
nlm = '$NLM'
chat_id = '$TELEGRAM_CHAT_ID'
bot_token = '$TELEGRAM_BOT_TOKEN'

def tg_send(method, file_path, field, caption):
    \"\"\"Send file via Telegram Bot API using curl.\"\"\"
    if not (chat_id and bot_token):
        return False
    try:
        r = subprocess.run([
            'curl', '-s', '-w', '\\n%{http_code}',
            '-X', 'POST', f'https://api.telegram.org/bot{bot_token}/{method}',
            '-F', f'chat_id={chat_id}',
            '-F', f'{field}=@{file_path}',
            '-F', f'caption={caption}',
        ], capture_output=True, text=True, timeout=120)
        http_code = r.stdout.strip().split('\\n')[-1]
        return http_code.startswith('2')
    except Exception as e:
        print(f'  Telegram error: {e}')
        return False
notebooks = json.load(open(f'{project}/data/notebooks.json'))
all_reports = []  # (category_name, markdown_text) for consolidated report

# Scope delivery to THIS run only. Prior runs that died before flipping
# their 'generating' entries to 'complete' would otherwise get re-delivered,
# polluting this week's digest with stale content.
current_run_id = 'weekly_$DATE'
for nb in notebooks.get('notebooks', []):
    if nb.get('status') == 'generating' and nb.get('run_id') != current_run_id:
        nb['status'] = 'abandoned'

recent = [nb for nb in notebooks.get('notebooks', [])
          if nb.get('status') == 'generating' and nb.get('run_id') == current_run_id]
if not recent:
    print('No notebooks pending delivery for run', current_run_id)
    exit(0)

# Poll for completion (max 10 minutes)
max_wait = 600
waited = 0
auth_failures = 0
while waited < max_wait:
    all_done = True
    for nb in recent:
        r = subprocess.run([nlm, 'studio', 'status', nb['id']],
                          capture_output=True, text=True, timeout=30)
        if r.returncode != 0 or 'authentication' in r.stderr.lower() or 'no auth' in r.stderr.lower():
            auth_failures += 1
            if auth_failures <= 2:
                print(f'  Auth expired — rotating cookies...')
                subprocess.run([f'{project}/.venv/bin/python3', f'{project}/src/nlm/nlm_rotate_cookies.py', '--verify'],
                              capture_output=True, text=True, timeout=30)
            all_done = False
            break
        if 'in_progress' in r.stdout.lower() or 'generating' in r.stdout.lower():
            all_done = False
    if all_done:
        break
    print(f'  Still generating... ({waited}s elapsed)')
    time.sleep(30)
    waited += 30

# Download and deliver each notebook's artifacts
for nb in recent:
    slug = nb.get('alias', nb['id'][:8])
    print(f'\\nDelivering: {nb[\"category_name\"]} ({slug})')

    # Download podcast: nlm CLI first (works on 0.9.6+; retry while the media
    # URL propagates), then the CDP redirect-following workaround as fallback.
    # Skip entirely if podcast creation failed (e.g. NLM audio quota) — retrying
    # a download that can never exist wasted ~6 min per notebook.
    podcast_raw = f'{project}/data/{slug}-podcast-raw.mp4'
    podcast = f'{project}/data/{slug}-podcast.mp3'
    if not nb.get('has_podcast', True):
        print(f'  Podcast: skipped (creation failed)')
    else:
        for attempt in range(4):
            r = subprocess.run([nlm, 'download', 'audio', nb['id'], '--output', podcast_raw],
                              capture_output=True, text=True, timeout=600)
            if os.path.exists(podcast_raw) and os.path.getsize(podcast_raw) > 0:
                break
            if attempt < 3:
                print(f'  Podcast: download not ready — retrying in 120s...')
                time.sleep(120)
        if not (os.path.exists(podcast_raw) and os.path.getsize(podcast_raw) > 0):
            r = subprocess.run([f'{project}/.venv/bin/python3', f'{project}/src/nlm/nlm_download_audio.py',
                               nb['id'], '--output', podcast_raw],
                              capture_output=True, text=True, timeout=600)
    if os.path.exists(podcast_raw) and os.path.getsize(podcast_raw) > 0:
        # Compress for Telegram
        ffmpeg = '/opt/homebrew/bin/ffmpeg' if os.path.exists('/opt/homebrew/bin/ffmpeg') else 'ffmpeg'
        subprocess.run([ffmpeg, '-y', '-i', podcast_raw, '-ac', '1', '-b:a', '48k', '-ar', '22050', podcast],
                      capture_output=True, timeout=120)
        print(f'  Podcast: downloaded + compressed')
    else:
        print(f'  Podcast: download failed')

    # Download report
    report_md = f'{project}/data/{slug}-report.md'
    r = subprocess.run([nlm, 'download', 'report', nb['id'], '--output', report_md],
                      capture_output=True, text=True, timeout=60)
    if r.returncode == 0 and os.path.exists(report_md):
        print(f'  Report: downloaded')
        all_reports.append((nb['category_name'], open(report_md).read()))
        # Render PDF
        report_pdf = f'{project}/data/{slug}-report.pdf'
        os.environ['DYLD_FALLBACK_LIBRARY_PATH'] = os.environ.get('DYLD_FALLBACK_LIBRARY_PATH', '') + ':/opt/homebrew/lib'
        r2 = subprocess.run([f'{project}/.venv/bin/python3', f'{project}/src/lib/pdf_renderer.py',
                           '--input', report_md, '--output', report_pdf],
                          capture_output=True, text=True, timeout=60)
        if r2.returncode == 0:
            print(f'  PDF: rendered')

    # Get share link
    r = subprocess.run([nlm, 'share', 'public', nb['id']],
                      capture_output=True, text=True, timeout=15)
    share_link = ''
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            if 'http' in line:
                share_link = line.strip()
                break

    # Index in ChromaDB
    subprocess.run([f'{project}/.venv/bin/python3', f'{project}/src/pipeline/index_notebook.py',
                   '--notebook-id', nb['id']],
                  capture_output=True, text=True, timeout=60)

    # Send podcast via Telegram (per-category, individual delivery)
    if os.path.exists(podcast):
        if tg_send('sendAudio', podcast, 'audio', f'{nb[\"category_name\"]} Podcast'):
            print(f'  Telegram: podcast sent')
        else:
            print(f'  Telegram: podcast failed')

    # Update notebook status
    nb['status'] = 'complete'

# --- Consolidated weekly report (HTML-first) ---
if all_reports:
    import re
    from datetime import date as _date
    week_date = _date.today().strftime('%Y-%m-%d')

    parts = [f'# Weekly Research Digest \\u2014 {week_date}\\n']
    for cat_name, text in all_reports:
        parts.append(f'\\n---\\n\\n## {cat_name}\\n')
        cleaned = re.sub(r'^#\\s+.*$', '', text, count=1, flags=re.MULTILINE).strip()
        parts.append(cleaned)
    consolidated_md = '\\n\\n'.join(parts)

    md_path = f'{project}/data/weekly-report-{week_date}.md'
    html_path = f'{project}/data/weekly-report-{week_date}.html'
    pdf_path = f'{project}/data/weekly-report-{week_date}.pdf'

    with open(md_path, 'w') as f:
        f.write(consolidated_md)

    env = {**os.environ, 'DYLD_FALLBACK_LIBRARY_PATH':
           os.environ.get('DYLD_FALLBACK_LIBRARY_PATH', '') + ':/opt/homebrew/lib'}

    # Render PDF (with TOC)
    subprocess.run(
        [f'{project}/.venv/bin/python3', f'{project}/src/lib/pdf_renderer.py',
         '--input', md_path, '--output', pdf_path],
        capture_output=True, text=True, timeout=120, env=env)

    # Render standalone HTML
    try:
        from markdown_it import MarkdownIt
        html_body = MarkdownIt().enable('table').render(consolidated_md)
        css = open(f'{project}/templates/research-report.css').read()
        full_html = f'<!DOCTYPE html><html><head><meta charset=\"utf-8\"><style>{css}</style></head><body>{html_body}</body></html>'
        with open(html_path, 'w') as f:
            f.write(full_html)
    except Exception as e:
        print(f'  HTML render failed: {e}')

    # Deliver: HTML primary, PDF fallback
    delivered = False
    if os.path.exists(html_path):
        delivered = tg_send('sendDocument', html_path, 'document',
                           f'Weekly Research Digest \\u2014 {week_date}')
    if not delivered and os.path.exists(pdf_path):
        delivered = tg_send('sendDocument', pdf_path, 'document',
                           f'Weekly Research Digest \\u2014 {week_date}')
    print(f'\\nConsolidated report: {len(all_reports)} categories' +
          (' \\u2014 delivered' if delivered else ' \\u2014 delivery failed'))

# Save updated registry
json.dump(notebooks, open(f'{project}/data/notebooks.json', 'w'), indent=2)
print('\\n=== Delivery complete ===')
" 2>&1

        if [[ $? -eq 0 ]]; then
            step_ok "digest-deliver"
        else
            step_fail "digest-deliver"
        fi
    else
        log "  No notebooks.json found"
        step_fail "digest-deliver (no notebooks)"
    fi
fi
echo ""

# ── Summary ───────────────────────────────────────────────────
PIPELINE_END=$(date +%s)
PIPELINE_DURATION=$((PIPELINE_END - PIPELINE_START))
log_json "pipeline_end" "weekly" "$([ ${#ERRORS[@]} -eq 0 ] && echo ok || echo fail)" "$((PIPELINE_DURATION * 1000))"

log "═══ Pipeline Complete ═══"
if [[ ${#ERRORS[@]} -eq 0 ]]; then
    log "All steps completed successfully"
else
    log "Errors in ${#ERRORS[@]} step(s):"
    for err in "${ERRORS[@]}"; do
        log "  • $err"
    done
    alert "⚠️ Weekly pipeline completed with ${#ERRORS[@]} error(s): ${ERRORS[*]}"
fi

# Write run record to processing-log.json
$PYTHON -c "
import sys; sys.path.insert(0, '$PROJECT_ROOT/src')
from lib.file_utils import load_json, save_json, now_iso
log = load_json('$PROJECT_ROOT/data/processing-log.json')
log.setdefault('runs', []).append({
    'run_id': 'weekly_$DATE',
    'pipeline': 'weekly',
    'started_at': '$(date -u -r $PIPELINE_START +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)',
    'completed_at': now_iso(),
    'duration_s': $PIPELINE_DURATION,
    'errors': $(python3 -c "import json; print(json.dumps('${ERRORS[*]}'.split() if '${ERRORS[*]}' else []))"),
    'nlm_skipped': $($SKIP_NLM && echo True || echo False),
})
save_json('$PROJECT_ROOT/data/processing-log.json', log)
" 2>/dev/null || true
