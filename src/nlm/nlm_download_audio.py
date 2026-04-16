#!/usr/bin/env python3
"""Download NLM audio by resolving Google CDN redirect chain with Chrome CDP cookies.

The NLM CLI's `nlm download audio` fails with TooManyRedirects because httpx
strips cookies on cross-domain redirects. This script works around the bug by:
1. Getting the audio URL from NLM's studio metadata
2. Extracting domain-aware cookies from Chrome via CDP
3. Manually following the redirect chain, sending matching cookies per hop
4. Streaming the audio from the final googlevideo CDN URL

Usage:
    python3 nlm_download_audio.py <notebook_id> --output <path>
    python3 nlm_download_audio.py <notebook_id>  # outputs to <notebook_id>_audio.mp4

Requires:
    - Chrome running with --remote-debugging-port (any port in 9222-9231)
    - websocket-client (pip install websocket-client)
    - NLM CLI cookies in ~/.notebooklm-mcp-cli/profiles/default/cookies.json
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    import websocket
except ImportError:
    print("ERROR: websocket-client required. Install: pip install websocket-client", file=sys.stderr)
    sys.exit(1)

try:
    import requests
except ImportError:
    print("ERROR: requests required. Install: pip install requests", file=sys.stderr)
    sys.exit(1)


NLM = os.path.expanduser("~/.local/bin/nlm")
COOKIE_PATH = os.path.expanduser("~/.notebooklm-mcp-cli/profiles/default/cookies.json")
CDP_PORTS = range(9222, 9232)
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"


def get_audio_url(notebook_id: str) -> str | None:
    """Get the audio download URL from NLM studio metadata."""
    # Add NLM packages to path
    nlm_pkg = Path.home() / ".local/share/uv/tools/notebooklm-mcp-cli/lib"
    for p in nlm_pkg.glob("python*/site-packages"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))

    try:
        from notebooklm_tools.core.client import NotebookLMClient

        with open(COOKIE_PATH) as f:
            cookies = json.load(f)

        client = NotebookLMClient(cookies)
        artifacts = client._list_raw(notebook_id)

        for a in artifacts:
            if isinstance(a, list) and len(a) > 4 and a[2] == 1 and a[4] == 3:
                # Type 1 = audio, Status 3 = completed
                meta = a[6]
                if isinstance(meta, list) and len(meta) > 5:
                    media_list = meta[5]
                    if isinstance(media_list, list):
                        # Prefer m140 format (direct audio)
                        for item in media_list:
                            if isinstance(item, list) and len(item) >= 3 and item[2] == "audio/mp4":
                                url = item[0]
                                if "=m140" in url and "-dv" not in url:
                                    return url
                        # Fallback to first audio/mp4
                        for item in media_list:
                            if isinstance(item, list) and len(item) >= 3 and item[2] == "audio/mp4":
                                return item[0]
                        # Last resort: first URL
                        if media_list and isinstance(media_list[0], list):
                            return media_list[0][0]
    except Exception as e:
        print(f"WARN: Could not get audio URL via API: {e}", file=sys.stderr)
    return None


def get_chrome_cookies() -> dict:
    """Extract all cookies from Chrome via CDP."""
    for port in CDP_PORTS:
        try:
            result = subprocess.run(
                ["curl", "-s", f"http://localhost:{port}/json"],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode != 0:
                continue

            targets = json.loads(result.stdout)
            # Prefer a Google page target
            target = None
            for t in targets:
                if t.get("type") == "page" and "google" in t.get("url", "") and t.get("webSocketDebuggerUrl"):
                    target = t
                    break
            if not target:
                for t in targets:
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        target = t
                        break

            if target:
                ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=5)
                ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
                resp = json.loads(ws.recv())
                ws.close()

                cookies = resp.get("result", {}).get("cookies", [])
                if cookies:
                    # Build domain-aware cookie index: (domain, name) -> value
                    result_cookies = {}
                    for c in cookies:
                        domain = c.get("domain", "")
                        if "google" in domain or "youtube" in domain:
                            result_cookies[(domain, c["name"])] = c["value"]
                    return result_cookies
        except Exception:
            continue
    return {}


def cookies_for_url(url: str, domain_cookies: dict) -> str:
    """Get cookie header string for a specific URL's domain."""
    host = urlparse(url).hostname or ""
    matching = {}
    for (domain, name), value in domain_cookies.items():
        if domain.startswith("."):
            if host.endswith(domain) or host == domain[1:]:
                matching[name] = value
        elif host == domain:
            matching[name] = value
    return "; ".join(f"{k}={v}" for k, v in matching.items())


