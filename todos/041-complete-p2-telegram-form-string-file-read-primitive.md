---
status: complete
priority: p2
issue_id: "041"
tags: [code-review, security]
dependencies: []
---

# `curl -F` in `telegram_send.sh` is a local-file-read primitive

## Problem Statement

`curl -F "field=@/path"` and `-F "field=</path"` make curl **read a local file** and upload its contents as the field value; `;filename=` / `;type=` inside a value are parsed as parameters. Any message or caption whose first character is `@` or `<` therefore exfiltrates a local file into Telegram.

## Findings

Verified call sites in `scripts/telegram_send.sh`:
- `:55` — `-F "text=${text}"`
- `:81`, `:109` — `-F "caption=${caption}"`
- `:79`, `:107` — `-F "document=@${file_path}"` / `-F "audio=@${file_path}"`; a path containing `;` or `,` silently changes the declared filename/content-type or truncates the path.

Impact is concrete: `.env` (`OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`) and `config/google-credentials.json` (a Google account password) both live under `$PROJECT_ROOT`. The new capture code will put notebook titles and `capture_error` strings into alerts and captions — and `nlm download all` names its output directory after the notebook title, so title characters reach the path handed to curl.

## Proposed Solutions

1. **`--form-string` (recommended).** `curl --form-string "text=$text"` treats the value as literal and disables `@`, `<`, and `;` entirely. For file fields, validate the path against `^[A-Za-z0-9/._-]+$` or rename to `<nlm_artifact_id>.<ext>` first and record only that path in the DB. Must land **before** the local-RAG work, which routes model output into `tg_send_message`.

## Acceptance Criteria

- [ ] All text/caption fields use `--form-string`
- [ ] File paths validated or sanitized before reaching curl
- [ ] Artifacts renamed to `<nlm_artifact_id>.<ext>` immediately after download
- [ ] Test: a caption beginning `@/etc/passwd` is sent literally
