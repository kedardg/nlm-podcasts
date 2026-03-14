# 2026-03-10 — Podcast Fix & Auto-Login

## Summary
Diagnosed and fixed two issues that stopped podcasts for several days.
Added auto-login credentials support to `nlm_playwright.py`.
Fixed JSON syntax error in `config/google-credentials.json`.

## Issues Diagnosed

### 1. Expired Google session (Mar 4–5)
NLM browser profile (`~/.notebooklm-chrome`) session had expired.
`nlm_playwright.py` was logging `login_failed` and aborting.
Resolution: ran `--login` manually to refresh the session.

### 2. `NameError: _open_studio_panel` (BUG-007)
`generate_report()` called `_open_studio_panel(page)` which was never defined.
Fixed by adding the function definition before the call.

### 3. CDP fallback (operational, not a bug)
`nlm_download_audio.py` CDP path requires Chrome running with `--remote-debugging-port`.
When Chrome isn't running, the fallback silently fails.
No code change — operational awareness only.

## Auto-Login Feature Added

Added `_do_auto_login(page, credentials)` to `nlm_playwright.py`:
- Reads `config/google-credentials.json` (email, password, profile_dir)
- Fills Google OAuth2 web form (email → Next → password → Next)
- Polls for redirect to notebooklm.google.com
- Detects 2FA challenges and falls back to `--login` prompt
- `ensure_login()` now calls auto-login before prompting user

**Important**: Regular Google password works. App Passwords do NOT work (web login ≠ SMTP).
If 2FA is enabled, auto-login will detect the challenge and fall back gracefully.

## Files Changed
- `scripts/nlm_playwright.py` — added `_open_studio_panel()`, `_do_auto_login()`, updated `ensure_login()`
- `config/google-credentials.json` — fixed JSON syntax (missing opening quote on profile_dir value, unescaped `"` in password)
- `CLAUDE.md` — documented `config/google-credentials.json`, updated `nlm_playwright.py` description
- `docs/BUGS.md` — added BUG-007

## Decisions
- Store credentials in `config/` (gitignored) rather than `~/.config/` for project locality
- Auto-login is opt-in (only activates if credentials file exists)
- Credentials file uses plain JSON — no encryption, relies on filesystem permissions
