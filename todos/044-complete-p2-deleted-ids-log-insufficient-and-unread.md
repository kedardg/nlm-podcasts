---
status: complete
priority: p2
issue_id: "044"
tags: [code-review, data-integrity, operations]
dependencies: []
---

# `deleted-ids.log` cannot reconstruct what was destroyed, and nothing reads it

## Problem Statement

The log exists to survive "delete succeeded, commit failed" — the exact case where the DB is unavailable. An id alone then tells you *that* something was destroyed, not *what*. It is also specified inside gitignored, unbacked `data/`, on the same volume risk R3 says may unmount, next to the data it is meant to outlive. And the plan never says what reads it or what happens on divergence.

## Findings

- Plan: `data/archive/deleted-ids.log`, append-only, fsynced before the delete call.
- No reconciliation pass is defined. An append-only log nobody reads is a comfort blanket, not a control.
- `fsync()` on macOS does not flush the drive write cache — `F_FULLFSYNC` is required, and `PRAGMA fullfsync` defaults to 0 (verified). Appending to a new file and fsyncing the fd also does not fsync the parent directory entry.
- Anything with write access can truncate the log, including a buggy rerun.

## Proposed Solutions

1. **Self-contained records, off-volume mirror, defined reader (recommended).** Each line a fsynced JSON record written before the delete: `{ts, notebook_id, title, alias, source_count, source_urls_sha256, artifact_ids[], artifact_shas[], capture_completeness, allowlist_digest, run_id}`. Mode 0600, `O_APPEND`, mirrored to a second filesystem (needed for the backup gap anyway). Define a reconciliation pass that runs at startup and reports divergence from the DB. Add a `capture_completeness` column (`full|sources_only|nothing`); `nothing` requires an explicit `--allow-empty-artifacts` and appears in the pre-run manifest.

## Partial progress (2026-09-29)

Shipped in `src/pipeline/notebook_capture.py::_log_deletion`:
- Records are self-contained JSON, not bare ids: ts, notebook_id, title, alias,
  source_count, source_urls_sha256, artifact_ids, artifact_shas,
  capture_completeness, allowlist_digest, run_id.
- Written `O_APPEND`, mode 0600, fsynced, **before** the delete call.
- Mirrored to `~/openclaw-research-backups/`, off the external volume.
- `capture_completeness` is also a column on `notebooks`.

Reconciliation shipped as `src/pipeline/archive_reconcile.py`: compares the
log, the database and the live account, and names every divergence. `--repair`
corrects a row only on three independent pieces of evidence — logged before the
delete, confirmed gone upstream, and a capture still on disk.

It earned its place immediately. Four notebooks showed as `needs_human` with a
misleading "could not retrieve studio status" error. The `notebook_events` log
showed two reclaim processes had raced (before the pipeline lock landed): one
captured, verified and deleted each notebook; the other, having lost the race,
failed its download *because the notebook was already gone* and overwrote the
state. The captures were intact on disk (136 MB, 95 MB, 41 MB, 41 MB) and all
four were confirmed deleted upstream, so the rows were repaired to `deleted`.

## Acceptance Criteria

- [ ] Log records are self-contained, not bare ids
- [ ] Mirrored off `/Volumes/ext`
- [ ] A reconciliation pass exists and runs at startup
- [ ] `capture_completeness` recorded; `nothing` requires an explicit flag
