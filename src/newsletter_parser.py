#!/usr/bin/env python3
"""
Newsletter parser for OpenClaw Research Automation System.

Connects to a Gmail account via IMAP, fetches newsletter emails,
extracts and classifies links, and routes them to the appropriate
pipeline (research sources vs news queue).

Usage:
    python newsletter_parser.py                     # Fetch & process new newsletters
    python newsletter_parser.py --check             # Dry-run: show what would be extracted
    python newsletter_parser.py --since 2026-02-01  # Process since specific date
    python newsletter_parser.py --email-id <id>     # Process a single email by IMAP ID

Requires:
    - Gmail App Password stored in ~/.config/openclaw-research/email-credentials.json
    - beautifulsoup4, lxml, trafilatura, ural (installed in .venv)
"""

import argparse
import email
import imaplib
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from email.header import decode_header
from pathlib import Path
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

import requests
from bs4 import BeautifulSoup
from ural import normalize_url

from lib.file_utils import load_json, load_news_queue, save_json, now_iso

try:
    from lib.url_utils import normalize_url_deep
except ImportError:
    def normalize_url_deep(url):
        try:
            from ural import normalize_url
            return normalize_url(url, strip_trailing_slash=True) if url else url
        except Exception:
            return url

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SOURCES_FILE = DATA_DIR / "sources.json"
NEWS_QUEUE_FILE = DATA_DIR / "news-queue.json"
NEWSLETTERS_FILE = DATA_DIR / "newsletters.json"
CREDENTIALS_FILE = Path.home() / ".config" / "openclaw-research" / "email-credentials.json"

# ── Classification Domains ────────────────────────────────────

RESEARCH_DOMAINS = [
    "arxiv.org", "openreview.net", "papers.nips.cc",
    "aclanthology.org", "semanticscholar.org", "proceedings.mlr.press",
    "jmlr.org", "biorxiv.org", "doi.org", "scholar.google.com",
    "paperswithcode.com",
]

GITHUB_PATTERNS = ["github.com", "gitlab.com"]

# HuggingFace needs path-aware classification (handled in classify_url)
HUGGINGFACE_DOMAIN = "huggingface.co"

NEWS_DOMAINS = [
    "techcrunch.com", "theverge.com", "wired.com",
    "arstechnica.com", "venturebeat.com", "reuters.com",
    "bloomberg.com", "cnbc.com", "bbc.com", "nytimes.com",
    "wsj.com", "thenewstack.io", "infoworld.com",
    "zdnet.com", "cnet.com",
    # AI company domains (news articles)
    "deepmind.google", "meta.ai",
]

# AI company blog domains — always classify as "blog"
AI_BLOG_DOMAINS = [
    "blog.google", "ai.googleblog.com",
    "openai.com", "anthropic.com",
]

BLOG_PATTERNS = [
    "medium.com", "substack.com", "dev.to", "hashnode.dev",
    "wordpress.com", "ghost.io", "mirror.xyz",
    # Notable individual blogs
    "simonwillison.net", "lilianweng.github.io", "karpathy.github.io",
]

# Patterns for *.github.io/blog* style blogs
GITHUB_IO_BLOG_PATTERN = re.compile(r"^[a-z0-9-]+\.github\.io$")

JUNK_PATTERNS = [
    "unsubscribe", "click.", "track.", "list-manage",
    "mailchimp.com", "email.mg", "sendgrid.net",
    "view-in-browser", "manage-preferences", "email-preferences",
    "mailgun.net", "mandrillapp.com", "campaign-archive",
    "beehiiv.com/cdn-cgi", "convertkit.com",
    "twitter.com/intent", "facebook.com/sharer",
    "linkedin.com/shareArticle",
    # Social media post links from newsletters are noise
    "twitter.com/", "x.com/",
    # Email platform verification / magic links
    "magic.beehiiv.com",
    # Forms, surveys, referrals, subscribe links
    "typeform.com", "tally.so", "/safelist", "/referral",
    "sub_confirmation=",
    # Newsletter management
    "manage.kmail", "email-preferences", "subscription",
    # App store links (not content)
    "apps.apple.com", "play.google.com/store",
]

