---
status: complete
priority: p1
issue_id: "034"
tags: [code-review, data-integrity, operations]
dependencies: []
---

# `backup.sh` exits 1 today — the Phase 0 backup gate cannot pass

## Problem Statement

Phase 0 gates irreversible deletion on "`backup.sh` run and verified restorable." Verified: the script **fails**, silently, and covers none of the data this plan creates.

## Findings

- `backup.sh:107`:
  ```bash
  cp $SCRIPT_DIR/src/pipeline/*.py" "$SCRIPT_DIR/src/nlm/*.py" "$SCRIPT_DIR/src/rag/*.py "$BACKUP_DIR/scripts/" 2>/dev/null
  ```
  The quotes pair such that the three globs collapse into one shell word with literal spaces. Nothing expands, `cp` fails, `2>/dev/null` hides it, `set -euo pipefail` aborts.
- Verified empirically: `EXIT=1`; output stops after `pyproject.toml`; templates, `data/daily-feed/`, and config are never copied; "Backup complete" never prints. An operator sees plausible output and a silent failure.
- The data loop is a hardcoded 7-file list covering none of: `data/research.db` (+`-wal`/`-shm`), `data/archive/`, the allowlist/excluded files, `deleted-ids.log`.
- Default destination is `$SCRIPT_DIR/backups/` — on `/Volumes/ext`, the same device whose unmount is the plan's own risk R3.
- `backups/` and `data/` are both gitignored (`.gitignore:9,29`). The only backup on disk is 100 KB from 2026-02-16.
- `cp` of a live WAL SQLite is corruption-prone; `restore.sh` restores the same hardcoded set.

## Proposed Solutions

1. **Fix and extend before Phase 0 is declared met (recommended).** Repair line 107; add the new paths; use `sqlite3 .backup` / `VACUUM INTO` for both `research.db` and `chroma.sqlite3`; default the destination **off** `/Volumes/ext`; add a restore drill that restores to a scratch dir and asserts row/file counts. Gate on the drill, not on exit 0.
2. Skip the backup gate. Unacceptable for an irreversible 465-notebook operation.

## Acceptance Criteria

- [ ] `backup.sh` exits 0 and prints its completion summary
- [ ] Covers `research.db`, `data/archive/`, allowlist/excluded, `deleted-ids.log`
- [ ] Both SQLite DBs backed up via a consistent method, not `cp`
- [ ] Destination defaults off `/Volumes/ext`
- [ ] Restore drill asserts counts; `restore.sh` updated
- [ ] `backup.sh`/`restore.sh` added to the plan's Modified table (CLAUDE.md mandates this for new scripts)
