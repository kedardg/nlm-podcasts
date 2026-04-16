#!/usr/bin/env python3
"""
Shared URL normalization for OpenClaw Research Automation System.

Single source of truth for deep URL normalization across all pipelines:
newsletter_parser.py, router.py, data_triage.py.

Key function: normalize_url_deep(url) -> str
Also exports: dedup_urls(sources, news) -> dict with removal reports
"""

import json
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# Tracking parameters to strip
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_cid",
    "fbclid", "gclid", "gclsrc",
    "mc_cid", "mc_eid",
    "oly_enc_id", "oly_anon_id",
    "ref", "source", "s", "si",
    "_hsenc", "_hsmi", "hsa_acc", "hsa_cam", "hsa_grp", "hsa_ad",
    "hsa_src", "hsa_tgt", "hsa_kw", "hsa_mt", "hsa_net", "hsa_ver",
    "mkt_tok",
    "ck_subscriber_id",
    "vero_id",
    "sref", "smid", "smtyp",
    # beehiiv / newsletter tracking
    "_bhlid", "_bhiiv", "bhcl_id", "dub_id",
    # substack
    "publication_id", "post_id", "isFreemail", "token", "r", "triedRedirect",
    # general newsletter tracking
    "sid", "last_resource_guid",
}

# arXiv pattern: extract paper ID (strip version suffix for normalization)
ARXIV_ID_PATTERN = re.compile(
    r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?$"
)

# YouTube video ID extraction patterns
YOUTUBE_PATTERNS = [
    # youtube.com/watch?v=ID
    re.compile(r"youtube\.com/watch\?.*v=([a-zA-Z0-9_-]{11})"),
    # youtu.be/ID
    re.compile(r"youtu\.be/([a-zA-Z0-9_-]{11})"),
    # youtube.com/embed/ID
    re.compile(r"youtube\.com/embed/([a-zA-Z0-9_-]{11})"),
    # youtube.com/v/ID
    re.compile(r"youtube\.com/v/([a-zA-Z0-9_-]{11})"),
    # youtube.com/shorts/ID
    re.compile(r"youtube\.com/shorts/([a-zA-Z0-9_-]{11})"),
]


def normalize_url_deep(url):
    """Deep URL normalization — canonical form for dedup comparison.

    Rules:
    - Lowercase scheme + domain, strip www., strip trailing slash, strip fragment
    - Strip tracking params (utm_*, fbclid, gclid, mc_cid, etc.)
    - arXiv: abs/pdf/html all normalize to https://arxiv.org/abs/<id>
    - GitHub/GitLab: strip .git, lowercase entire path
    - HuggingFace: normalize /papers/* vs /paper/*
    - YouTube: extract video ID, normalize to youtube.com/watch?v=ID
    """
    if not url or not isinstance(url, str):
        return ""

    url = url.strip()
    if not url:
        return ""

    try:
        parsed = urlparse(url)
    except Exception:
        return url

    scheme = (parsed.scheme or "").lower()
    if scheme and scheme not in ("http", "https"):
        return url
    if not scheme and not parsed.netloc:
        return url  # not a URL (e.g., bare text)
    scheme = "https"  # always normalize to https

    netloc = parsed.netloc.lower()
    # Strip www. prefix
    if netloc.startswith("www."):
        netloc = netloc[4:]

    path = parsed.path
    query = parsed.query

    # ── arXiv normalization ──
    # arxiv.org/abs/2301.12345, /pdf/2301.12345.pdf, /html/2301.12345v2
    # all → https://arxiv.org/abs/2301.12345
    if "arxiv.org" in netloc:
        m = ARXIV_ID_PATTERN.search(netloc + path)
        if m:
            return f"https://arxiv.org/abs/{m.group(1)}"

    # ── YouTube normalization ──
    # Extract video ID, normalize to youtube.com/watch?v=ID
    if "youtube.com" in netloc or "youtu.be" in netloc:
        full = netloc + path + ("?" + query if query else "")
        for pattern in YOUTUBE_PATTERNS:
            m = pattern.search(full)
            if m:
                return f"https://youtube.com/watch?v={m.group(1)}"

    # ── GitHub / GitLab normalization ──
    # Case-insensitive paths, strip .git suffix
    if "github.com" in netloc or "gitlab.com" in netloc:
        path = path.lower()
        path = re.sub(r"\.git$", "", path)

    # ── HuggingFace normalization ──
    # /paper/* → /papers/* (canonical)
    if "huggingface.co" in netloc:
        path = re.sub(r"^/paper/", "/papers/", path)

    # Strip trailing slash from path
    if path.endswith("/") and len(path) > 1:
        path = path.rstrip("/")

    # Strip tracking params from query
    if query:
        params = parse_qs(query, keep_blank_values=False)
        clean_params = {
            k: v for k, v in params.items()
            if k.lower() not in TRACKING_PARAMS
        }
        query = urlencode(clean_params, doseq=True) if clean_params else ""

    # Rebuild URL without fragment
    normalized = urlunparse((scheme, netloc, path, "", query, ""))

    # Strip trailing slash on the final URL
    if normalized.endswith("/"):
        normalized = normalized.rstrip("/")

    return normalized


