#!/usr/bin/env python3
"""Rotate Google PSIDTS cookies to keep NotebookLM CLI auth alive.

Calls Google's RotateCookies endpoint to get fresh __Secure-1PSIDTS and
other rotating cookies, then persists them to the NLM CLI profile.

This is the same mechanism Chrome's RotateCookiesPage iframe uses.
Based on the proven approach from HanaokaYuzu/Gemini-API.

Falls back to CDP extraction from running Chrome if rotation returns 401
(core session dead but Chrome still has a live Google session).

Usage:
    python3 scripts/nlm_rotate_cookies.py          # Rotate and save
    python3 scripts/nlm_rotate_cookies.py --check   # Dry run (show what would change)
    python3 scripts/nlm_rotate_cookies.py --verify   # Rotate, save, then verify auth
    python3 scripts/nlm_rotate_cookies.py --cdp      # Force CDP extraction (skip rotation)

Exit codes:
    0 = rotation successful (or no rotation needed)
    1 = rotation failed (session dead, need nlm login)
    2 = cookies file not found
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Cookie storage paths (matches NLM CLI layout)
NLM_DIR = Path.home() / ".notebooklm-mcp-cli"
PROFILE_DIR = NLM_DIR / "profiles" / "default"
COOKIES_FILE = PROFILE_DIR / "cookies.json"
METADATA_FILE = PROFILE_DIR / "metadata.json"
AUTH_CACHE_FILE = NLM_DIR / "auth.json"

ROTATE_URL = "https://accounts.google.com/RotateCookies"
ROTATE_BODY = '[000,"-0000000000000000000"]'

# Cookies that are known to rotate and should be updated
ROTATING_COOKIES = {
    "__Secure-1PSIDTS", "__Secure-3PSIDTS",
    "SIDCC", "__Secure-1PSIDCC", "__Secure-3PSIDCC",
    "HSID", "SSID",
    "OSID", "__Secure-OSID",
}


def load_cookies() -> dict[str, str] | None:
    """Load cookies from NLM CLI profile."""
    if not COOKIES_FILE.exists():
        return None
    try:
        data = json.loads(COOKIES_FILE.read_text())
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def save_cookies(cookies: dict[str, str]) -> None:
    """Save updated cookies to NLM CLI profile and auth cache."""
    # Update profile cookies.json
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    COOKIES_FILE.write_text(json.dumps(cookies, indent=2))

    # Update metadata timestamp
    if METADATA_FILE.exists():
        try:
            meta = json.loads(METADATA_FILE.read_text())
            meta["last_validated"] = datetime.now().isoformat()
            METADATA_FILE.write_text(json.dumps(meta, indent=2))
        except (json.JSONDecodeError, KeyError):
            pass

    # Also update auth.json cache if it exists
    if AUTH_CACHE_FILE.exists():
        try:
            cache = json.loads(AUTH_CACHE_FILE.read_text())
            cache["cookies"] = cookies
            cache["extracted_at"] = time.time()
            AUTH_CACHE_FILE.write_text(json.dumps(cache, indent=2))
        except (json.JSONDecodeError, KeyError):
            pass


def rotate_cookies(cookies: dict[str, str], dry_run: bool = False) -> tuple[dict[str, str], list[str]]:
    """Call RotateCookies endpoint and return updated cookies.

    Returns:
        (updated_cookies, list_of_changed_cookie_names)

    Raises:
        SessionDeadError: If rotation returns 401 (core session expired)
    """
    import httpx

    cookie_jar = httpx.Cookies()
    for name, value in cookies.items():
        cookie_jar.set(name, value, domain=".google.com")

    response = httpx.post(
        ROTATE_URL,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/143.0.0.0 Safari/537.36",
        },
        cookies=cookie_jar,
        content=ROTATE_BODY,
        follow_redirects=False,
        timeout=15.0,
    )

    if response.status_code == 401:
        raise SessionDeadError("RotateCookies returned 401")

    if response.status_code == 302:
        location = response.headers.get("location", "")
        if "accounts.google.com" in location:
            raise SessionDeadError("RotateCookies redirected to login")

    response.raise_for_status()

    # Extract rotated cookies from Set-Cookie headers
    updated = dict(cookies)  # copy
    changed = []

    for cookie_header in response.headers.get_list("set-cookie"):
        if "=" not in cookie_header:
            continue
        name_value = cookie_header.split(";")[0].strip()
        eq_pos = name_value.index("=")
        name = name_value[:eq_pos]
        value = name_value[eq_pos + 1:]

        if name in updated and updated[name] != value:
            changed.append(name)
            if not dry_run:
                updated[name] = value
        elif name not in updated and name in ROTATING_COOKIES:
            changed.append(f"+{name}")
            if not dry_run:
                updated[name] = value

    return updated, changed


class SessionDeadError(Exception):
    """Core session cookies (SID etc.) have expired — rotation won't help."""
    pass


# ─── CDP fallback: extract fresh cookies from running Chrome ───

CDP_PORTS = range(9222, 9232)