# Vague anchor texts that indicate boilerplate navigation, not content links.
# Matched case-insensitively after stripping whitespace.
BOILERPLATE_ANCHORS = {
    "here", "click here", "read more", "learn more", "view", "link",
    "this", "subscribe", "join", "get in touch", "sign up", "share",
    "forward", "guide", "view online", "view in browser",
    "manage preferences", "update preferences", "contact us",
    "website", "our website", "visit", "visit us", "details",
    "more info", "more details", "see more", "explore", "apply",
    "register", "rsvp", "download", "get started", "try it",
    "try now", "start free", "book a demo", "schedule",
    "advertise", "sponsor", "partner with us", "careers",
    # Social actions / newsletter chrome
    "like", "comment", "restack", "share this", "forward this",
    "subscribe here", "upgrade to paid", "read in app",
    "privacy policy", "terms of service", "terms", "unsubscribe",
    "manage subscription", "email preferences",
    # Short CTA labels
    "read", "open", "go", "see", "watch", "listen", "check",
}

# Prefixes that indicate boilerplate when the anchor starts with them
BOILERPLATE_PREFIXES = [
    "subscribe", "unsubscribe", "upgrade", "manage", "view in",
    "read in", "open in", "privacy", "terms of", "powered by",
    "follow us", "connect with", "join the", "join our",
    "check out", "access the", "download the", "download our",
    "get the", "get our", "grab the", "grab our", "claim your",
    "our previous", "our resources", "our website", "our youtube",
    "advertise", "become a", "partner with",
]

# URL path patterns that are never content (author pages, legal pages, etc.)
JUNK_URL_PATHS = [
    "/author/", "/authors/", "/privacy", "/terms", "/tos",
    "/cookie", "/legal", "/careers", "/jobs", "/about-us",
    "/contact", "/advertise", "/subscribe", "/signup", "/login",
    "/signin", "/account", "/settings", "/preferences",
]

# Social post URLs to filter (more specific — only status/post links)
SOCIAL_POST_PATTERNS = [
    re.compile(r"twitter\.com/[^/]+/status/"),
    re.compile(r"x\.com/[^/]+/status/"),
]

# Product homepages / ad platforms — not articles or content.
# These are tool landing pages that newsletters link to for sponsorship.
NON_CONTENT_DOMAINS = [
    "chatgpt.com", "claude.ai", "gemini.google.com",
    "cal.com", "notion.so", "airtable.com",
    "passionfroot.me",  # newsletter ad platform
    "try.gamma.app", "try.elevenlabs.io", "get.emergent.sh",
    "accounts.google.com",
    # Poll / rating / feedback platforms
    "/polls/",
]

YOUTUBE_PATTERNS = ["youtube.com", "youtu.be"]

# ── Helpers ───────────────────────────────────────────────────




def next_source_id(sources):
    existing = sources.get("sources", [])
    if not existing:
        return "src_001"
    max_num = 0
    for s in existing:
        m = re.match(r"src_(\d+)", s.get("id", ""))
        if m:
            max_num = max(max_num, int(m.group(1)))
    return f"src_{max_num + 1:03d}"


def next_news_id(news_queue):
    existing = news_queue.get("articles", [])
    if not existing:
        return "news_001"
    max_num = 0
    for a in existing:
        m = re.match(r"news_(\d+)", a.get("id", ""))
        if m:
            max_num = max(max_num, int(m.group(1)))
    return f"news_{max_num + 1:03d}"


def decode_mime_header(header_value):
    """Decode a MIME-encoded email header."""
    if not header_value:
        return ""
    parts = decode_header(header_value)
    decoded = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(data)
    return " ".join(decoded)


# ── URL Processing ────────────────────────────────────────────


def is_junk_url(url):
    """Check if a URL is a tracking/junk/non-content link."""
    url_lower = url.lower()
    if any(pattern in url_lower for pattern in JUNK_PATTERNS):
        return True
    if any(pattern in url_lower for pattern in NON_CONTENT_DOMAINS):
        return True
    return False


