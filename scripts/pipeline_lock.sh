#!/bin/bash
# ─────────────────────────────────────────────────────────────
# pipeline_lock.sh — PID-liveness pipeline locking
#
#   source scripts/pipeline_lock.sh
#   acquire_pipeline_lock "$PROJECT_ROOT/data/.pipeline.lock" "daily" || exit 1
#
# Replaces the previous mkdir-based lockdir, which had three defects:
#
#   1. Nothing was written inside the lockdir, so "is the holder alive?" could
#      not be asked — staleness was guessed from the directory's mtime.
#   2. Stale recovery was `rmdir` then `mkdir`, so two processes could both
#      observe the lock as stale and both succeed.
#   3. Every process installed `trap 'rmdir "$LOCKDIR"' EXIT` unconditionally,
#      so a process whose lock had been reclaimed would delete the *new*
#      holder's lock on its way out.
#
# shlock(1) (BSD, present on macOS) writes the holder's PID and atomically
# takes over only when that PID is gone. Liveness, not a clock — which matters
# because a reclaim run over hundreds of notebooks legitimately outlives any
# fixed timeout, and there are irreversible deletes on one side of this race.
# ─────────────────────────────────────────────────────────────

_PIPELINE_LOCK_FILE=""

acquire_pipeline_lock() {
    local lock_file="$1"
    local label="${2:-pipeline}"

    mkdir -p "$(dirname "$lock_file")"

    # Migrate away from the old lockdir if one is lying around.
    if [[ -d "$lock_file" ]]; then
        rmdir "$lock_file" 2>/dev/null || {
            echo "ERROR: $lock_file is a non-empty directory (old lock format)" >&2
            return 1
        }
    fi

    if command -v shlock >/dev/null 2>&1; then
        if ! shlock -f "$lock_file" -p $$; then
            local holder
            holder=$(cat "$lock_file" 2>/dev/null | tr -d '[:space:]')
            echo "ERROR: $label lock held by live PID ${holder:-unknown} ($lock_file)" >&2
            return 1
        fi
    else
        # Portable fallback with the same semantics: atomic create, then
        # liveness-check the recorded PID.
        if ! ( set -o noclobber; echo $$ > "$lock_file" ) 2>/dev/null; then
            local holder
            holder=$(cat "$lock_file" 2>/dev/null | tr -d '[:space:]')
            if [[ -n "$holder" ]] && kill -0 "$holder" 2>/dev/null; then
                echo "ERROR: $label lock held by live PID $holder ($lock_file)" >&2
                return 1
            fi
            echo "WARNING: reclaiming lock from dead PID ${holder:-unknown}" >&2
            rm -f "$lock_file"
            ( set -o noclobber; echo $$ > "$lock_file" ) 2>/dev/null || {
                echo "ERROR: cannot acquire $label lock" >&2; return 1; }
        fi
    fi

    _PIPELINE_LOCK_FILE="$lock_file"
    trap 'release_pipeline_lock' EXIT
    return 0
}

# Release only a lock this process actually holds. Never blindly remove the
# file — that is how the old trap destroyed other processes' locks.
release_pipeline_lock() {
    [[ -n "$_PIPELINE_LOCK_FILE" ]] || return 0
    local holder
    holder=$(cat "$_PIPELINE_LOCK_FILE" 2>/dev/null | tr -d '[:space:]')
    if [[ "$holder" == "$$" ]]; then
        rm -f "$_PIPELINE_LOCK_FILE"
    fi
    _PIPELINE_LOCK_FILE=""
}
