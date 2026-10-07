---
status: complete
priority: p1
issue_id: "039"
tags: [code-review, security]
dependencies: []
---

# Gate condition 6 is under-specified — symlinks, absolute paths, and hardlinks defeat it

## Problem Statement

Artifact paths come from `nlm download all --json` (external input). "Resolves inside `data/archive/<notebook_id>/`" is the right intent but leaves three holes, and the check is applied at verify time only — too late if the write already escaped.

## Findings

- **Symlinks.** `normpath`/`abspath` resolve `..` lexically but do not follow links. A symlink left in `artifacts/` by an interrupted run passes a lexical check and writes outside the base.
- **Absolute paths.** An absolute `items[].path` contains no `..` and defeats the stated check — `os.path.join(base, "/abs/path")` returns `/abs/path`.
- **Hardlinks.** sha256 cannot distinguish a hardlink into another notebook's directory — exactly the R1 duplicate-title scenario.
- **Gate 6's own guarantee fails where it matters:** "path came from the `--json` payload, never constructed" is false on the failure branch — `downloads.py:559,570` sets `path` to the *constructed* `output_path`.
- Related: `download all` resolves its target through the alias manager (`cli/commands/download.py:299`), and `nlm delete notebook` accepts "ID **or** alias". 26 aliases contain a literal `/` and are user-editable.

## Proposed Solutions

1. **One `safe_archive_path(notebook_id, rel) -> Path` (recommended)** that: asserts the id UUID regex; rejects absolute paths, `..`, NUL, newline; `resolve(strict=True)` then `is_relative_to(base)`; asserts no path component is a symlink; asserts `st_dev` matches the recorded volume. Applied at **write** time as well as verify time. Record `st_dev`+`st_ino` per artifact and assert `st_ino` uniqueness across notebook dirs. Always pass a raw UUID to delete, asserted against the regex immediately before the call. Download into a freshly emptied `artifacts/` per attempt, then assert the on-disk file set equals `items[].path` exactly — no extras, no missing.

## Acceptance Criteria

- [ ] `safe_archive_path()` exists and is used at write **and** verify
- [ ] Absolute path, `..`, and symlink components all rejected
- [ ] `st_ino` uniqueness asserted across notebook directories
- [ ] Delete is passed a regex-validated UUID, never an alias
- [ ] Test: symlink in `artifacts/` → refused