def _strip_emoji(text):
    """Remove emoji, special symbols, and leading/trailing punctuation."""
    # Remove common emoji and symbol characters
    text = re.sub(r'[\U0001F300-\U0001FAFF\u2600-\u27BF\u2700-\u27BF\u2B50\u23CF\u23E9-\u23F3\u23F8-\u23FA\u200D\uFE0F\u2764\u2B06\u2197\u2934\u274C\u2705\u26A0]', '', text)
    # Remove common newsletter decorators
    text = re.sub(r'^[→↗️👉📌🔗💡📎🎯🔥⚡💰🆕]+\s*', '', text)
    return text.strip()


def is_sponsored_content(anchor_text):
    """Check if anchor text contains an explicit sponsor/ad marker.

    Catches titles like "Redefine enterprise AI with Airia (Sponsor)" or
    "Deploy models with Crusoe [Sponsored]" — where the full title is
    meaningful but the sponsor tag marks it as paid placement.
    """
    if not anchor_text:
        return False
    clean = anchor_text.strip().lower()
    # Explicit markers newsletters use for paid placements
    sponsor_markers = [
        "(sponsor)", "(sponsored)", "[sponsor]", "[sponsored]",
        "(ad)", "[ad]", "(advertisement)", "[advertisement]",
        "(partner)", "[partner]", "(promoted)", "[promoted]",
    ]
    return any(marker in clean for marker in sponsor_markers)


def has_affiliate_url_signals(url):
    """Check raw URL for affiliate/paid tracking patterns before normalization.

    Must be called on the ORIGINAL url before canonicalize_url strips the
    query parameters.  Catches items like Proton Mail (aff_id=...) and
    Oxylabs (go2cloud.org/aff_c) that carry no text marker.
    """
    if not url:
        return False
    lower = url.lower()
    affiliate_patterns = [
        "aff_id=", "aff_type=", "aff_sub",     # Affiliate network params
        "/aff_ad", "/aff_c",                     # Affiliate routing paths
        "offer_id=", "campaign_id=",             # Offer tracking
        "s_category=paid", "s_source=paid",      # Paid category tracking
        "utm_ads=",                              # UTM ads parameter
        "go2cloud.org",                          # Affiliate redirect domain
    ]
    return any(p in lower for p in affiliate_patterns)


def is_boilerplate_anchor(anchor_text):
    """Check if anchor text is vague boilerplate (not a real article title)."""
    if not anchor_text:
        return True
    clean = _strip_emoji(anchor_text).strip().lower()
    if not clean or len(clean) < 3:
        return True
    # Exact match
    if clean in BOILERPLATE_ANCHORS:
        return True
    # Strip trailing punctuation for matching ("Get in touch today." → "get in touch today")
    clean_stripped = clean.rstrip(".,!?:")
    if clean_stripped in BOILERPLATE_ANCHORS:
        return True
    # Prefix match ("subscribe here", "unsubscribe from this list", etc.)
    if any(clean.startswith(prefix) for prefix in BOILERPLATE_PREFIXES):
        return True
    if any(clean_stripped.startswith(prefix) for prefix in BOILERPLATE_PREFIXES):
        return True
    return False


def is_junk_url_path(url):
    """Check if the URL path is a non-content page (author, legal, login, etc.)."""
    try:
        path = urlparse(url).path.lower()
        return any(p in path for p in JUNK_URL_PATHS)
    except Exception:
        return False


def canonicalize_url(url):
    """Normalize a URL using deep normalization from url_utils.

    Falls back to ural's normalize_url if deep normalization fails.
    """
    try:
        return normalize_url_deep(url)
    except Exception:
        try:
            return normalize_url(url, strip_trailing_slash=True)
        except Exception:
            return url


