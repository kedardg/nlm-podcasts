#!/usr/bin/env python3
"""
Blog monitor for OpenClaw Research Automation System.

Monitors AI company blogs via RSS feeds and HTML scraping.
Extracts new articles, deduplicates against existing data,
and routes them to sources.json or news-queue.json.

Usage:
    python blog_monitor.py              # Normal run
    python blog_monitor.py --check      # Dry run (preview only)
    python blog_monitor.py --feed feed_001  # Run single feed

Requires:
    - feedparser, beautifulsoup4, requests, trafilatura (installed in .venv)
    - data/feeds.json config file
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse, urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

try:
    import trafilatura
except ImportError:
    trafilatura = None

try:
    from lib.url_utils import normalize_url_deep
except ImportError:
    def normalize_url_deep(url):
        return url

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
FEEDS_FILE = DATA_DIR / "feeds.json"
SOURCES_FILE = DATA_DIR / "sources.json"
NEWS_QUEUE_FILE = DATA_DIR / "news-queue.json"
CATEGORIES_FILE = DATA_DIR / "categories.json"

# Shared utilities
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from lib.file_utils import load_json, load_news_queue, save_json, now_iso
from newsletter_parser import (
    next_source_id,
    next_news_id,
    is_duplicate_source,
    is_duplicate_news,
    classify_url,
    _categorize_by_url,
    _categorize_by_keywords,
)


# ── Feed Loading ─────────────────────────────────────────────


def load_feeds(feed_id=None):
    """Load feed configurations from data/feeds.json.

    Args:
        feed_id: If provided, return only this feed.

    Returns:
        List of active feed dicts.
    """
    data = load_json(FEEDS_FILE)
    feeds = data.get("feeds", [])
    config = data.get("config", {})

    if feed_id:
        feeds = [f for f in feeds if f["id"] == feed_id]
        if not feeds:
            print(f"ERROR: Feed '{feed_id}' not found in {FEEDS_FILE}")
            sys.exit(1)
    else:
        feeds = [f for f in feeds if f.get("status") == "active"]

    return feeds, config, data


# ── RSS Fetching ─────────────────────────────────────────────


def fetch_rss(feed, config):
    """Fetch items from an RSS feed.

    Returns:
        List of dicts with keys: url, title, published
    """
    rss_url = feed.get("rss_url")
    if not rss_url:
        return []

    ua = config.get("user_agent", "OpenClaw-Research/1.0")
    max_items = config.get("max_per_feed", 20)

    try:
        parsed = feedparser.parse(
            rss_url,
            agent=ua,
        )
    except Exception as e:
        print(f"  RSS parse error: {e}")
        return []

    if parsed.bozo and not parsed.entries:
        print(f"  RSS feed error: {parsed.bozo_exception}")
        return []

    items = []
    for entry in parsed.entries[:max_items]:
        url = entry.get("link", "").strip()
        if not url:
            continue

        title = entry.get("title", "").strip()
        published = None
        if entry.get("published_parsed"):
            try:
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pass

        # For shared RSS feeds (e.g. OpenAI has one rss.xml for all),
        # filter by the feed's page URL path to get the right section
        feed_url = feed.get("url", "")
        feed_path = urlparse(feed_url).path.rstrip("/")
        entry_path = urlparse(url).path

        # If the feed page has a specific section path, only include matching entries
        if feed_path and len(feed_path) > 1:
            # e.g. feed_path="/news/research", entry must start with /news/research/
            if not entry_path.startswith(feed_path + "/") and entry_path != feed_path:
                # Looser check: at least the last segment should match
                feed_section = feed_path.split("/")[-1]
                if feed_section and feed_section not in entry_path:
                    continue

        items.append({
            "url": url,
            "title": title or "Untitled",
            "published": published,
        })

    return items


# ── HTML Scraping ────────────────────────────────────────────


def fetch_scrape(feed, config):
    """Scrape article links from an HTML page.

    Returns:
        List of dicts with keys: url, title, published (None for scraped)
    """
    page_url = feed.get("url")
    if not page_url:
        return []

    ua = config.get("user_agent", "OpenClaw-Research/1.0")
    selector = feed.get("scrape_selector", "a")
    max_items = config.get("max_per_feed", 20)

    try:
        resp = requests.get(
            page_url,
            headers={"User-Agent": ua},
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"  Scrape fetch error: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    seen_urls = set()

    for a_tag in soup.select(selector):
        href = a_tag.get("href", "").strip()
        if not href:
            continue

        # Resolve relative URLs
        url = urljoin(page_url, href)

        # Skip non-HTTP links
        if not url.startswith("http"):
            continue

        # Skip anchor-only / same-page links
        parsed = urlparse(url)
        page_parsed = urlparse(page_url)
        if parsed.netloc == page_parsed.netloc and parsed.path == page_parsed.path:
            continue

        # Skip external links (we want links within the same domain)
        if parsed.netloc != page_parsed.netloc:
            # Allow www. variants
            if parsed.netloc.replace("www.", "") != page_parsed.netloc.replace("www.", ""):
                continue

        # Skip obvious non-article paths
        skip_paths = ["/careers", "/jobs", "/about", "/contact", "/legal",
                      "/privacy", "/terms", "/login", "/signup"]
        if any(parsed.path.lower().startswith(p) for p in skip_paths):
            continue

        # Skip feed/syndication URLs
        if parsed.path.rstrip("/").endswith(("/rss", "/feed", "/atom")):
            continue

        # Skip category/team/tag index pages (e.g. /research/team/alignment)
        skip_segments = ["/team/", "/tag/", "/category/", "/author/",
                         "/page/", "/archive/"]
        if any(seg in parsed.path.lower() for seg in skip_segments):
            continue

        # Require a minimum path depth (at least /section/slug) to avoid index pages
        path_parts = [p for p in parsed.path.split("/") if p]
        if len(path_parts) < 2:
            continue

        # Normalize for dedup within this scrape
        norm = normalize_url_deep(url)
        if norm in seen_urls:
            continue
        seen_urls.add(norm)

        # Extract title from anchor text
        raw_title = a_tag.get_text(strip=True)

        # Skip links with very short or generic navigation titles
        generic_titles = {"rss", "ai", "blog", "home", "more", "next", "prev",
                          "previous", "breadcrumb", "menu", "search", "share",
                          "feed", "all", "back", "close", "open"}
        if raw_title and raw_title.strip().lower() in generic_titles:
            continue

        title = raw_title
        if not title or len(title) < 5:
            # Try to get title from a nearby heading or parent
            parent = a_tag.parent
            if parent:
                heading = parent.find(["h1", "h2", "h3", "h4", "span"])
                if heading:
                    candidate = heading.get_text(strip=True)
                    # Don't use generic heading text as title
                    if candidate and candidate.strip().lower() not in generic_titles:
                        title = candidate

        if not title or len(title) < 5:
            title = None  # Will be resolved later via resolve_title()

        items.append({
            "url": url,
            "title": title,
            "published": None,
        })

        if len(items) >= max_items:
            break

    return items


def resolve_title(url):
    """Fetch a page title using trafilatura or a simple <title> scrape."""
    if trafilatura:
        try:
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                metadata = trafilatura.extract_metadata(downloaded)
                if metadata and metadata.title:
                    return metadata.title[:120]
        except Exception:
            pass

    # Fallback: simple title tag extraction
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "OpenClaw-Research/1.0"},
            timeout=15,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
            # Strip common suffixes
            for sep in [" | ", " - ", " — ", " :: "]:
                if sep in title:
                    title = title.split(sep)[0].strip()
            return title[:120]
    except Exception:
        pass

    return None


# ── Routing ──────────────────────────────────────────────────


def route_item(url, title, feed, sources, news_queue, categories, dry_run=False):
    """Classify and add an item to sources.json or news-queue.json.

    Returns:
        Tuple of (routed: bool, destination: str)
    """
    norm_url = normalize_url_deep(url)
    if not norm_url:
        return False, "skip"

    # Cross-stream dedup
    if is_duplicate_source(norm_url, sources):
        return False, "duplicate"
    if is_duplicate_news(norm_url, news_queue):
        return False, "duplicate"

    # Determine routing
    route_to = feed.get("route_to", "sources")
    feed_category = feed.get("category", "ml")

    # Use classify_url to determine content type, but respect feed config for routing
    classification = classify_url(url)

    # Determine fine-grained category
    cat = _categorize_by_url(url, categories)
    if not cat and title:
        cat = _categorize_by_keywords(title, categories)
    if not cat:
        cat = feed_category

    if dry_run:
        return True, route_to

    if route_to == "sources":
        sid = next_source_id(sources)
        src_type = "url"
        if classification == "youtube":
            src_type = "youtube"

        sources["sources"].append({
            "id": sid,
            "type": src_type,
            "origin": "blog",
            "url": norm_url,
            "file_path": None,
            "title": (title or "Untitled")[:120],
            "category": cat,
            "tags": [],
            "added_at": now_iso(),
            "status": "queued",
            "notebook_id": None,
            "from_account": None,
            "from_newsletter": None,
            "from_feed": feed["id"],
        })
        return True, "sources"

    else:  # route_to == "news"
        nid = next_news_id(news_queue)
        content_type = classification if classification in ("news", "blog") else "blog"

        news_queue["articles"].append({
            "id": nid,
            "url": norm_url,
            "canonical_url": norm_url,
            "title": (title or "Untitled")[:120],
            "source_newsletter": None,
            "source_newsletter_name": None,
            "source_feed": feed["id"],
            "source_feed_name": feed["name"],
            "extracted_at": now_iso(),
            "category": cat,
            "content_type": content_type,
            "content_snippet": None,
            "status": "queued",
            "digest_id": None,
        })
        return True, "news"


# ── Main Pipeline ────────────────────────────────────────────


def process_feeds(feed_id=None, dry_run=False):
    """Main entry point: load feeds, fetch items, dedup, route."""
    feeds, config, feeds_data = load_feeds(feed_id)
    sources = load_json(SOURCES_FILE)
    news_queue = load_news_queue(NEWS_QUEUE_FILE)
    categories = load_json(CATEGORIES_FILE)

    if not feeds:
        print("No active feeds to process.")
        return

    total_stats = {"fetched": 0, "routed": 0, "duplicate": 0, "skipped": 0}
    feed_results = []

    for feed in feeds:
        print(f"\n[{feed['id']}] {feed['name']} ({feed['type']})")
        print(f"  URL: {feed['url']}")

        # Fetch items based on feed type
        if feed["type"] == "rss":
            items = fetch_rss(feed, config)
        elif feed["type"] == "scrape":
            items = fetch_scrape(feed, config)
        else:
            print(f"  Unknown feed type: {feed['type']}")
            continue

        print(f"  Found {len(items)} item(s)")
        total_stats["fetched"] += len(items)

        routed_count = 0
        dup_count = 0
        skip_count = 0

        for item in items:
            url = item["url"]
            title = item.get("title")

            # Resolve title if missing (for scraped items)
            if not title and not dry_run:
                title = resolve_title(url)
            if not title:
                # Extract from URL path as fallback
                path_parts = [p for p in urlparse(url).path.split("/") if p]
                if path_parts:
                    title = path_parts[-1].replace("-", " ").replace("_", " ").title()[:80]
                else:
                    title = "Untitled"

            routed, dest = route_item(
                url, title, feed, sources, news_queue, categories, dry_run
            )

            if dry_run:
                status = "NEW" if routed else dest.upper()
                print(f"    [{status:9s}] {(title or 'Untitled')[:50]:50s} → {url[:60]}")
                if routed:
                    routed_count += 1
                elif dest == "duplicate":
                    dup_count += 1
                else:
                    skip_count += 1
            else:
                if routed:
                    routed_count += 1
                elif dest == "duplicate":
                    dup_count += 1
                else:
                    skip_count += 1

        total_stats["routed"] += routed_count
        total_stats["duplicate"] += dup_count
        total_stats["skipped"] += skip_count

        feed_results.append({
            "id": feed["id"],
            "name": feed["name"],
            "fetched": len(items),
            "routed": routed_count,
            "duplicate": dup_count,
        })

        print(f"  Routed: {routed_count}, Duplicate: {dup_count}, Skipped: {skip_count}")

        # Update feed metadata
        if not dry_run:
            for f in feeds_data.get("feeds", []):
                if f["id"] == feed["id"]:
                    f["last_checked"] = now_iso()
                    f["total_extracted"] = f.get("total_extracted", 0) + routed_count

    # Save data
    if not dry_run:
        save_json(SOURCES_FILE, sources)
        save_json(NEWS_QUEUE_FILE, news_queue)
        save_json(FEEDS_FILE, feeds_data)

    # Summary
    print(f"\n{'=' * 50}")
    print(f"Blog Monitor Summary")
    print(f"  Feeds processed: {len(feeds)}")
    print(f"  Items fetched:   {total_stats['fetched']}")
    print(f"  Items routed:    {total_stats['routed']}")
    print(f"  Duplicates:      {total_stats['duplicate']}")
    print(f"  Skipped:         {total_stats['skipped']}")

    return total_stats


def main():
    parser = argparse.ArgumentParser(description="Blog monitor for OpenClaw Research")
    parser.add_argument("--check", action="store_true", help="Dry-run: preview without saving")
    parser.add_argument("--feed", type=str, help="Run a single feed by ID (e.g., feed_001)")
    args = parser.parse_args()

    process_feeds(
        feed_id=args.feed,
        dry_run=args.check,
    )


if __name__ == "__main__":
    main()
