---
status: complete
priority: p1
issue_id: "026"
tags: [codex-review, security]
dependencies: ["001", "007"]
---

# Harden cookie/auth file permissions to 0600

## Problem

`nlm_rotate_cookies.py::save_cookies()` wrote Google session cookies via
`Path.write_text()`, inheriting the caller's umask (commonly 0644). Any local
user or process could read the live session tokens and hijack the NotebookLM
session.

## Fix Applied

1. **lib/file_utils.py** — Extended `save_json()` with an optional `mode`
   parameter. When given (e.g. `0o600`), the temp file is created via
   `os.open(..., O_CREAT, mode)` so permissions are set at creation time
   (no race window). Final `os.chmod` after rename as belt-and-suspenders.
2. **scripts/nlm_rotate_cookies.py** — `save_cookies()` now uses
   `save_json(path, data, mode=0o600)` for cookies.json, metadata.json, and
   auth.json. Profile dir enforced to 0o700 via `_secure_dir()`.

Supersedes todos 001 and 007 for nlm_rotate_cookies.py scope.

## Source

Codex adversarial review (2026-04-15) — HIGH finding.