def resolve_redirect(url, timeout=10):
    """Follow tracking redirects to get the actual destination URL."""
    try:
        resp = requests.head(
            url, allow_redirects=True, timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; OpenClawBot/1.0)"},
        )
        return resp.url
    except Exception:
        # If HEAD fails, try GET with stream to avoid downloading body
        try:
            resp = requests.get(
                url, allow_redirects=True, timeout=timeout, stream=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; OpenClawBot/1.0)"},
            )
            resp.close()
            return resp.url
        except Exception:
            return url


def classify_url(url):
    """Classify a URL into: research, github, news, blog, youtube, or unknown."""
    if not url:
        return "unknown"
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    path = parsed.path.lower()

    # Social media post links — filter as junk (noise from newsletters)
    if any(p.search(url) for p in SOCIAL_POST_PATTERNS):
        return "social"

    # Research papers
    if any(d in domain for d in RESEARCH_DOMAINS):
        return "research"

    # HuggingFace — path-aware classification
    if HUGGINGFACE_DOMAIN in domain:
        path_parts = [p for p in path.split("/") if p]
        if path_parts and path_parts[0] == "papers":
            return "research"  # huggingface.co/papers/* → research
        if len(path_parts) >= 2:
            return "github"  # huggingface.co/{user}/{repo} → github/oss
        return "unknown"

    # GitHub/GitLab repos (must have at least user/repo path)
    if any(d in domain for d in GITHUB_PATTERNS):
        path_parts = [p for p in path.split("/") if p]
        if len(path_parts) >= 2:
            return "github"

    # YouTube — only actual videos, not channel/profile pages
    if any(d in domain for d in YOUTUBE_PATTERNS):
        # Must be a video URL (watch?v=, /shorts/, /embed/, /v/, or youtu.be/ID)
        if ("watch?" in url and "v=" in url) or \
           "/shorts/" in path or "/embed/" in path or "/v/" in path:
            return "youtube"
        if "youtu.be" in domain:
            yt_parts = [p for p in path.split("/") if p]
            if len(yt_parts) == 1 and len(yt_parts[0]) == 11:
                return "youtube"
        # Channel pages (@user, /channel/, /c/, /@) and subscribe links are not content
        return "unknown"

    # AI company blog domains (always blog)
    if any(d in domain for d in AI_BLOG_DOMAINS):
        return "blog"

    # News sites (check for blog paths within news domains)
    if any(d in domain for d in NEWS_DOMAINS):
        if "/blog" in path or "/research" in path:
            return "blog"
        return "news"

    # Blog platforms
    if any(p in domain for p in BLOG_PATTERNS):
        return "blog"

    # *.github.io blogs
    if GITHUB_IO_BLOG_PATTERN.match(domain):
        return "blog"

    # Check path for /blog/ indicator
    if "/blog/" in path or "/posts/" in path or "/articles/" in path:
        return "blog"

    return "unknown"


def extract_title_from_url(url):
    """Extract a title hint from the URL path."""
    if not url:
        return "Untitled"
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]
    if path_parts:
        title = path_parts[-1]
        # Clean up common URL slugs
        title = title.replace("-", " ").replace("_", " ")
        # Remove file extensions
        title = re.sub(r"\.\w{2,4}$", "", title)
        return title.title()[:80]
    return parsed.netloc


# ── Email Fetching ────────────────────────────────────────────


def load_credentials():
    """Load IMAP credentials from config file."""
    if not CREDENTIALS_FILE.exists():
        print(f"ERROR: Credentials file not found: {CREDENTIALS_FILE}")
        print("Create it with:")
        print(json.dumps({
            "email": "your-research-email@gmail.com",
            "app_password": "xxxx xxxx xxxx xxxx",
            "imap_host": "imap.gmail.com",
            "imap_port": 993,
            "plus_alias": "ai",
        }, indent=2))
        sys.exit(1)
    return load_json(CREDENTIALS_FILE)


def connect_imap(creds):
    """Connect to IMAP server and login."""
    try:
        mail = imaplib.IMAP4_SSL(creds["imap_host"], creds.get("imap_port", 993))
        mail.login(creds["email"], creds["app_password"])
        return mail
    except Exception as e:
        print(f"IMAP connection failed: {e}")
        return None