def download_with_redirects(url: str, output_path: str, domain_cookies: dict) -> bool:
    """Follow redirect chain with domain-aware cookies and download audio."""
    max_hops = 15

    for hop in range(max_hops):
        cookie_str = cookies_for_url(url, domain_cookies)
        headers = {"Cookie": cookie_str, "User-Agent": USER_AGENT}

        resp = requests.get(url, headers=headers, allow_redirects=False,
                          timeout=600, stream=True)

        if resp.status_code == 200:
            ct = resp.headers.get("Content-Type", "")
            if ct.startswith("audio/") or ct.startswith("video/") or "octet-stream" in ct:
                cl = int(resp.headers.get("Content-Length", 0))
                total = 0
                with open(output_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=262144):
                        f.write(chunk)
                        total += len(chunk)
                        if cl and total % (5 * 1024 * 1024) < 262144:
                            print(f"  {total // (1024*1024)}/{cl // (1024*1024)} MB", flush=True)
                print(f"  Downloaded {total // (1024*1024)} MB")
                return True
            else:
                print(f"WARN: Got 200 but Content-Type={ct}", file=sys.stderr)
                resp.close()
                return False
        elif resp.status_code in (301, 302, 303, 307, 308):
            url = resp.headers["Location"]
            resp.close()
        else:
            print(f"ERROR: HTTP {resp.status_code} at hop {hop}", file=sys.stderr)
            resp.close()
            return False

    print("ERROR: Too many redirects", file=sys.stderr)
    return False


def main():
    parser = argparse.ArgumentParser(description="Download NLM audio via Chrome CDP cookies")
    parser.add_argument("notebook_id", help="Notebook ID or alias")
    parser.add_argument("--output", "-o", help="Output file path")
    args = parser.parse_args()

    notebook_id = args.notebook_id
    output_path = args.output or f"{notebook_id}_audio.mp4"

    # Resolve alias
    try:
        result = subprocess.run([NLM, "alias", "get", notebook_id],
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            resolved = result.stdout.strip()
            # Check if it looks like a UUID
            if len(resolved) == 36 and resolved.count("-") == 4:
                notebook_id = resolved
    except Exception:
        pass

    print(f"Downloading audio for notebook {notebook_id}")

    # Step 1: Get audio URL from NLM metadata
    audio_url = get_audio_url(notebook_id)
    if not audio_url:
        print("ERROR: No completed audio found in notebook", file=sys.stderr)
        sys.exit(1)
    print(f"  Audio URL: {audio_url[:80]}...")

    # Step 2: Get cookies from Chrome
    domain_cookies = get_chrome_cookies()
    if not domain_cookies:
        print("ERROR: Could not extract cookies from Chrome (is it running with --remote-debugging-port?)", file=sys.stderr)
        sys.exit(1)
    print(f"  Chrome cookies: {len(domain_cookies)} entries")

    # Step 3: Download with redirect following
    print("  Following redirect chain...")
    if download_with_redirects(audio_url, output_path, domain_cookies):
        print(f"OK: {output_path}")
        sys.exit(0)
    else:
        # Clean up partial file
        if os.path.exists(output_path):
            os.unlink(output_path)
        print("FAILED: Audio download failed", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
