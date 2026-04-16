#!/usr/bin/env python3
"""NLM Playwright Automation — browser-driven NotebookLM workflow.

Replaces the nlm CLI with Playwright browser automation to fix:
- BUG-001: TooManyRedirects on audio download (now native browser download)
- Cookie rotation every 10 min (browser manages its own session)
- 180-min poll timeouts (direct UI status checks)

Usage:
    .venv/bin/python3 scripts/nlm_playwright.py \\
        --create "Daily Feed — 2026-03-03" \\
        --add-urls url1 url2 url3 \\
        --add-text "briefing content" --text-title "Daily Feed Briefing" \\
        --report-prompt config/prompts/daily-report-prompt-v3.txt \\
        --podcast-format deep_dive --podcast-length long \\
        --podcast-focus config/prompts/daily-podcast-focus.txt \\
        --download-dir data/daily-feed/ \\
        --output-prefix 2026-03-03 \\
        --share

    # Test login only
    .venv/bin/python3 scripts/nlm_playwright.py --test-login

    # Add --headless for cron/pipeline use
    .venv/bin/python3 scripts/nlm_playwright.py --headless ...

Auth:
    Auto-login: fill config/google-credentials.json with your Google email and
    an App Password (https://myaccount.google.com/apppasswords). The script will
    automatically sign in when the session expires — no manual intervention needed.

    Manual login (one-time): .venv/bin/python3 scripts/nlm_playwright.py --login
    This opens a browser window for manual sign-in. Session is saved to the
    persistent profile (~/.notebooklm-chrome/) for subsequent headless runs.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lib.file_utils import load_json, save_json, now_iso
from lib.log_utils import StepTimer, log_event

NLM_URL = "https://notebooklm.google.com"
CREDENTIALS_PATH = PROJECT_ROOT / "config" / "google-credentials.json"
DEFAULT_PROFILE_DIR = Path.home() / ".notebooklm-chrome"
OPERATIONS_LOG = PROJECT_ROOT / "data" / "nlm-operations-log.json"
PROCESSING_LOG = PROJECT_ROOT / "data" / "processing-log.json"


# ── Utility helpers ──────────────────────────────────────────────

def ts():
    """Current UTC ISO timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    """Print timestamped message to stdout."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def read_file_text(path):
    """Read a file and return its text, or None if missing."""
    p = Path(path)
    if p.exists():
        return p.read_text().strip()
    return None


# ── NLM CLI auth sync ──────────────────────────────────────────

NLM_CLI_PROFILE = Path.home() / ".notebooklm-mcp-cli" / "profiles" / "default"
NLM_CLI_AUTH = Path.home() / ".notebooklm-mcp-cli" / "auth.json"


def _cleanup_profile_locks(profile_dir=None):
    """Remove stale Chrome lock files that prevent Playwright from launching.

    Chrome writes these when running; if the process crashes they remain
    and block subsequent launches with TargetClosedError.
    """
    pdir = Path(profile_dir or DEFAULT_PROFILE_DIR)
    for lock_file in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        p = pdir / lock_file
        if p.exists() or p.is_symlink():
            try:
                p.unlink(missing_ok=True)
                log(f"  Cleaned up stale lock: {lock_file}")
            except OSError:
                pass


def _extract_session_tokens(page):
    """Extract f.sid, csrf_token, and build_label from a live NLM page."""
    try:
        tokens = page.evaluate("""() => {
            var scripts = document.querySelectorAll('script');
            var f = '', c = '', b = '';
            for (var s of scripts) {
                var t = s.textContent, m;
                if ((m = t.match(/FdrFJe":"([^"]+)"/))) f = m[1];
                if ((m = t.match(/SNlM0e":"([^"]+)"/))) c = m[1];
                if ((m = t.match(/cfb2h":"([^"]+)"/))) b = m[1];
            }
            return {fsid: f, csrf: c, bl: b};
        }""")
        return tokens
    except Exception as e:
        log(f"  Token extraction failed: {e}")
        return {"fsid": "", "csrf": "", "bl": ""}


def sync_cookies_to_nlm(browser_context, page):
    """Sync Playwright cookies + session tokens to nlm CLI config.

    Bridges the gap between Playwright's persistent profile and the nlm CLI's
    cookie-based auth. Call after any successful NLM page load.
    """
    try:
        # 1. Extract cookies from Playwright
        cookies = browser_context.cookies()
        google_cookies = [c for c in cookies if ".google.com" in c.get("domain", "")]

        if len(google_cookies) < 5:
            log(f"  Sync skipped: only {len(google_cookies)} Google cookies (need >=5)")
            return False

        # 2. Extract session tokens from the page
        tokens = _extract_session_tokens(page)
        if not tokens.get("fsid"):
            log("  Sync skipped: could not extract session tokens from page")
            return False

        # 3. Write cookies.json (nlm CLI format: {name: value} dict)
        NLM_CLI_PROFILE.mkdir(parents=True, exist_ok=True)
        cookies_dict = {c["name"]: c["value"] for c in google_cookies}
        cookies_path = NLM_CLI_PROFILE / "cookies.json"
        cookies_path.write_text(json.dumps(cookies_dict, indent=2))
        os.chmod(cookies_path, 0o600)

        # 4. Write metadata.json
        meta_path = NLM_CLI_PROFILE / "metadata.json"
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        meta["csrf_token"] = tokens["csrf"]
        meta["session_id"] = tokens["fsid"]
        meta["build_label"] = tokens["bl"]
        meta["last_validated"] = datetime.now(timezone.utc).isoformat()
        meta_path.write_text(json.dumps(meta, indent=2))

        # 5. Update auth.json cache
        auth = {}
        if NLM_CLI_AUTH.exists():
            try:
                auth = json.loads(NLM_CLI_AUTH.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        auth["csrf_token"] = tokens["csrf"]
        auth["session_id"] = tokens["fsid"]
        auth["build_label"] = tokens["bl"]
        auth["cookies"] = cookies_dict
        NLM_CLI_AUTH.write_text(json.dumps(auth, indent=2))

        log(f"  Synced {len(google_cookies)} cookies + tokens to nlm CLI")
        return True

    except Exception as e:
        log(f"  Cookie sync failed: {e}")
        return False


def load_credentials():
    """Load Google credentials from config/google-credentials.json."""
    if not CREDENTIALS_PATH.exists():
        return None
    try:
        return json.loads(CREDENTIALS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None


# ── Run record (operations log) ─────────────────────────────────

class RunRecord:
    """Accumulates provenance data for a single NLM Playwright run."""

    def __init__(self, headless=False):
        self.run_id = f"pw_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}"
        self.started_at = ts()
        self.start_mono = time.monotonic()
        self.headless = headless
        self.notebook = {}
        self.sources_urls = []
        self.sources_text = []
        self.sources_files = []
        self.artifacts = {}
        self.share_link = ""
        self.errors = []

    def add_url(self, url, status, title="", error=""):
        entry = {"url": url, "status": status}
        if title:
            entry["title"] = title
        if error:
            entry["error"] = error
        self.sources_urls.append(entry)

    def add_text(self, title, chars, status, error=""):
        entry = {"title": title, "chars": chars, "status": status}
        if error:
            entry["error"] = error
        self.sources_text.append(entry)

    def set_artifact(self, kind, status, output="", size_bytes=0, error=""):
        self.artifacts[kind] = {"status": status}
        if output:
            self.artifacts[kind]["output"] = output
        if size_bytes:
            self.artifacts[kind]["size_bytes"] = size_bytes
        if error:
            self.artifacts[kind]["error"] = error

    def to_dict(self):
        all_sources = self.sources_urls + self.sources_text + self.sources_files
        succeeded = sum(1 for s in all_sources if s.get("status") == "ok")
        failed = sum(1 for s in all_sources if s.get("status") == "fail")
        elapsed = time.monotonic() - self.start_mono
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "completed_at": ts(),
            "duration_s": round(elapsed),
            "notebook": self.notebook,
            "sources_added": {
                "urls": self.sources_urls,
                "text": self.sources_text,
                "files": self.sources_files,
                "total": len(all_sources),
                "succeeded": succeeded,
                "failed": failed,
            },
            "artifacts": self.artifacts,
            "share_link": self.share_link,
            "errors": self.errors,
            "headless": self.headless,
        }

    def save(self):
        """Append run record to operations log and processing log."""
        # Operations log (detailed)
        ops = load_json(str(OPERATIONS_LOG), default={"runs": []})
        ops.setdefault("runs", []).append(self.to_dict())
        save_json(str(OPERATIONS_LOG), ops)
        log(f"  Operations log: {OPERATIONS_LOG}")

        # Processing log (summary)
        proc = load_json(str(PROCESSING_LOG), default={"runs": []})
        proc.setdefault("runs", []).append({
            "run_id": self.run_id,
            "pipeline": "daily-playwright",
            "started_at": self.started_at,
            "completed_at": ts(),
            "duration_s": round(time.monotonic() - self.start_mono),
            "errors": self.errors,
            "nlm_skipped": False,
        })
        save_json(str(PROCESSING_LOG), proc)


# ── Playwright automation functions ──────────────────────────────

def create_browser_cdp(cdp_url):
    """Connect to an existing Chrome instance via CDP.

    Uses system Chrome's Google session — no separate login needed.
    Chrome must be running with --remote-debugging-port (e.g., 9222).

    Reuses an existing NLM tab if found, otherwise creates a new page.
    """
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(cdp_url)

    # Get the default context (Chrome's existing session)
    context = browser.contexts[0] if browser.contexts else browser.new_context()

    # Prefer reusing an existing NLM page (it already has the session cookies)
    page = None
    for p in context.pages:
        if "notebooklm.google.com" in p.url:
            page = p
            break
    if not page:
        page = context.new_page()

    return pw, browser, page


def create_browser(headless=False, profile_dir=None):
    """Launch Chromium with persistent profile for session reuse.

    On macOS, headed mode can fail with CVDisplayLinkCreateWithCGDisplay errors
    when no display is available (SSH, cron, some terminal contexts). Falls back
    to headless mode automatically.
    """
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()

    profile = str(profile_dir or DEFAULT_PROFILE_DIR)
    os.makedirs(profile, exist_ok=True)
    _cleanup_profile_locks(profile)

    launch_kwargs = dict(
        user_data_dir=profile,
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        viewport={"width": 1280, "height": 900},
        locale="en-US",
        ignore_default_args=["--enable-automation"],
    )

    try:
        browser = pw.chromium.launch_persistent_context(**launch_kwargs)
    except Exception as e:
        if not headless and ("SIGTRAP" in str(e) or "CVDisplayLink" in str(e) or "Target page, context or browser has been closed" in str(e)):
            log("  Headed mode failed (no display?), retrying headless...")
            browser = pw.chromium.launch_persistent_context(**{**launch_kwargs, "headless": True})
        else:
            raise

    page = browser.pages[0] if browser.pages else browser.new_page()
    return pw, browser, page


def _do_auto_login(page, credentials: dict) -> bool:
    """Fill Google login form with stored credentials.

    Works reliably when:
    - Browser is headed (not headless) — Google rarely blocks this
    - Password is a Google App Password (16-char, bypasses 2FA)

    Returns True if login succeeded, False otherwise.
    """
    email = credentials.get("email", "")
    password = credentials.get("password", "")
    if not email or not password:
        log("  Auto-login skipped: credentials missing email or password")
        return False

    log(f"  Auto-login: filling credentials for {email}")
    try:
        # Step 1: Fill email field
        email_input = page.locator('input[type="email"]').first
        email_input.wait_for(state="visible", timeout=15000)
        email_input.fill(email)

        # Click Next after email
        next_btn = page.locator('button:has-text("Next"), #identifierNext').first
        next_btn.click()
        time.sleep(2)

        # Step 2: Fill password field
        pw_input = page.locator('input[type="password"]').first
        pw_input.wait_for(state="visible", timeout=15000)
        pw_input.fill(password)

        # Click Next after password
        pw_next = page.locator('button:has-text("Next"), #passwordNext').first
        pw_next.click()

        # Step 3: Wait for redirect to NLM (up to 30s)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            time.sleep(2)
            url = page.url
            if "notebooklm.google.com" in url and "accounts.google" not in url:
                log("  Auto-login successful")
                return True
            # Detect 2FA / verification page
            if "challenge" in url or "signin/v2/challenge" in url or "twosv" in url:
                log("  Auto-login: 2FA/challenge detected — cannot proceed automatically")
                log("  Use a Google App Password (no 2FA) or run --login manually")
                return False

        log(f"  Auto-login: timed out. Current URL: {page.url}")
        return False

    except Exception as e:
        log(f"  Auto-login failed: {e}")
        return False


def ensure_login(page, credentials=None, timeout=120):
    """Navigate to NLM and verify session. Auto-logins if credentials are available.

    Auth strategy:
      1. Check if already logged in via persistent browser profile session
      2. If session expired and credentials are in config/google-credentials.json,
         attempt automated login (fills Google login form)
      3. If no credentials or auto-login fails, prompt user to run --login manually

    Use a Google App Password for the password field to bypass 2FA prompts.
    Generate one at: https://myaccount.google.com/apppasswords

    Returns True if logged in, False if session expired and could not recover.
    """
    with StepTimer("ensure_login"):
        page.goto(NLM_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)

        url = page.url
        # Already logged in — NLM main page
        if "notebooklm.google.com" in url and "accounts.google" not in url:
            log("  Logged in (session from browser profile)")
            return True

        # Session expired — try auto-login from credentials file
        creds = credentials or load_credentials()
        if creds:
            log("  Session expired — attempting auto-login from config/google-credentials.json")
            if _do_auto_login(page, creds):
                return True
            log("  Auto-login failed")

        # Final fallback — prompt for manual login
        log("  ERROR: Not logged in. Options:")
        log("  1. Add credentials to config/google-credentials.json for auto-login")
        log("     (use a Google App Password: https://myaccount.google.com/apppasswords)")
        log("  2. Run: .venv/bin/python3 scripts/nlm_playwright.py --login")
        return False


def do_interactive_login(headless=False, profile_dir=None):
    """Interactive login: open headed browser for manual Google sign-in.

    The user logs in manually in the browser window. The persistent profile
    saves cookies/session for subsequent headless runs.
    """
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    actual_profile = str(profile_dir or DEFAULT_PROFILE_DIR)
    os.makedirs(actual_profile, exist_ok=True)
    _cleanup_profile_locks(actual_profile)

    log("Opening browser for Google login...")
    log("Log into your Google account in the browser window.")
    log("Once you see NotebookLM's main page, press ENTER here.")

    browser = pw.chromium.launch_persistent_context(
        user_data_dir=actual_profile,
        headless=False,  # Must be headed for manual login
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        viewport={"width": 1280, "height": 900},
        locale="en-US",
        ignore_default_args=["--enable-automation"],
    )
    page = browser.pages[0] if browser.pages else browser.new_page()
    page.goto(NLM_URL, wait_until="domcontentloaded", timeout=60000)

    # Wait for login: poll page URL until user signs in manually,
    # or accept ENTER from stdin if available.
    log("Waiting for you to sign in (polling every 3s, up to 5 min)...")
    deadline = time.monotonic() + 300  # 5 min
    logged_in = False
    while time.monotonic() < deadline:
        try:
            url = page.url
            if "notebooklm.google.com" in url and "accounts.google" not in url:
                logged_in = True
                break
        except Exception:
            pass  # page might be navigating
        time.sleep(3)

    if logged_in:
        log("Login successful! Session saved to browser profile.")
        log(f"Profile: {actual_profile}")
        log("You can now use --headless for automated runs.")
        # Sync cookies to nlm CLI so both auth systems stay in sync
        sync_cookies_to_nlm(browser, page)
    else:
        url = page.url
        log(f"WARNING: Timed out. Current URL: {url}")
        log("Try running --login again and complete sign-in within 5 min.")

    browser.close()
    pw.stop()


def do_sync_auth():
    """Sync Playwright auth session to nlm CLI — headless, auto-login if needed.

    Used by nlm_ensure_auth.sh as a recovery layer. Returns 0 on success, 1 on failure.
    """
    log("Syncing Playwright auth to nlm CLI...")

    try:
        pw, browser, page = create_browser(headless=True)
    except Exception as e:
        log(f"  Failed to launch browser: {e}")
        return 1

    try:
        # Check if already logged in via persistent profile
        if not ensure_login(page):
            log("  Playwright session expired and auto-login failed")
            browser.close()
            pw.stop()
            return 1

        # We're on the NLM page — sync cookies to nlm CLI
        if sync_cookies_to_nlm(browser, page):
            log("  Auth sync complete")
            browser.close()
            pw.stop()
            return 0
        else:
            log("  Auth sync failed (could not extract cookies/tokens)")
            browser.close()
            pw.stop()
            return 1

    except Exception as e:
        log(f"  Sync auth error: {e}")
        try:
            browser.close()
            pw.stop()
        except Exception:
            pass
        return 1


def create_notebook(page, title, record):
    """Create a new notebook with the given title. Returns notebook ID or None."""
    with StepTimer("create_notebook"):
        # Navigate to NLM home
        page.goto(NLM_URL, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        # Click "Create new notebook" — STABLE aria-role selector (confirmed by research)
        create_btn = page.get_by_role("button", name="Create new notebook")
        try:
            create_btn.wait_for(state="attached", timeout=10000)
            create_btn.click()
        except Exception:
            # Fallback to text-based selectors
            for selector in [
                'button:has-text("New notebook")',
                'button:has-text("Create")',
            ]:
                try:
                    el = page.locator(selector).first
                    if el.is_visible(timeout=3000):
                        el.click()
                        break
                except Exception:
                    continue
            else:
                log("  ERROR: Could not find create notebook button")
                record.errors.append("create_notebook: button not found")
                return None

        time.sleep(3)

        # Close the initial source dialog if it auto-opens
        try:
            close_btn = page.locator("button:has(mat-icon:has-text('close'))").first
            if close_btn.is_visible(timeout=2000):
                close_btn.click()
                time.sleep(500)
        except Exception:
            pass

        # Extract notebook ID from URL (format: /notebook/<uuid>)
        notebook_id = _extract_notebook_id(page.url)
        if not notebook_id:
            log("  ERROR: Could not extract notebook ID from URL")
            record.errors.append("create_notebook: no ID in URL")
            return None

        # Rename the notebook — DataNath approach: click .title-input, Ctrl+A, type
        _rename_notebook(page, title)

        notebook_url = page.url
        record.notebook = {
            "title": title,
            "id": notebook_id,
            "url": notebook_url,
        }
        log(f"  Created notebook: {notebook_id}")
        return notebook_id


def _extract_notebook_id(url):
    """Extract UUID notebook ID from NLM URL."""
    match = re.search(r'/notebook/([0-9a-f-]{36})', url)
    if match:
        return match.group(1)
    match = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', url)
    return match.group(1) if match else None


def _rename_notebook(page, title):
    """Rename the current notebook. Uses .title-input (DataNath approach)."""
    try:
        title_el = page.locator(".title-input").first
        if title_el.is_visible(timeout=3000):
            title_el.click()
            page.keyboard.press("Control+A")
            title_el.fill(title)
            title_el.press("Enter")
            time.sleep(1)
            log(f"  Renamed notebook to: {title}")
            return
    except Exception:
        pass

    # Fallback: try h1 or any title-like element
    try:
        for selector in ['h1.notebook-title', 'h1[contenteditable]', '[contenteditable="true"]']:
            el = page.locator(selector).first
            if el.is_visible(timeout=2000):
                el.dblclick()
                time.sleep(0.3)
                page.keyboard.press("Control+A")
                page.keyboard.type(title)
                page.keyboard.press("Enter")
                log(f"  Renamed notebook to: {title}")
                return
    except Exception as e:
        log(f"  Warning: could not rename notebook: {e}")


def add_url_source(page, url, record, timeout=90):
    """Add a URL source to the current notebook.

    Uses NLM's actual selectors (from research of 3 automation repos):
    - STABLE: page.get_by_role("button", name="Add source")
    - STABLE: page.get_by_role("button", name="Insert")
    - MODERATE: span.mdc-evolution-chip__text-label for "Website" chip
    - MODERATE: [formcontrolname='newUrl'] for URL input
    - FRAGILE (completion): div.single-source-container + .mat-mdc-progress-spinner
    """
    try:
        # Click "Add source" — STABLE selector
        add_btn = page.get_by_role("button", name="Add source")
        try:
            add_btn.wait_for(state="attached", timeout=5000)
        except Exception:
            record.add_url(url, "fail", error="add source button not found")
            return False
        add_btn.click()
        time.sleep(1)

        # Click "Website" chip — MODERATE selector (Angular Material)
        chip = page.locator("span.mdc-evolution-chip__text-label", has_text="Website")
        try:
            chip.wait_for(state="attached", timeout=5000)
            chip.click()
        except Exception:
            # Fallback: try text match
            page.get_by_text("Website", exact=True).click()
        time.sleep(0.5)

        # Fill URL — MODERATE selector (Angular formcontrol)
        url_input = page.locator("[formcontrolname='newUrl']")
        try:
            url_input.wait_for(state="attached", timeout=5000)
        except Exception:
            # Fallback selectors
            url_input = page.locator("textarea[placeholder*='http'], input[type='url']").first
        url_input.fill(url)
        time.sleep(0.5)

        # Click Insert — STABLE selector
        insert_btn = page.get_by_role("button", name="Insert")
        insert_btn.click()
        time.sleep(2)

        # Wait for source processing (DataNath's robust approach):
        # 1. Wait for source container to appear
        # 2. Wait for spinner to detach
        # 3. Wait for checkbox with changed label
        _wait_source_processing(page, url, timeout)

        record.add_url(url, "ok")
        return True

    except Exception as e:
        record.add_url(url, "fail", error=str(e)[:200])
        _close_dialog(page)
        return False


def add_text_source(page, text, title, record, timeout=90):
    """Add a pasted text source to the current notebook."""
    try:
        # Click "Add source" — STABLE
        add_btn = page.get_by_role("button", name="Add source")
        add_btn.wait_for(state="attached", timeout=5000)
        add_btn.click()
        time.sleep(1)

        # Click "Copied text" chip — MODERATE
        chip = page.locator("span.mdc-evolution-chip__text-label", has_text="Copied text")
        try:
            chip.wait_for(state="attached", timeout=5000)
            chip.click()
        except Exception:
            page.get_by_text("Copied text", exact=True).click()
        time.sleep(0.5)

        # Fill text content — MODERATE (Angular formcontrol)
        text_input = page.locator("textarea[formcontrolname='textInput']")
        if not text_input.is_visible(timeout=3000):
            text_input = page.locator("textarea[formcontrolname='newText']")
        if not text_input.is_visible(timeout=2000):
            text_input = page.locator("mat-dialog-container textarea").last
        text_input.fill(text)
        time.sleep(0.5)

        # Fill title if field exists
        if title:
            try:
                title_input = page.locator("input[formcontrolname='sourceTitle']")
                if title_input.is_visible(timeout=2000):
                    title_input.fill(title)
            except Exception:
                pass

        # Click Insert — STABLE
        page.get_by_role("button", name="Insert").click()
        time.sleep(2)
        _wait_source_processing(page, title, timeout)

        record.add_text(title, len(text), "ok")
        return True

    except Exception as e:
        record.add_text(title, len(text), "fail", error=str(e)[:200])
        _close_dialog(page)
        return False


def _close_dialog(page):
    """Close any open dialog/modal."""
    try:
        close_btn = page.locator("button:has(mat-icon:has-text('close'))").first
        if close_btn.is_visible(timeout=1000):
            close_btn.click()
            return
    except Exception:
        pass
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
    except Exception:
        pass


def _wait_source_processing(page, source_label, timeout=90):
    """Wait for source processing to complete.

    DataNath's robust approach:
    1. Wait for source container to appear
    2. Wait for spinner to detach
    3. Wait for checkbox label to differ from URL (indicates processing done)
    """
    try:
        container = page.locator("div.single-source-container").last
        container.wait_for(state="attached", timeout=30000)

        spinner = container.locator(".mat-mdc-progress-spinner")
        spinner.wait_for(state="detached", timeout=timeout * 1000)

        # Extra: wait for checkbox label to change (indicates content indexed)
        try:
            checkbox = container.locator("input.mdc-checkbox__native-control.mdc-checkbox--selected")
            checkbox.wait_for(state="attached", timeout=10000)
        except Exception:
            pass

        time.sleep(1.2)  # Brief settle time
    except Exception:
        # Spinner may not appear for fast/cached sources — just wait a bit
        time.sleep(3)


def _open_studio_panel(page):
    """Open the NLM Studio/customize panel (edit icon or Studio tab).

    Tries the mat-icon pencil/edit button first, then falls back to a
    Studio tab or any visible customize button.
    """
    import time as _time
    for selector in [
        "mat-icon:has-text('edit')",
        "mat-icon.edit-button-icon",
        '[aria-label*="customize" i]',
        '[aria-label*="studio" i]',
        'button:has-text("Customize")',
    ]:
        try:
            el = page.locator(selector).first
            if el.is_visible(timeout=3000):
                el.click()
                _time.sleep(1)
                return
        except Exception:
            continue


def generate_report(page, prompt_text, record, timeout=300):
    """Generate a report using the Studio panel with a custom prompt."""
    with StepTimer("generate_report"):
        try:
            # Open studio/customize panel
            _open_studio_panel(page)
            time.sleep(2)

            # Look for report option and click it
            for selector in [
                'button:has-text("Briefing Doc")',
                'button:has-text("Report")',
                'button:has-text("Study Guide")',
                '[aria-label*="report" i]',
                '[aria-label*="Briefing" i]',
            ]:
                try:
                    el = page.locator(selector).first
                    if el.is_visible(timeout=2000):
                        el.click()
                        break
                except Exception:
                    continue
            time.sleep(1)

            # If there's a custom prompt/instructions field, fill it
            if prompt_text:
                _fill_custom_prompt(page, prompt_text)

            # Click generate/create
            for selector in [
                'button:has-text("Generate")',
                'button:has-text("Create")',
                'button:has-text("Customize")',
            ]:
                try:
                    el = page.locator(selector).first
                    if el.is_visible(timeout=2000):
                        el.click()
                        break
                except Exception:
                    continue

            log("  Report generation initiated")
            return True

        except Exception as e:
            log(f"  Report generation failed: {e}")
            record.errors.append(f"generate_report: {e}")
            return False


def generate_podcast(page, fmt, length, focus_text, record, timeout=300):
    """Generate podcast audio using the Studio panel.

    Uses NLM's actual selectors (from israelbls research):
    - FRAGILE: mat-icon:has-text('edit') for opening config dialog
    - MODERATE: mat-radio-button:has-text('Deep Dive') for style
    - FRAGILE: textarea[placeholder*='Things to try'] for focus prompt
    - MODERATE: mat-dialog-actions button:has-text('Generate') for submit
    - STABLE: page.get_by_role("button", name="Generate") for simple generate
    """
    with StepTimer("generate_podcast"):
        try:
            # Try opening the audio config dialog via edit/pencil icon
            edit_icon = page.locator("mat-icon:has-text('edit'), mat-icon.edit-button-icon").first
            dialog_opened = False

            try:
                if edit_icon.is_visible(timeout=5000):
                    edit_icon.click(force=True)
                    page.wait_for_selector("mat-dialog-container", state="visible", timeout=5000)
                    dialog_opened = True
            except Exception:
                pass

            if dialog_opened:
                # Set audio style via radio button
                style_map = {
                    "deep_dive": "Deep Dive",
                    "summary": "Summary",
                    "critique": "Critique",
                    "debate": "Debate",
                }
                style_text = style_map.get(fmt, "Deep Dive")
                try:
                    radio = page.locator(f"mat-radio-button:has-text('{style_text}')")
                    if radio.is_visible(timeout=2000):
                        radio.click()
                        time.sleep(0.3)
                except Exception:
                    pass

                # Set focus prompt if provided
                if focus_text:
                    try:
                        textarea = page.locator("textarea[placeholder*='Things to try']").first
                        if not textarea.is_visible(timeout=2000):
                            textarea = page.locator("mat-dialog-container textarea").last
                        if textarea.is_visible(timeout=2000):
                            textarea.fill(focus_text)
                            time.sleep(0.3)
                    except Exception:
                        log("  Could not set focus prompt (non-critical)")

                # Click Generate in dialog
                gen_btn = page.locator("mat-dialog-actions button:has-text('Generate')").last
                if not gen_btn.is_visible(timeout=2000):
                    gen_btn = page.locator("mat-dialog-actions button").last
                gen_btn.click()

                # Wait for dialog to close
                try:
                    page.wait_for_selector("mat-dialog-container", state="hidden", timeout=5000)
                except Exception:
                    pass

            else:
                # Fallback: simple Generate button click
                gen_btn = page.get_by_role("button", name="Generate")
                try:
                    gen_btn.wait_for(state="attached", timeout=10000)
                    gen_btn.click()
                except Exception:
                    log("  ERROR: Cannot find audio generation controls")
                    record.errors.append("generate_podcast: no controls found")
                    return False

            time.sleep(2)
            log(f"  Podcast generation initiated (style={fmt})")
            return True

        except Exception as e:
            log(f"  Podcast generation failed: {e}")
            record.errors.append(f"generate_podcast: {e}")
            return False


def poll_studio(page, timeout=10800, poll_interval=30):
    """Poll studio status until all artifacts are complete or timeout.

    Uses israelbls's status detection approach:
    - "sync" or "Generating" in artifact text → generating
    - "play_arrow" icon visible → completed
    - "error" in text → failed

    Artifact library selector: artifact-library > * (MODERATE stability)
    """
    with StepTimer("poll_studio"):
        start = time.monotonic()
        deadline = start + timeout
        check = 0

        while time.monotonic() < deadline:
            check += 1
            try:
                library = page.locator("artifact-library")
                if library.is_visible(timeout=5000):
                    items = library.locator(":scope > *")
                    count = items.count()

                    if count == 0:
                        if check % 10 == 1:
                            log("  No artifacts visible yet...")
                    else:
                        all_done = True
                        has_error = False

                        for i in range(count):
                            text = items.nth(i).inner_text().lower()
                            if "sync" in text or "generating" in text:
                                all_done = False
                            elif "error" in text:
                                has_error = True
                                log(f"  Artifact {i+1} FAILED")

                        if all_done:
                            elapsed = int(time.monotonic() - start)
                            log(f"  All {count} artifacts complete ({elapsed}s)")
                            return True
                        if has_error:
                            return False

            except Exception:
                pass

            elapsed = int(time.monotonic() - start)
            if check % 10 == 1 and check > 1:
                log(f"  Still generating... ({elapsed}s elapsed)")

            # Periodically reload to get fresh status
            if check % 30 == 0:
                try:
                    page.reload(wait_until="domcontentloaded", timeout=30000)
                    time.sleep(3)
                except Exception:
                    pass

            time.sleep(poll_interval)

        elapsed = int(time.monotonic() - start)
        log(f"  Studio poll timed out after {elapsed}s")
        return False


def download_report(page, output_path, record):
    """Download the report text from the notebook."""
    with StepTimer("download_report"):
        try:
            # Try to find and click the report artifact
            report_el = None
            for selector in [
                '[aria-label*="Briefing Doc" i]',
                '[aria-label*="Report" i]',
                ':has-text("Briefing Doc")',
            ]:
                try:
                    el = page.locator(selector).first
                    if el.is_visible(timeout=2000):
                        report_el = el
                        break
                except Exception:
                    continue

            if report_el:
                report_el.click()
                time.sleep(2)

            # Try to get report content — look for the report viewer content
            report_text = ""

            # Method 1: Look for a copy button and use clipboard
            for selector in [
                'button[aria-label*="Copy" i]',
                'button:has-text("Copy")',
            ]:
                try:
                    el = page.locator(selector).first
                    if el.is_visible(timeout=2000):
                        el.click()
                        time.sleep(1)
                        # Read from clipboard
                        report_text = page.evaluate("navigator.clipboard.readText()")
                        if report_text:
                            break
                except Exception:
                    continue

            # Method 2: Extract text from the report viewer element
            if not report_text:
                for selector in [
                    '[class*="report-content"]',
                    '[class*="artifact-content"]',
                    '[class*="document-content"]',
                    '.studio-content',
                    'article',
                ]:
                    try:
                        el = page.locator(selector).first
                        if el.is_visible(timeout=2000):
                            report_text = el.inner_text()
                            if len(report_text) > 100:
                                break
                    except Exception:
                        continue

            # Method 3: Use the download button if available
            if not report_text:
                try:
                    with page.expect_download(timeout=15000) as download_info:
                        for selector in [
                            'button[aria-label*="Download" i]',
                            'a[download]',
                            'button:has-text("Download")',
                        ]:
                            try:
                                el = page.locator(selector).first
                                if el.is_visible(timeout=2000):
                                    el.click()
                                    break
                            except Exception:
                                continue
                    download = download_info.value
                    dl_path = download.path()
                    if dl_path:
                        report_text = Path(dl_path).read_text()
                except Exception:
                    pass

            if report_text and len(report_text) > 50:
                output = Path(output_path)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(report_text)
                size = output.stat().st_size
                record.set_artifact("report", "ok", output=str(output), size_bytes=size)
                log(f"  Report saved: {output} ({size:,} bytes)")
                return True
            else:
                log("  ERROR: Could not extract report text")
                record.set_artifact("report", "fail", error="could not extract text")
                return False

        except Exception as e:
            log(f"  Report download failed: {e}")
            record.set_artifact("report", "fail", error=str(e))
            return False


def download_audio(page, output_path, record):
    """Download podcast audio from the notebook.

    This is the key fix for BUG-001 (TooManyRedirects). The browser handles
    the cross-domain redirect chain natively — no CDP cookie hack needed.

    Method 1 (preferred): More menu → Download menu item → expect_download()
    Method 2 (fallback): Play interception → capture media URL → download

    Uses NLM selectors from israelbls research:
    - STABLE: button[aria-label='More'] — three-dot menu on artifact
    - STABLE: page.get_by_role("menuitem", name="Download")
    - STABLE: button[aria-label='Play'] — play button for interception
    - MODERATE: artifact-library > * — artifact items
    """
    with StepTimer("download_audio"):
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Find the audio artifact in the library
            library = page.locator("artifact-library")
            if not library.is_visible(timeout=5000):
                log("  ERROR: No artifact library visible")
                record.set_artifact("podcast", "fail", error="no artifact library")
                return False

            items = library.locator(":scope > *")
            audio_item = None
            # Prefer completed audio artifacts (have play_arrow and a visible More button).
            # Skip items that are still generating (no More button = not ready).
            for i in range(items.count()):
                item = items.nth(i)
                text = item.inner_text().lower()
                if "play_arrow" in text:
                    more = item.locator("button[aria-label='More']")
                    if more.is_visible(timeout=1000):
                        audio_item = item
                        break
            # Fallback: any audio-like item with More button visible
            if not audio_item:
                for i in range(items.count()):
                    item = items.nth(i)
                    text = item.inner_text().lower()
                    if "audio" in text or "deep dive" in text or "podcast" in text:
                        more = item.locator("button[aria-label='More']")
                        if more.is_visible(timeout=1000):
                            audio_item = item
                            break

            if not audio_item:
                log("  ERROR: No audio artifact found")
                record.set_artifact("podcast", "fail", error="no audio artifact")
                return False

            audio_item.scroll_into_view_if_needed(timeout=2000)

            # Method 1: More → Download menu item
            more_btn = audio_item.locator("button[aria-label='More']")
            if more_btn.is_visible(timeout=3000):
                more_btn.click()
                time.sleep(0.5)

                download_item = page.get_by_role("menuitem", name="Download")
                if download_item.is_visible(timeout=3000):
                    try:
                        with page.expect_download(timeout=180_000) as dl_info:
                            download_item.click()
                        download = dl_info.value
                        download.save_as(str(output))

                        if output.exists() and output.stat().st_size > 1000:
                            size = output.stat().st_size
                            record.set_artifact("podcast", "ok", output=str(output), size_bytes=size)
                            log(f"  Audio saved: {output} ({size:,} bytes)")
                            return True
                    except Exception as e:
                        log(f"  Download via menu failed: {e}, trying play interception...")
                else:
                    # Close menu
                    page.keyboard.press("Escape")
                    time.sleep(0.3)

            # Method 2: Play interception — capture media URL
            log("  Trying play-interception method...")
            captured_url = {"value": None}

            def intercept_media(route):
                if route.request.resource_type == "media":
                    captured_url["value"] = route.request.url
                    route.abort()
                else:
                    route.continue_()

            page.route("**/*", intercept_media)
            play_btn = audio_item.locator("button[aria-label='Play']")

            if play_btn.is_visible(timeout=5000):
                play_btn.click()

                # Wait for media URL capture (5s)
                for _ in range(50):
                    if captured_url["value"]:
                        break
                    time.sleep(0.1)

                page.unroute("**/*")

                # Close audio player
                try:
                    page.locator("button[aria-label='Close audio player']").click()
                except Exception:
                    pass

                if captured_url["value"]:
                    # Download using browser context cookies
                    cookies = page.context.cookies()
                    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

                    import urllib.request
                    req = urllib.request.Request(captured_url["value"])
                    req.add_header("Cookie", cookie_str)
                    req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)")

                    with urllib.request.urlopen(req, timeout=180) as resp:
                        data = resp.read()
                        output.write_bytes(data)
                        record.set_artifact("podcast", "ok", output=str(output), size_bytes=len(data))
                        log(f"  Audio saved (intercepted): {output} ({len(data):,} bytes)")
                        return True
            else:
                page.unroute("**/*")

            log("  ERROR: Could not download audio via any method")
            record.set_artifact("podcast", "fail", error="all download methods failed")
            return False

        except Exception as e:
            log(f"  Audio download failed: {e}")
            record.set_artifact("podcast", "fail", error=str(e)[:200])
            return False


def get_share_link(page, record):
    """Enable public sharing and get the share link."""
    with StepTimer("get_share_link"):
        try:
            # Click share button
            for selector in [
                'button[aria-label*="Share" i]',
                'button:has-text("Share")',
            ]:
                try:
                    el = page.locator(selector).first
                    if el.is_visible(timeout=2000):
                        el.click()
                        time.sleep(2)
                        break
                except Exception:
                    continue

            # Enable "Anyone with the link" or public toggle
            for selector in [
                'button:has-text("Anyone with the link")',
                'button:has-text("Enable")',
                '[role="switch"]',
                'input[type="checkbox"]',
            ]:
                try:
                    el = page.locator(selector).first
                    if el.is_visible(timeout=2000):
                        el.click()
                        time.sleep(1)
                        break
                except Exception:
                    continue

            # Copy link
            link = ""
            for selector in [
                'button:has-text("Copy link")',
                'button[aria-label*="Copy" i]',
                'input[value*="notebooklm"]',
            ]:
                try:
                    el = page.locator(selector).first
                    if el.is_visible(timeout=2000):
                        # If it's an input, grab the value
                        tag = el.evaluate("el => el.tagName.toLowerCase()")
                        if tag == "input":
                            link = el.input_value()
                        else:
                            el.click()
                            time.sleep(0.5)
                            try:
                                link = page.evaluate("navigator.clipboard.readText()")
                            except Exception:
                                pass
                        if link:
                            break
                except Exception:
                    continue

            # Fallback: use the current page URL as the share link
            if not link:
                link = page.url

            _close_dialog(page)

            record.share_link = link
            log(f"  Share link: {link}")
            return link

        except Exception as e:
            log(f"  Get share link failed: {e}")
            record.errors.append(f"get_share_link: {e}")
            return page.url


# ── Main workflow ────────────────────────────────────────────────

def run_workflow(args):
    """Execute the full NLM Playwright workflow."""
    record = RunRecord(headless=args.headless)
    pw = browser = page = None

    try:
        # Resolve profile dir from credentials
        creds = load_credentials()
        profile_dir = None
        if creds:
            pd = creds.get("profile_dir")
            if pd:
                profile_dir = Path(pd).expanduser()

        log("=== NLM Playwright Automation ===")
        cdp_url = getattr(args, 'cdp_url', None)
        log(f"Headless: {args.headless}" + (f", CDP: {cdp_url}" if cdp_url else ""))

        # Launch browser
        with StepTimer("browser_launch"):
            if cdp_url:
                pw, browser, page = create_browser_cdp(cdp_url)
            else:
                pw, browser, page = create_browser(
                    headless=args.headless,
                    profile_dir=profile_dir,
                )
        log(f"  Browser launched" + (f" (CDP: {cdp_url})" if cdp_url else f" (profile: {profile_dir or DEFAULT_PROFILE_DIR})"))

        # Login
        if not ensure_login(page, creds):
            record.errors.append("login_failed")
            log("FATAL: Login failed. Aborting.")
            return 1

        # Test login only
        if args.test_login:
            log("Login test passed!")
            return 0

        # Create or open notebook
        if args.create:
            notebook_id = create_notebook(page, args.create, record)
            if not notebook_id:
                log("FATAL: Could not create notebook. Aborting.")
                return 1
        elif args.open:
            notebook_id = args.open
            notebook_url = f"{NLM_URL}/notebook/{notebook_id}"
            log(f"Opening existing notebook: {notebook_id}")
            page.goto(notebook_url, wait_until="domcontentloaded", timeout=30000)
            # Wait for artifact-library AND its contents to render.
            # The library skeleton appears immediately; the artifact items load ~3-8s later.
            try:
                page.wait_for_selector("artifact-library button[aria-label='More']", timeout=20000)
            except Exception:
                page.wait_for_timeout(8000)
            record.notebook = {"id": notebook_id, "url": notebook_url}
        else:
            log("No --create or --open specified, skipping notebook creation")
            return 0

        # Add URL sources
        if args.add_urls:
            log(f"Adding {len(args.add_urls)} URL sources...")
            added = 0
            failed = 0
            for url in args.add_urls:
                ok = add_url_source(page, url, record, timeout=90)
                if ok:
                    added += 1
                    log(f"  [{added}/{len(args.add_urls)}] {url}")
                else:
                    failed += 1
                    log(f"  FAIL: {url}")
            log(f"  Sources: {added} added, {failed} failed")

        # Add text source
        if args.add_text:
            text = args.add_text
            title = args.text_title or "Pasted Text"
            # If text looks like a file path, read it
            if os.path.isfile(text):
                text = Path(text).read_text()
            log(f"Adding text source: {title} ({len(text)} chars)")
            add_text_source(page, text, title, record, timeout=90)

        # Wait for all sources to settle
        time.sleep(5)

        # Generate report
        report_prompt = ""
        if args.report_prompt:
            report_prompt = read_file_text(args.report_prompt) or ""
            if not report_prompt:
                log(f"  Warning: could not read prompt file {args.report_prompt}")
        if args.report_prompt or args.create:
            log("Generating report...")
            generate_report(page, report_prompt, record)
            time.sleep(2)

        # Generate podcast
        if args.podcast_format:
            focus = ""
            if args.podcast_focus:
                focus = read_file_text(args.podcast_focus) or ""
            log(f"Generating podcast (format={args.podcast_format}, length={args.podcast_length})...")
            generate_podcast(page, args.podcast_format, args.podcast_length, focus, record)

        # Poll for completion
        if args.report_prompt or args.podcast_format:
            log("Polling studio for completion...")
            poll_studio(page, timeout=args.poll_timeout, poll_interval=30)

        # Download artifacts
        download_dir = Path(args.download_dir or f"{PROJECT_ROOT}/data/daily-feed")
        download_dir.mkdir(parents=True, exist_ok=True)
        prefix = args.output_prefix or datetime.now().strftime("%Y-%m-%d")

        if args.report_prompt or args.create:
            report_path = download_dir / f"{prefix}-report.md"
            log(f"Downloading report to {report_path}...")
            download_report(page, str(report_path), record)

        if args.podcast_format or args.open:
            audio_path = download_dir / f"{prefix}-podcast-raw.mp4"
            log(f"Downloading audio to {audio_path}...")
            download_audio(page, str(audio_path), record)

        # Share link
        if args.share:
            link = get_share_link(page, record)
            print(f"SHARE_LINK={link}")

        # Print notebook ID for pipeline integration
        nb_id = record.notebook.get("id", "")
        if nb_id:
            print(f"NOTEBOOK_ID={nb_id}")

        # Sync cookies to nlm CLI after successful workflow
        sync_cookies_to_nlm(browser, page)

        return 0

    except Exception as e:
        log(f"FATAL: {e}")
        record.errors.append(str(e))
        return 1

    finally:
        # Save operations log
        try:
            record.save()
        except Exception as e:
            log(f"  Warning: could not save operations log: {e}")

        # Clean up browser
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if pw:
            try:
                pw.stop()
            except Exception:
                pass


# ── CLI ──────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="NLM Playwright Automation — browser-driven NotebookLM workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Auth:
  --login          Open headed browser for manual Google sign-in (one-time)
  --test-login     Verify saved session is still valid

Workflow:
  --create TITLE   Create notebook → add sources → generate → download

Examples:
  # First time: login interactively
  .venv/bin/python3 scripts/nlm_playwright.py --login

  # Test session
  .venv/bin/python3 scripts/nlm_playwright.py --test-login --headless

  # Full pipeline
  .venv/bin/python3 scripts/nlm_playwright.py --headless \\
    --create "Daily Feed — 2026-03-04" \\
    --add-urls https://arxiv.org/abs/2503.01234 \\
    --podcast-format deep_dive \\
    --download-dir data/daily-feed/ \\
    --output-prefix 2026-03-04 --share
""",
    )

    # Auth commands
    parser.add_argument("--login", action="store_true",
                        help="Interactive login: open headed browser for manual Google sign-in")
    parser.add_argument("--sync-auth", action="store_true",
                        help="Sync Playwright session cookies to nlm CLI (headless, auto-login if needed)")

    # Actions
    parser.add_argument("--create", metavar="TITLE",
                        help="Create a new notebook with this title")
    parser.add_argument("--open", metavar="NOTEBOOK_ID",
                        help="Open an existing notebook by ID (for download-only runs)")
    parser.add_argument("--test-login", action="store_true",
                        help="Test login only, then exit")

    # Sources
    parser.add_argument("--add-urls", nargs="+", metavar="URL",
                        help="URLs to add as sources")
    parser.add_argument("--add-text", metavar="TEXT_OR_FILE",
                        help="Text content or file path to add as source")
    parser.add_argument("--text-title", metavar="TITLE", default="Pasted Text",
                        help="Title for the text source")

    # Report
    parser.add_argument("--report-prompt", metavar="FILE",
                        help="Path to custom report prompt file")

    # Podcast
    parser.add_argument("--podcast-format", metavar="FORMAT",
                        choices=["deep_dive", "brief", "critique", "debate"],
                        help="Podcast format")
    parser.add_argument("--podcast-length", metavar="LENGTH", default="default",
                        choices=["short", "default", "long"],
                        help="Podcast length")
    parser.add_argument("--podcast-focus", metavar="FILE",
                        help="Path to podcast focus/instructions file")

    # Output
    parser.add_argument("--download-dir", metavar="DIR",
                        help="Directory for downloaded artifacts")
    parser.add_argument("--output-prefix", metavar="PREFIX",
                        help="Prefix for output filenames (default: YYYY-MM-DD)")
    parser.add_argument("--share", action="store_true",
                        help="Enable public sharing and print link")

    # Browser
    parser.add_argument("--headless", action="store_true",
                        help="Run in headless mode (for cron/pipeline)")
    parser.add_argument("--no-headless", action="store_true",
                        help="Force headed mode (for debugging)")
    parser.add_argument("--cdp-url", metavar="URL",
                        help="Connect to Chrome via CDP (e.g., http://127.0.0.1:9222). "
                             "Uses system Chrome's Google session — no separate login needed.")

    # Polling
    parser.add_argument("--poll-timeout", type=int, default=10800,
                        help="Max seconds to poll for artifact completion (default: 10800 = 3hr)")

    return parser.parse_args()


def main():
    args = parse_args()

    # --login: interactive login mode
    if args.login:
        creds = load_credentials()
        profile_dir = None
        if creds:
            pd = creds.get("profile_dir")
            if pd:
                profile_dir = Path(pd).expanduser()
        do_interactive_login(profile_dir=profile_dir)
        return

    # --sync-auth: headless session sync to nlm CLI
    if args.sync_auth:
        sys.exit(do_sync_auth())
        return

    # --test-login only checks session status — force headless (no interaction)
    if args.test_login and not args.no_headless:
        args.headless = True

    # --no-headless overrides --headless
    if args.no_headless:
        args.headless = False

    sys.exit(run_workflow(args))


if __name__ == "__main__":
    main()