def fetch_newsletters(mail, since_date=None, folder="INBOX", plus_alias=None):
    """Fetch newsletter emails from IMAP.

    Args:
        mail: IMAP connection
        since_date: Only fetch emails after this date (ISO string or None for last 7 days)
        folder: IMAP folder to search
        plus_alias: Gmail + alias to filter by (e.g., "ai" for user+ai@gmail.com)
    """
    mail.select(f'"{folder}"')

    # Build search criteria
    if since_date:
        date_str = datetime.fromisoformat(since_date.replace("Z", "+00:00")).strftime("%d-%b-%Y")
    else:
        date_str = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%d-%b-%Y")

    search_criteria = f'SINCE "{date_str}"'

    # If using + alias, filter by the To: address
    if plus_alias:
        # Gmail search: to address contains +alias@
        search_criteria = f'SINCE "{date_str}" TO "+{plus_alias}@"'

    status, messages = mail.search(None, search_criteria)
    if status != "OK":
        return []

    msg_ids = messages[0].split()
    return msg_ids


def parse_email(mail, msg_id):
    """Fetch and parse a single email, returning metadata and HTML body."""
    status, data = mail.fetch(msg_id, "(RFC822)")
    if status != "OK" or not data or not data[0]:
        return None

    try:
        msg = email.message_from_bytes(data[0][1])
    except (IndexError, TypeError):
        return None

    # Decode headers
    subject = decode_mime_header(msg.get("Subject", ""))
    sender = decode_mime_header(msg.get("From", ""))
    date_str = msg.get("Date", "")
    to_addr = decode_mime_header(msg.get("To", ""))

    # Extract HTML body
    html_body = None
    text_body = None
    for part in msg.walk():
        content_type = part.get_content_type()
        if content_type == "text/html" and html_body is None:
            payload = part.get_payload(decode=True)
            if payload:
                try:
                    charset = part.get_content_charset() or "utf-8"
                    html_body = payload.decode(charset, errors="replace")
                except (LookupError, AttributeError):
                    html_body = payload.decode("utf-8", errors="replace")
        elif content_type == "text/plain" and text_body is None:
            payload = part.get_payload(decode=True)
            if payload:
                try:
                    charset = part.get_content_charset() or "utf-8"
                    text_body = payload.decode(charset, errors="replace")
                except (LookupError, AttributeError):
                    text_body = payload.decode("utf-8", errors="replace")

    return {
        "subject": subject,
        "sender": sender,
        "date": date_str,
        "to": to_addr,
        "html_body": html_body,
        "text_body": text_body,
        "msg_id": msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id),
    }


# ── Link Extraction ──────────────────────────────────────────


def extract_links(html_body):
    """Extract all meaningful links from newsletter HTML."""
    if not html_body:
        return []

    soup = BeautifulSoup(html_body, "lxml")
    links = []
    seen_urls = set()

    for a_tag in soup.find_all("a", href=True):
        url = a_tag["href"].strip()
        anchor_text = a_tag.get_text(strip=True)

        # Skip empty/trivial anchors
        if not url or url.startswith("mailto:") or url.startswith("#"):
            continue
        if not url.startswith("http"):
            continue

        # Skip junk/tracking URLs
        if is_junk_url(url):
            continue

        # Skip image-only links with no text (tracking pixels)
        if not anchor_text or len(anchor_text) < 3:
            # Check if it wraps an image
            if a_tag.find("img") and not anchor_text:
                continue

        # Skip sponsored/paid placement links ("... (Sponsor)", "[Ad]", etc.)
        if is_sponsored_content(anchor_text):
            continue

        # Skip affiliate/paid tracking URLs (check raw URL before normalization)
        if has_affiliate_url_signals(url):
            continue

        # Skip boilerplate navigation links ("here", "guide", "click here", etc.)
        if is_boilerplate_anchor(anchor_text):
            continue

        # Resolve tracking redirects
        resolved = resolve_redirect(url)
        canonical = canonicalize_url(resolved)

        # Re-check junk on the resolved URL (original may have been a redirect)
        if is_junk_url(canonical) or is_junk_url(resolved):
            continue

        # Check for non-content URL paths (author pages, login, legal, etc.)
        if is_junk_url_path(canonical):
            continue

        # Dedup within this email
        if canonical in seen_urls:
            continue
        seen_urls.add(canonical)

        links.append({
            "url": canonical,
            "original_url": url,
            "anchor_text": anchor_text or "",
        })

    return links