def dedup_urls(sources_data, news_data):
    """Cross-stream deduplication using normalize_url_deep.

    Checks all sources and news items for URL duplicates:
    1. Within sources (keep earliest by added_at)
    2. Within news (keep earliest by extracted_at)
    3. Cross-stream: if same URL in both sources and news, mark news item as duplicate

    Args:
        sources_data: dict loaded from sources.json
        news_data: dict loaded from news-queue.json

    Returns:
        {
            "sources_removed": [{"id": ..., "duplicate_of": ..., "reason": ...}],
            "news_removed": [{"id": ..., "duplicate_of": ..., "reason": ...}],
        }
    """
    sources_removed = []
    news_removed = []

    # Build normalized URL → first source mapping
    url_to_source = {}
    for src in sources_data.get("sources", []):
        if src.get("status") == "duplicate":
            continue
        url = src.get("url")
        if not url:
            continue
        norm = normalize_url_deep(url)
        if norm in url_to_source:
            # Duplicate within sources — mark the later one
            existing = url_to_source[norm]
            src_id = src.get("id")
            if not src_id:
                continue
            sources_removed.append({
                "id": src_id,
                "duplicate_of": existing.get("id", "unknown"),
                "reason": _dedup_reason(url, existing.get("url", "")),
            })
        else:
            url_to_source[norm] = src

    # Build normalized URL → first news mapping, also cross-check against sources
    url_to_news = {}
    for article in news_data.get("articles", []):
        if article.get("status") == "duplicate":
            continue
        url = article.get("canonical_url") or article.get("url")
        if not url:
            continue
        norm = normalize_url_deep(url)

        article_id = article.get("id")
        if not article_id:
            continue

        # Cross-stream check: if URL already in sources, mark news as duplicate
        if norm in url_to_source:
            existing_src = url_to_source[norm]
            news_removed.append({
                "id": article_id,
                "duplicate_of": existing_src.get("id", "unknown"),
                "reason": "cross-stream",
            })
            continue

        # Within-news dedup
        if norm in url_to_news:
            existing = url_to_news[norm]
            news_removed.append({
                "id": article_id,
                "duplicate_of": existing.get("id", "unknown"),
                "reason": _dedup_reason(url, existing.get("url", "")),
            })
        else:
            url_to_news[norm] = article

    return {
        "sources_removed": sources_removed,
        "news_removed": news_removed,
    }


def _dedup_reason(url1, url2):
    """Generate a human-readable reason for why two URLs are considered duplicates."""
    url1 = url1 or ""
    url2 = url2 or ""
    if "arxiv.org" in url1 or "arxiv.org" in url2:
        return "arXiv normalization"
    if "youtube.com" in url1 or "youtu.be" in url1 or "youtube.com" in url2 or "youtu.be" in url2:
        return "YouTube ID normalization"
    if "github.com" in url1 or "github.com" in url2:
        return "GitHub case normalization"
    if "huggingface.co" in url1 or "huggingface.co" in url2:
        return "HuggingFace path normalization"
    return "URL normalization"


# ── Self-test ──

if __name__ == "__main__":
    print("URL Normalization Tests:")
    print("=" * 60)

    tests = [
        # arXiv equivalence
        ("https://arxiv.org/abs/2301.12345", "https://arxiv.org/abs/2301.12345"),
        ("https://arxiv.org/pdf/2301.12345.pdf", "https://arxiv.org/abs/2301.12345"),
        ("https://arxiv.org/html/2301.12345v2", "https://arxiv.org/abs/2301.12345"),
        ("http://www.arxiv.org/abs/2301.12345", "https://arxiv.org/abs/2301.12345"),
        # GitHub case insensitivity
        ("https://github.com/User/Repo.git", "https://github.com/user/repo"),
        ("https://github.com/user/repo/", "https://github.com/user/repo"),
        ("https://GITHUB.COM/HuggingFace/Transformers", "https://github.com/huggingface/transformers"),
        # YouTube ID extraction
        ("https://youtu.be/dQw4w9WgXcQ", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=abc", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
        ("https://youtube.com/embed/dQw4w9WgXcQ", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
        ("https://youtube.com/shorts/dQw4w9WgXcQ", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
        # Tracking param stripping
        ("https://example.com/page?utm_source=newsletter&utm_medium=email&id=42", "https://example.com/page?id=42"),
        ("https://example.com/page?fbclid=abc123", "https://example.com/page"),
        # HuggingFace
        ("https://huggingface.co/paper/2301.12345", "https://huggingface.co/papers/2301.12345"),
        # www stripping
        ("https://www.example.com/path", "https://example.com/path"),
        # Trailing slash
        ("https://example.com/path/", "https://example.com/path"),
    ]

    passed = 0
    failed = 0
    for url_in, expected in tests:
        result = normalize_url_deep(url_in)
        ok = result == expected
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{status}] {url_in}")
        if not ok:
            print(f"         Expected: {expected}")
            print(f"         Got:      {result}")

    print(f"\n{passed} passed, {failed} failed out of {len(tests)} tests")

    # Equivalence group tests
    print("\nEquivalence Tests:")
    print("-" * 60)
    equiv_groups = [
        [
            "https://arxiv.org/abs/2301.12345",
            "https://arxiv.org/pdf/2301.12345.pdf",
            "https://arxiv.org/html/2301.12345v2",
        ],
        [
            "https://github.com/User/Repo.git",
            "https://github.com/user/repo",
            "https://github.com/USER/REPO/",
        ],
        [
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/embed/dQw4w9WgXcQ",
        ],
    ]
    for group in equiv_groups:
        normalized = [normalize_url_deep(u) for u in group]
        all_same = len(set(normalized)) == 1
        status = "PASS" if all_same else "FAIL"
        print(f"  [{status}] All equivalent: {normalized[0]}")
        if not all_same:
            for u, n in zip(group, normalized):
                print(f"         {u} -> {n}")
