---
status: complete
priority: p1
issue_id: "035"
tags: [code-review, data-integrity]
dependencies: []
---

# The verification gate passes on truncated, silently-dropped, and unsupported artifacts

## Problem Statement

Four distinct paths let the 8-condition gate return "verified" while an artifact is partial or absent. Each ends in an irreversible delete.

## Findings

Against installed `nlm` 0.9.6 source:

1. **Truncated downloads.** `services/downloads.py:553-575` — on failure `download_all` appends `{"success": False, "path": output_path}` and streaming leaves a **partial file at that path**. Gate 4 then sees: exists ✓, non-zero ✓, magic bytes ✓ (a truncated PDF still starts `%PDF-`; a truncated m4a often still `ffprobe`s a duration), sha256 recomputes ✓ — because sha256 proves the file didn't change between capture and verify, not that it is complete.
2. **Mind maps silently dropped.** `services/studio.py:718-745` — `except Exception: pass  # Mind maps are optional`. A transient failure makes `studio status` return a successful, complete-looking set minus the mind maps. The saved snapshot, `download all`, and gates 4+5 all agree.
3. **`status` can be `"unknown"`.** `services/studio.py:661-664` normalizes non-string status to `"unknown"`. Gate 5 is written as a denylist ("nothing is `in_progress`"), so `unknown` passes.
4. **`skipped[]` never inspected.** `downloads.py:468-486` — artifacts outside `VALID_ARTIFACT_TYPES` are pushed to `skipped` with reason `unsupported artifact type` and never downloaded, while the call still succeeds.

## Proposed Solutions

1. **Close all four (recommended).**
   - Require `all(item["success"] is True)`; refuse on any `False`.
   - Cross-check `duration_seconds` from `studio status` (`services/studio.py:706-709`) against `ffprobe`; record `Content-Length` vs `st_size`.
   - Compare `download all`'s own `total_artifacts` (it re-polls independently, `downloads.py:453`) against the saved snapshot count — this also closes the TOCTOU.
   - Make the terminal set an **allowlist** `{completed, failed}`; anything else → `needs_human`.
   - Refuse on any `skipped[]` entry whose reason is not `already downloaded`.
   - Require `studio status` to exit 0 and return a parseable array before evaluating gate 5 (same vacuous-pass class as R2).

## Acceptance Criteria

- [ ] `success: False` on any item → `needs_human`, no delete
- [ ] Terminal-status allowlist, not denylist
- [ ] `skipped[]` inspected with a reason allowlist
- [ ] `total_artifacts` cross-check implemented
- [ ] Test: truncated artifact → refused
- [ ] Test: `status: "unknown"` → refused