# ── Routing ───────────────────────────────────────────────────


def is_duplicate_source(url, sources):
    """Check if URL already exists in sources.json (using deep normalization)."""
    if not url:
        return False
    norm = normalize_url_deep(url)
    for s in sources.get("sources", []):
        existing_url = s.get("url")
        if existing_url and normalize_url_deep(existing_url) == norm:
            return True
    return False


def is_duplicate_news(url, news_queue):
    """Check if URL already exists in news-queue.json (using deep normalization)."""
    if not url:
        return False
    norm = normalize_url_deep(url)
    for a in news_queue.get("articles", []):
        for field in ("canonical_url", "url"):
            existing_url = a.get(field)
            if existing_url and normalize_url_deep(existing_url) == norm:
                return True
    return False


def is_duplicate_cross_stream(url, sources, news_queue):
    """Check if URL exists in the OTHER data file (cross-stream dedup)."""
    if not url:
        return False
    norm = normalize_url_deep(url)
    # Check sources
    for s in sources.get("sources", []):
        existing_url = s.get("url")
        if existing_url and normalize_url_deep(existing_url) == norm:
            return True
    # Check news
    for a in news_queue.get("articles", []):
        for field in ("canonical_url", "url"):
            existing_url = a.get(field)
            if existing_url and normalize_url_deep(existing_url) == norm:
                return True
    return False


def route_links(links, newsletter_id, newsletter_name):
    """Classify links and add them to the appropriate data file.

    Returns a summary dict with counts.
    """
    sources = load_json(SOURCES_FILE)
    news_queue = load_news_queue(NEWS_QUEUE_FILE)
    categories = load_json(DATA_DIR / "categories.json")

    stats = {"research": 0, "github": 0, "news": 0, "blog": 0, "youtube": 0, "social": 0, "skipped": 0, "duplicate": 0}

    for link in links:
        url = link["url"]
        if not url:
            continue
        classification = classify_url(url)
        title = link.get("anchor_text") or extract_title_from_url(url) or "Untitled"

        # Social media links are noise from newsletters — skip
        if classification == "social":
            stats["social"] += 1
            continue

        if classification in ("research", "github", "youtube"):
            # Route to existing research pipeline (sources.json)
            if is_duplicate_source(url, sources):
                stats["duplicate"] += 1
                continue
            # Cross-stream: also check news queue
            if is_duplicate_news(url, news_queue):
                stats["duplicate"] += 1
                continue

            sid = next_source_id(sources)
            # Map classification to source type
            if classification == "youtube":
                src_type = "youtube"
            else:
                src_type = "url"

            # Determine category using URL patterns then keyword matching
            cat = _categorize_by_url(url, categories)
            if not cat:
                cat = _categorize_by_keywords(title, categories)
            if not cat:
                cat = "general"

            sources["sources"].append({
                "id": sid,
                "type": src_type,
                "origin": "newsletter",
                "url": url,
                "file_path": None,
                "title": title[:120],
                "category": cat,
                "tags": [],
                "added_at": now_iso(),
                "status": "queued",
                "notebook_id": None,
                "from_account": None,
                "from_newsletter": newsletter_id,
            })
            stats[classification] += 1

        elif classification in ("news", "blog"):
            # Route to news pipeline (news-queue.json)
            if is_duplicate_news(url, news_queue):
                stats["duplicate"] += 1
                continue
            # Cross-stream: also check sources
            if is_duplicate_source(url, sources):
                stats["duplicate"] += 1
                continue

            nid = next_news_id(news_queue)
            # Also assign a fine-grained category for daily feed sections
            fine_cat = _categorize_by_keywords(title, categories) or "general"
            news_queue["articles"].append({
                "id": nid,
                "url": url,
                "canonical_url": url,
                "title": title[:120],
                "source_newsletter": newsletter_id,
                "source_newsletter_name": newsletter_name,
                "extracted_at": now_iso(),
                "category": fine_cat,
                "content_type": classification,
                "content_snippet": None,
                "status": "queued",
                "digest_id": None,
            })
            stats[classification] += 1

        elif classification == "unknown":
            # Only route unknown links to news if anchor text looks like a real title
            # (at least 3 words or 20+ chars) — otherwise it's likely boilerplate
            if len(title.split()) < 3 and len(title) < 20:
                stats["skipped"] += 1
                continue

            if is_duplicate_news(url, news_queue):
                stats["duplicate"] += 1
                continue
            # Cross-stream: also check sources
            if is_duplicate_source(url, sources):
                stats["duplicate"] += 1
                continue

            nid = next_news_id(news_queue)
            fine_cat = _categorize_by_keywords(title, categories) or "general"
            news_queue["articles"].append({
                "id": nid,
                "url": url,
                "canonical_url": url,
                "title": title[:120],
                "source_newsletter": newsletter_id,
                "source_newsletter_name": newsletter_name,
                "extracted_at": now_iso(),
                "category": fine_cat,
                "content_type": "uncategorized",
                "content_snippet": None,
                "status": "queued",
                "digest_id": None,
            })
            stats["news"] += 1
        else:
            stats["skipped"] += 1

    save_json(SOURCES_FILE, sources)
    save_json(NEWS_QUEUE_FILE, news_queue)

    return stats