def extract_cookies_via_cdp() -> dict[str, str] | None:
    """Extract fresh cookies from a running Chrome instance via CDP.

    Scans ports 9222-9231 for a Chrome DevTools endpoint, finds a
    NotebookLM tab (or any Google tab), and extracts all Google cookies.

    Returns:
        dict of cookie name→value, or None if Chrome/CDP unavailable
    """
    import websocket

    # Find Chrome CDP endpoint
    import httpx
    debugger_url = None
    nlm_ws_url = None

    # Try localhost first, then host.docker.internal (for Docker containers
    # reaching the host's Chrome). The ws:// URL from /json uses the same host.
    cdp_hosts = ["127.0.0.1"]
    if os.environ.get("PROJECT_ROOT") == "/app":
        # Running inside Docker — also try the host machine
        cdp_hosts.append("host.docker.internal")

    for host in cdp_hosts:
        for port in CDP_PORTS:
            try:
                resp = httpx.get(f"http://{host}:{port}/json/version", timeout=2)
                debugger_url = resp.json().get("webSocketDebuggerUrl")
                # Find NLM tab
                pages = httpx.get(f"http://{host}:{port}/json", timeout=2).json()
                for page in pages:
                    if "notebooklm.google.com" in page.get("url", ""):
                        nlm_ws_url = page.get("webSocketDebuggerUrl")
                        break
                # If no NLM tab, use any Google tab
                if not nlm_ws_url:
                    for page in pages:
                        if "google.com" in page.get("url", ""):
                            nlm_ws_url = page.get("webSocketDebuggerUrl")
                            break
                # Fix ws:// URL to use the reachable host (not 127.0.0.1 from inside Docker)
                if nlm_ws_url and host != "127.0.0.1":
                    nlm_ws_url = nlm_ws_url.replace("127.0.0.1", host).replace("localhost", host)
                if nlm_ws_url:
                    break
            except Exception:
                continue
        if nlm_ws_url:
            break

    if not nlm_ws_url:
        return None

    try:
        ws = websocket.create_connection(nlm_ws_url, timeout=10, suppress_origin=True)
        ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
        while True:
            resp = json.loads(ws.recv())
            if resp.get("id") == 1:
                break
        cookies_list = resp.get("result", {}).get("cookies", [])
        ws.close()
    except Exception:
        return None

    if not cookies_list:
        return None

    # Merge all Google-domain cookies (prefer notebooklm-specific values)
    cookies = {}
    for c in cookies_list:
        domain = c.get("domain", "")
        if "google" not in domain:
            continue
        name = c["name"]
        if name not in cookies or "notebooklm" in domain:
            cookies[name] = c["value"]

    # Validate minimum required cookies
    required = {"SID", "HSID", "SSID", "APISID", "SAPISID"}
    if not required.issubset(cookies.keys()):
        return None

    return cookies


def verify_auth() -> bool:
    """Quick auth check by listing notebooks."""
    import subprocess
    result = subprocess.run(
        [str(Path.home() / ".local" / "bin" / "nlm"), "login", "--check"],
        capture_output=True, text=True, timeout=30,
    )
    return result.returncode == 0


def main():
    dry_run = "--check" in sys.argv
    do_verify = "--verify" in sys.argv
    force_cdp = "--cdp" in sys.argv

    # ─── CDP-only mode ───
    if force_cdp:
        print("Extracting fresh cookies from Chrome via CDP...")
        cdp_cookies = extract_cookies_via_cdp()
        if cdp_cookies is None:
            print("ERROR: No Chrome CDP endpoint found or no Google cookies")
            sys.exit(1)
        # Immediately rotate to ensure PSIDTS is fresh
        try:
            cdp_cookies, changed = rotate_cookies(cdp_cookies)
            if changed:
                print(f"Post-CDP rotation: {len(changed)} cookies refreshed")
        except Exception:
            pass  # CDP cookies might still work without rotation
        save_cookies(cdp_cookies)
        print(f"Saved {len(cdp_cookies)} cookies from Chrome")
        if do_verify:
            print("Verifying auth...")
            if verify_auth():
                print("Auth OK")
            else:
                print("WARNING: Auth check failed after CDP extraction")
                sys.exit(1)
        sys.exit(0)

    # ─── Normal rotation flow ───
    cookies = load_cookies()
    if cookies is None:
        print("ERROR: No cookies found at", COOKIES_FILE)
        print("Run: ~/.local/bin/nlm login")
        sys.exit(2)

    print(f"Loaded {len(cookies)} cookies from profile")
    old_psidts = cookies.get("__Secure-1PSIDTS", "")[:20]
    print(f"Current PSIDTS: {old_psidts}...")

    try:
        updated, changed = rotate_cookies(cookies, dry_run=dry_run)
    except SessionDeadError as e:
        print(f"Session dead: {e}")
        print("Attempting CDP fallback (extracting from Chrome)...")
        cdp_cookies = extract_cookies_via_cdp()
        if cdp_cookies is None:
            print("ERROR: CDP fallback failed. No Chrome with CDP found.")
            print("Run: ~/.local/bin/nlm login")
            sys.exit(1)
        save_cookies(cdp_cookies)
        print(f"Recovered {len(cdp_cookies)} cookies from Chrome via CDP")
        if do_verify or True:  # always verify after CDP recovery
            if verify_auth():
                print("Auth OK (recovered via CDP)")
                sys.exit(0)
            else:
                print("WARNING: Auth still failing after CDP recovery")
                sys.exit(1)
        sys.exit(0)
    except Exception as e:
        print(f"ERROR: Rotation failed: {e}")
        sys.exit(1)

    if not changed:
        print("No cookies changed (already fresh)")
        sys.exit(0)

    new_psidts = updated.get("__Secure-1PSIDTS", "")[:20]
    print(f"Rotated {len(changed)} cookies: {', '.join(changed)}")
    print(f"New PSIDTS: {new_psidts}...")

    if dry_run:
        print("(dry run — no changes saved)")
        sys.exit(0)

    save_cookies(updated)
    print("Saved updated cookies to profile")

    if do_verify:
        print("Verifying auth...")
        if verify_auth():
            print("Auth OK")
        else:
            print("WARNING: Auth check failed after rotation")
            sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