def _categorize_by_url(url, categories):
    """Categorize by URL pattern matching from categories.json."""
    if not url:
        return None
    for cat in categories.get("categories", []):
        for pattern in cat.get("url_patterns", []):
            if pattern in url:
                return cat.get("slug")
    return None


def _categorize_by_keywords(text, categories):
    """Categorize by keyword matching from categories.json."""
    if not text:
        return None
    text_lower = text.lower()
    best_slug = None
    best_count = 0
    for cat in categories.get("categories", []):
        if cat.get("slug") == "general":
            continue
        count = sum(1 for kw in cat.get("keywords", []) if kw.lower() in text_lower)
        if count > best_count:
            best_count = count
            best_slug = cat.get("slug")
    return best_slug


# ── Newsletter Tracking ──────────────────────────────────────


def find_or_create_newsletter(sender, subject, newsletters_data):
    """Find existing newsletter by sender pattern or create a new one."""
    sender_lower = sender.lower()

    for nl in newsletters_data.get("newsletters", []):
        if nl["sender_pattern"].lower() in sender_lower:
            return nl["id"], nl["name"]

    # Auto-create: extract a name from the sender
    # "TLDR AI <dan@tldrnewsletter.com>" -> "TLDR AI"
    name_match = re.match(r'^"?([^"<]+)"?\s*<', sender)
    name = name_match.group(1).strip() if name_match else sender.split("@")[0]

    # Extract email from sender
    email_match = re.search(r'<([^>]+)>', sender)
    sender_email = email_match.group(1) if email_match else sender

    nl_id = f"nl_{len(newsletters_data.get('newsletters', [])) + 1:03d}"
    newsletters_data["newsletters"].append({
        "id": nl_id,
        "name": name,
        "sender_pattern": sender_email,
        "content_type": "mixed",
        "added_at": now_iso(),
        "last_checked": None,
        "total_extracted": 0,
        "status": "active",
    })
    save_json(NEWSLETTERS_FILE, newsletters_data)

    return nl_id, name


# ── Main Pipeline ────────────────────────────────────────────


def process_newsletters(since_date=None, dry_run=False, single_id=None):
    """Main entry point: fetch, parse, extract, classify, route."""
    creds = load_credentials()
    newsletters_data = load_json(NEWSLETTERS_FILE)

    # Use last_global_check if no date specified
    if not since_date:
        since_date = newsletters_data.get("config", {}).get("last_global_check")

    plus_alias = creds.get("plus_alias")

    print(f"Connecting to {creds['imap_host']}...")
    mail = connect_imap(creds)
    if mail is None:
        print("ERROR: Could not connect to IMAP server. Aborting.")
        return {"research": 0, "github": 0, "news": 0, "blog": 0, "youtube": 0, "social": 0, "skipped": 0, "duplicate": 0}

    folder = newsletters_data.get("config", {}).get("check_folder", "INBOX")
    print(f"Searching folder '{folder}' since {since_date or 'last 7 days'}...")

    if single_id:
        msg_ids = [single_id.encode() if isinstance(single_id, str) else single_id]
    else:
        msg_ids = fetch_newsletters(mail, since_date, folder, plus_alias)

    print(f"Found {len(msg_ids)} email(s) to process.")

    total_stats = {"research": 0, "github": 0, "news": 0, "blog": 0, "youtube": 0, "social": 0, "skipped": 0, "duplicate": 0}
    processed_emails = 0

    for i, msg_id in enumerate(msg_ids, 1):
        email_data = parse_email(mail, msg_id)
        if not email_data:
            continue

        print(f"\n[{i}/{len(msg_ids)}] {email_data['sender'][:50]}")
        print(f"  Subject: {email_data['subject'][:70]}")

        # Find or auto-create newsletter entry
        nl_id, nl_name = find_or_create_newsletter(
            email_data["sender"],
            email_data["subject"],
            newsletters_data,
        )

        # Extract links
        links = extract_links(email_data["html_body"])
        print(f"  Links found: {len(links)}")

        if dry_run:
            for link in links:
                cls = classify_url(link["url"])
                print(f"    [{cls:8s}] {link['anchor_text'][:40]:40s} → {link['url'][:60]}")
            continue

        # Route links
        stats = route_links(links, nl_id, nl_name)
        for k, v in stats.items():
            total_stats[k] += v

        # Update newsletter tracking
        for nl in newsletters_data.get("newsletters", []):
            if nl["id"] == nl_id:
                nl["last_checked"] = now_iso()
                nl["total_extracted"] = nl.get("total_extracted", 0) + sum(
                    v for k, v in stats.items() if k not in ("skipped", "duplicate")
                )

        processed_emails += 1
        print(f"  Routed: research={stats['research']}, github={stats['github']}, "
              f"news={stats['news']}, blog={stats['blog']}, youtube={stats['youtube']}, "
              f"social={stats['social']}, dup={stats['duplicate']}")

    # Update global check timestamp
    if not dry_run and not single_id:
        newsletters_data.setdefault("config", {})["last_global_check"] = now_iso()
        save_json(NEWSLETTERS_FILE, newsletters_data)

    mail.logout()

    # Summary
    print(f"\n{'=' * 50}")
    print(f"Processed {processed_emails} email(s)")
    print(f"Research papers:  {total_stats['research']}")
    print(f"GitHub repos:     {total_stats['github']}")
    print(f"News articles:    {total_stats['news']}")
    print(f"Blog posts:       {total_stats['blog']}")
    print(f"YouTube videos:   {total_stats['youtube']}")
    print(f"Social (skipped): {total_stats['social']}")
    print(f"Duplicates:       {total_stats['duplicate']}")
    print(f"Skipped:          {total_stats['skipped']}")

    return total_stats


def main():
    parser = argparse.ArgumentParser(description="Newsletter parser for OpenClaw Research")
    parser.add_argument("--check", action="store_true", help="Dry-run: show extracted links without saving")
    parser.add_argument("--since", type=str, help="Process emails since date (ISO format, e.g., 2026-02-01)")
    parser.add_argument("--email-id", type=str, help="Process a single email by IMAP ID")
    args = parser.parse_args()

    process_newsletters(
        since_date=args.since,
        dry_run=args.check,
        single_id=args.email_id,
    )


if __name__ == "__main__":
    main()
