#!/usr/bin/env python3
"""
Deterministic command router for OpenClaw Research Automation System.

Handles all fixed commands without LLM involvement.
Returns structured JSON so the caller knows if the command was handled
or needs to be forwarded to the LLM.

Usage:
    python router.py "<message>"
    python router.py "track @omarsar0"
    python router.py "list sources"
    python router.py "https://arxiv.org/abs/2301.12345"

Output (JSON):
    {"handled": true, "response": "...", "action": "track"}
    {"handled": false, "route": "llm", "reason": "chat_query"}
"""

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from lib.file_utils import load_json, save_json, now_iso

try:
    from lib.url_utils import normalize_url_deep
except ImportError:
    def normalize_url_deep(url):
        return url  # fallback: no normalization

PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
SOURCES_FILE = DATA_DIR / "sources.json"
ACCOUNTS_FILE = DATA_DIR / "accounts.json"
CATEGORIES_FILE = DATA_DIR / "categories.json"
NOTEBOOKS_FILE = DATA_DIR / "notebooks.json"
PROCESSING_LOG_FILE = DATA_DIR / "processing-log.json"
NEWSLETTERS_FILE = DATA_DIR / "newsletters.json"
NEWS_QUEUE_FILE = DATA_DIR / "news-queue.json"




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


def categorize_by_url(url, categories):
    """Categorize a URL using URL pattern matching. Returns slug or None."""
    if not url:
        return None
    for cat in categories.get("categories", []):
        for pattern in cat.get("url_patterns", []):
            if pattern in url:
                return cat["slug"]
    return None


def categorize_by_keywords(text, categories):
    """Categorize text using keyword matching. Returns slug or None."""
    if not text:
        return None
    text_lower = text.lower()
    best_slug = None
    best_count = 0
    for cat in categories.get("categories", []):
        if cat["slug"] == "general":
            continue
        count = sum(1 for kw in cat.get("keywords", []) if kw.lower() in text_lower)
        if count > best_count:
            best_count = count
            best_slug = cat["slug"]
    return best_slug


def detect_url_type(url):
    """Determine source type from URL."""
    if "arxiv.org" in url:
        return "url"
    if "github.com" in url:
        return "url"
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    if "twitter.com" in url or "x.com" in url:
        return "x_post"
    return "url"


def extract_x_username(text):
    """Extract X/Twitter username from various formats."""
    text = text.strip()
    # URL format: https://x.com/username or https://twitter.com/username
    m = re.match(r"https?://(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]+)(?:/|$)", text)
    if m:
        return f"@{m.group(1)}"
    # @username format
    m = re.match(r"@([A-Za-z0-9_]+)", text)
    if m:
        return f"@{m.group(1)}"
    # bare username
    m = re.match(r"([A-Za-z0-9_]+)$", text)
    if m:
        return f"@{m.group(1)}"
    return None


# ── Command Handlers ───────────────────────────────────────────────


def cmd_manual(_args):
    return {
        "handled": True,
        "action": "manual",
        "response": (
            "\U0001f4cb Research Bot Commands\n"
            "\n"
            "\U0001f4e5 Adding Sources:\n"
            "\u2022 Send a PDF, URL, or link \u2014 auto-queued & categorized\n"
            "\u2022 /note <text> \u2014 add a text note to the queue\n"
            "\n"
            "\U0001f426 X/Twitter:\n"
            "\u2022 /track @username \u2014 start tracking\n"
            "\u2022 /track off @username \u2014 pause tracking\n"
            "\n"
            "\U0001f4c2 Browse:\n"
            "\u2022 /list accounts | sources | categories | newsletters\n"
            "\u2022 /addcategory <name> keywords: k1, k2\n"
            "\n"
            "\U0001f4e8 Newsletters:\n"
            "\u2022 /subscribe <name> from: sender@email\n"
            "\u2022 /subscribe off <name> \u2014 stop tracking\n"
            "\n"
            "\u2699\ufe0f Processing:\n"
            "\u2022 /generate podcast | report | report+podcast | news | daily\n"
            "\u2022 /process \u2014 run full pipeline now\n"
            "\n"
            "\U0001f4ac Research Q&A:\n"
            "\u2022 Just type a question \u2014 searches all your research\n"
            "\u2022 /chat ml: <question> \u2014 search specific category\n"
            "\n"
            "\U0001f4ca System:\n"
            "\u2022 /status \u2014 overview (queue, accounts, schedules)\n"
            "\u2022 /status triage \u2014 dedup & notebook grouping plan\n"
            "\u2022 /status news \u2014 news queue counts\n"
            "\u2022 /status daily \u2014 today's feed status\n"
            "\u2022 /reset \u2014 clear chat context\n"
            "\u2022 /model \u2014 switch LLM\n"
            "\u2022 /manual \u2014 show this help\n"
            "\n"
            "Done."
        ),
    }


def cmd_track(args):
    if not args:
        return {"handled": True, "action": "track", "response": "Usage: /track @username \u2014 start tracking\n       /track off @username \u2014 pause tracking\n\nDone."}

    # Handle "track off @user" → untrack
    if re.match(r"off\s+", args, re.IGNORECASE):
        return cmd_untrack(re.sub(r"^off\s+", "", args, flags=re.IGNORECASE))

    username = extract_x_username(args)
    if not username:
        return {"handled": True, "action": "track", "response": f"Could not parse username from: {args}\n\nFailed: invalid username format"}

    accounts = load_json(ACCOUNTS_FILE)
    for acc in accounts.get("accounts", []):
        if acc.get("username", "").lower() == username.lower():
            if acc.get("status") == "paused":
                acc["status"] = "active"
                save_json(ACCOUNTS_FILE, accounts)
                return {"handled": True, "action": "track", "response": f"Reactivated tracking for {username}.\n\nDone."}
            else:
                return {"handled": True, "action": "track", "response": f"{username} is already being tracked.\n\nDone."}

    accounts["accounts"].append({
        "username": username,
        "added_at": now_iso(),
        "last_scraped": None,
        "scrape_for": ["papers", "blogposts", "github_repos"],
        "status": "active",
    })
    save_json(ACCOUNTS_FILE, accounts)
    return {"handled": True, "action": "track", "response": f"Now tracking {username} for papers, blogposts, and GitHub repos.\n\nDone."}


def cmd_untrack(args):
    if not args:
        return {"handled": True, "action": "untrack", "response": "Usage: untrack @username\n\nDone."}

    username = extract_x_username(args)
    if not username:
        return {"handled": True, "action": "untrack", "response": f"Could not parse username from: {args}\n\nFailed: invalid username format"}

    accounts = load_json(ACCOUNTS_FILE)
    for acc in accounts.get("accounts", []):
        if acc.get("username", "").lower() == username.lower():
            acc["status"] = "paused"
            save_json(ACCOUNTS_FILE, accounts)
            return {"handled": True, "action": "untrack", "response": f"Paused tracking for {username}.\n\nDone."}

    return {"handled": True, "action": "untrack", "response": f"{username} not found in tracked accounts.\n\nFailed: account not found"}


def cmd_list(args):
    args = (args or "").strip().lower()

    if args == "accounts":
        return cmd_list_accounts()
    elif args == "sources":
        return cmd_list_sources()
    elif args == "categories":
        return cmd_list_categories()
    elif args == "newsletters":
        return cmd_list_newsletters()
    else:
        return {"handled": True, "action": "list", "response": "Usage: list accounts | list sources | list categories | list newsletters\n\nDone."}


def cmd_list_accounts():
    accounts = load_json(ACCOUNTS_FILE)
    accs = accounts.get("accounts", [])
    if not accs:
        return {"handled": True, "action": "list_accounts", "response": "No tracked X/Twitter accounts.\n\nDone."}

    lines = ["Tracked X/Twitter Accounts:"]
    for a in accs:
        scraped = a.get("last_scraped", "never") or "never"
        if scraped != "never":
            scraped = scraped[:10]
        lines.append(f"\u2022 {a.get('username', '???')} \u2014 {a.get('status', 'unknown')} \u2014 last scraped: {scraped}")
    lines.append("\nDone.")
    return {"handled": True, "action": "list_accounts", "response": "\n".join(lines)}


def cmd_list_sources():
    sources = load_json(SOURCES_FILE)
    srcs = sources.get("sources", [])
    if not srcs:
        return {"handled": True, "action": "list_sources", "response": "No sources in queue.\n\nDone."}

    by_status = {}
    by_cat = {}
    for s in srcs:
        st = s.get("status", "unknown")
        cat = s.get("category", "unknown")
        by_status[st] = by_status.get(st, 0) + 1
        if st == "queued":
            by_cat[cat] = by_cat.get(cat, 0) + 1

    lines = ["Research Sources:"]
    queued = by_status.get("queued", 0)
    if queued > 0:
        cat_detail = ", ".join(f"{k}: {v}" for k, v in sorted(by_cat.items()))
        lines.append(f"\u2022 Queued: {queued} ({cat_detail})")
    else:
        lines.append("\u2022 Queued: 0")
    lines.append(f"\u2022 Processing: {by_status.get('processing', 0)}")
    lines.append(f"\u2022 Added to notebooks: {by_status.get('added_to_notebook', 0)}")
    lines.append(f"\u2022 Errors: {by_status.get('error', 0)}")
    lines.append(f"\u2022 Total: {len(srcs)}")
    lines.append("\nDone.")
    return {"handled": True, "action": "list_sources", "response": "\n".join(lines)}


def cmd_list_categories():
    categories = load_json(CATEGORIES_FILE)
    cats = categories.get("categories", [])
    if not cats:
        return {"handled": True, "action": "list_categories", "response": "No categories defined.\n\nDone."}

    lines = ["Categories:"]
    for c in cats:
        kw = ", ".join(c.get("keywords", [])[:5])
        pats = ", ".join(c.get("url_patterns", []))
        desc_parts = []
        if kw:
            desc_parts.append(f"keywords: {kw}")
        if pats:
            desc_parts.append(f"urls: {pats}")
        if not desc_parts and c.get("slug") == "general":
            desc_parts.append("catch-all")
        desc = " \u2014 ".join(desc_parts) if desc_parts else ""
        lines.append(f"\u2022 {c.get('name', '???')} ({c.get('slug', '???')}) \u2014 {desc}")
    lines.append("\nDone.")
    return {"handled": True, "action": "list_categories", "response": "\n".join(lines)}


def cmd_status(args):
    """Unified status command with subcommands: triage, news, daily."""
    sub = (args or "").strip().lower()

    if sub == "triage":
        return cmd_triage(None)
    if sub == "news":
        return cmd_news_status(None)
    if sub == "daily":
        return cmd_daily_status(None)

    # Default: system overview
    sources = load_json(SOURCES_FILE)
    accounts = load_json(ACCOUNTS_FILE)
    notebooks = load_json(NOTEBOOKS_FILE)
    log = load_json(PROCESSING_LOG_FILE)

    srcs = sources.get("sources", [])
    accs = accounts.get("accounts", [])
    nbs = notebooks.get("notebooks", [])
    runs = log.get("runs", [])

    queued = sum(1 for s in srcs if s.get("status") == "queued")
    active_accs = sum(1 for a in accs if a.get("status") == "active")
    paused_accs = sum(1 for a in accs if a.get("status") == "paused")
    last_run_val = runs[-1].get("completed_at") if runs else None
    last_run = (last_run_val or "never")[:16] if last_run_val else "never"
    total_processed = sum(1 for s in srcs if s.get("status") == "added_to_notebook")

    lines = [
        "System Status:",
        f"\u2022 Queued sources: {queued}",
        f"\u2022 Tracked accounts: {len(accs)} ({active_accs} active, {paused_accs} paused)",
        f"\u2022 Last processing run: {last_run}",
        f"\u2022 Daily feed: 6:00 PM ET daily",
        f"\u2022 Weekly digest: Saturday 9:00 AM ET",
        f"\u2022 Notebooks created: {len(nbs)}",
        f"\u2022 Total sources processed: {total_processed}",
        "",
        "Subcommands: /status triage | news | daily",
        "\nDone.",
    ]
    return {"handled": True, "action": "status", "response": "\n".join(lines)}


def cmd_addcategory(args):
    if not args or "keywords:" not in args.lower():
        return {"handled": True, "action": "addcategory",
                "response": "Usage: addcategory <name> keywords: k1, k2, k3\n\nFailed: missing name or keywords"}

    parts = re.split(r"keywords:\s*", args, flags=re.IGNORECASE)
    name = parts[0].strip()
    keywords = [k.strip() for k in parts[1].split(",") if k.strip()] if len(parts) > 1 else []

    if not name:
        return {"handled": True, "action": "addcategory", "response": "Failed: category name is required"}

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        return {"handled": True, "action": "addcategory", "response": "Failed: category name produces an empty slug"}

    categories = load_json(CATEGORIES_FILE)
    cats = categories.get("categories", [])

    # Check for duplicate
    for c in cats:
        if c["slug"] == slug:
            return {"handled": True, "action": "addcategory",
                    "response": f"Category '{name}' ({slug}) already exists.\n\nDone."}

    new_cat = {"name": name, "slug": slug, "keywords": keywords, "url_patterns": []}

    # Insert before "general" (last)
    general_idx = next((i for i, c in enumerate(cats) if c["slug"] == "general"), len(cats))
    cats.insert(general_idx, new_cat)
    categories["categories"] = cats
    save_json(CATEGORIES_FILE, categories)

    return {"handled": True, "action": "addcategory",
            "response": f"Added category '{name}' ({slug}) with keywords: {', '.join(keywords)}\n\nDone."}


def cmd_putdata(_args):
    return {"handled": True, "action": "putdata",
            "response": "Ready to receive research data. Send me PDFs, URLs, or text.\nUse `note <text>` to add a text note.\n\nDone."}


def cmd_note(args):
    if not args:
        return {"handled": True, "action": "note", "response": "Usage: note <text>\n\nFailed: no text provided"}

    sources = load_json(SOURCES_FILE)
    sid = next_source_id(sources)
    title = args[:60] + ("..." if len(args) > 60 else "")

    categories = load_json(CATEGORIES_FILE)
    cat = categorize_by_keywords(args, categories) or "general"

    sources["sources"].append({
        "id": sid,
        "type": "text",
        "origin": "telegram",
        "url": None,
        "file_path": None,
        "title": title,
        "category": cat,
        "tags": [],
        "added_at": now_iso(),
        "status": "queued",
        "notebook_id": None,
        "from_account": None,
    })
    save_json(SOURCES_FILE, sources)
    return {"handled": True, "action": "note",
            "response": f"\u2705 Added text note to queue \u2192 {cat} category ({sid})\n\nDone."}


def cmd_ingest_url(url):
    """Handle a URL submission (not a command — just a raw URL)."""
    if not url:
        return {"handled": True, "action": "ingest_url", "response": "No URL provided.\n\nFailed: empty URL"}

    sources = load_json(SOURCES_FILE)
    norm_url = normalize_url_deep(url)

    # Dedup against sources (using normalized comparison)
    for s in sources.get("sources", []):
        existing_url = s.get("url")
        if existing_url and normalize_url_deep(existing_url) == norm_url:
            return {"handled": True, "action": "ingest_url",
                    "response": f"URL already in queue: {s['id']} ({s['status']})\n\nDone."}

    # Cross-stream dedup: also check news-queue.json
    news_queue = load_json(NEWS_QUEUE_FILE)
    for a in news_queue.get("articles", []):
        for field in ("canonical_url", "url"):
            existing_url = a.get(field)
            if existing_url and normalize_url_deep(existing_url) == norm_url:
                return {"handled": True, "action": "ingest_url",
                        "response": f"URL already in news queue: {a['id']} ({a.get('status', 'unknown')})\n\nDone."}

    categories = load_json(CATEGORIES_FILE)
    sid = next_source_id(sources)
    src_type = detect_url_type(url)
    cat = categorize_by_url(url, categories) or "general"

    # Extract a title hint from the URL
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]
    title = path_parts[-1] if path_parts else parsed.netloc

    sources["sources"].append({
        "id": sid,
        "type": src_type,
        "origin": "telegram",
        "url": url,
        "file_path": None,
        "title": title,
        "category": cat,
        "tags": [],
        "added_at": now_iso(),
        "status": "queued",
        "notebook_id": None,
        "from_account": None,
    })
    save_json(SOURCES_FILE, sources)
    return {"handled": True, "action": "ingest_url",
            "response": f"\u2705 Added \"{title}\" to queue \u2192 {cat} category ({sid})\n\nDone."}


def cmd_reset(_args):
    return {"handled": True, "action": "reset",
            "response": "Context cleared. Starting fresh \u2014 send me a command or question.\n\nDone."}


def cmd_subscribe(args):
    if not args:
        return {"handled": True, "action": "subscribe",
                "response": "Usage: /subscribe <name> from: sender@email\n       /subscribe off <name> \u2014 stop tracking\n\nFailed: missing name or sender"}

    # Handle "subscribe off <name>" → unsubscribe
    if re.match(r"off\s+", args, re.IGNORECASE):
        return cmd_unsubscribe(re.sub(r"^off\s+", "", args, flags=re.IGNORECASE))

    # Parse: "TLDR AI from: dan@tldrnewsletter.com"
    parts = re.split(r"\s+from:\s*", args, flags=re.IGNORECASE)
    name = parts[0].strip()
    sender = parts[1].strip() if len(parts) > 1 else ""

    if not name:
        return {"handled": True, "action": "subscribe", "response": "Failed: newsletter name is required"}
    if not sender:
        return {"handled": True, "action": "subscribe",
                "response": f"Usage: subscribe {name} from: sender@email.com\n\nFailed: sender email required"}

    newsletters = load_json(NEWSLETTERS_FILE)

    # Check for duplicate
    for nl in newsletters.get("newsletters", []):
        if nl["sender_pattern"].lower() == sender.lower():
            return {"handled": True, "action": "subscribe",
                    "response": f"Already tracking newsletters from {sender} ({nl['name']}).\n\nDone."}

    nl_id = f"nl_{len(newsletters.get('newsletters', [])) + 1:03d}"
    newsletters["newsletters"].append({
        "id": nl_id,
        "name": name,
        "sender_pattern": sender,
        "content_type": "mixed",
        "added_at": now_iso(),
        "last_checked": None,
        "total_extracted": 0,
        "status": "active",
    })
    save_json(NEWSLETTERS_FILE, newsletters)
    return {"handled": True, "action": "subscribe",
            "response": f"Now tracking \"{name}\" newsletter (from: {sender}).\n\nDone."}


def cmd_unsubscribe(args):
    if not args:
        return {"handled": True, "action": "unsubscribe",
                "response": "Usage: unsubscribe <newsletter-name>\n\nFailed: missing name"}

    name = args.strip().lower()
    newsletters = load_json(NEWSLETTERS_FILE)

    for nl in newsletters.get("newsletters", []):
        if nl["name"].lower() == name or nl["id"] == name:
            nl["status"] = "paused"
            save_json(NEWSLETTERS_FILE, newsletters)
            return {"handled": True, "action": "unsubscribe",
                    "response": f"Paused newsletter tracking for \"{nl['name']}\".\n\nDone."}

    return {"handled": True, "action": "unsubscribe",
            "response": f"Newsletter \"{args.strip()}\" not found.\n\nFailed: not found"}


def cmd_list_newsletters():
    newsletters = load_json(NEWSLETTERS_FILE)
    nls = newsletters.get("newsletters", [])
    if not nls:
        return {"handled": True, "action": "list_newsletters",
                "response": "No tracked newsletters.\n\nDone."}

    lines = ["Tracked Newsletters:"]
    for nl in nls:
        checked = nl.get("last_checked", "never") or "never"
        if checked != "never":
            checked = checked[:10]
        lines.append(f"\u2022 {nl.get('name', '???')} \u2014 {nl.get('status', 'unknown')} \u2014 from: {nl.get('sender_pattern', '???')} \u2014 extracted: {nl.get('total_extracted', 0)} \u2014 last: {checked}")
    lines.append("\nDone.")
    return {"handled": True, "action": "list_newsletters", "response": "\n".join(lines)}


def cmd_news_status(_args):
    news_queue = load_json(NEWS_QUEUE_FILE)
    articles = news_queue.get("articles", [])
    queued = sum(1 for a in articles if a.get("status") == "queued")
    digested = sum(1 for a in articles if a.get("status") == "digested")
    daily_digested = sum(1 for a in articles if a.get("status") == "daily-digested")
    total = len(articles)

    lines = [
        "News Queue Status:",
        f"\u2022 Queued articles: {queued}",
        f"\u2022 Daily digested: {daily_digested}",
        f"\u2022 Weekly digested: {digested}",
        f"\u2022 Total: {total}",
        "\nDone.",
    ]
    return {"handled": True, "action": "news_status", "response": "\n".join(lines)}


def cmd_daily_status(_args):
    """Show daily feed status — today's items and last generated feed."""
    sources = load_json(SOURCES_FILE)
    news_queue = load_json(NEWS_QUEUE_FILE)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Count today's items from sources
    src_today = sum(
        1 for s in sources.get("sources", [])
        if s.get("added_at", "").startswith(today) and s.get("status") in ("queued", "processing")
    )

    # Count today's items from news queue
    news_today = sum(
        1 for a in news_queue.get("articles", [])
        if a.get("extracted_at", "").startswith(today) and a.get("status") == "queued"
    )

    total_today = src_today + news_today

    # Check if today's feed was already generated
    daily_feed_dir = DATA_DIR / "daily-feed"
    pdf_exists = (daily_feed_dir / f"{today}.pdf").exists()
    md_exists = (daily_feed_dir / f"{today}.md").exists()

    lines = [
        "Daily Feed Status:",
        f"\u2022 Today's date: {today}",
        f"\u2022 Items available: {total_today} ({src_today} research, {news_today} news)",
        f"\u2022 PDF generated: {'Yes' if pdf_exists else 'No'}",
        f"\u2022 Markdown generated: {'Yes' if md_exists else 'No'}",
        f"\u2022 Next scheduled: 6:00 PM ET daily",
        "",
        "Use /generate daily to trigger now.",
        "\nDone.",
    ]
    return {"handled": True, "action": "daily_status", "response": "\n".join(lines)}


def cmd_triage(_args):
    """Show triage summary — if fresh plan exists, show it; otherwise generate one."""
    triage_plan_file = DATA_DIR / "triage-plan.json"

    # Check for existing fresh plan (<2h old)
    if triage_plan_file.exists():
        try:
            plan = load_json(triage_plan_file)
            generated_at = plan.get("generated_at", "")
            if generated_at:
                from datetime import datetime as dt
                gen_time = dt.fromisoformat(generated_at.replace("Z", "+00:00"))
                age = datetime.now(timezone.utc) - gen_time
                if age.total_seconds() < 2 * 3600:
                    # Fresh plan — format and return it
                    return _format_triage_response(plan)
        except Exception:
            pass

    # No fresh plan — run data_triage.py --summary
    try:
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "data_triage.py"), "--summary"],
            capture_output=True, text=True, timeout=30,
            cwd=str(PROJECT_ROOT),
        )
        output = result.stdout.strip() if result.stdout else ""
        if result.returncode != 0:
            err = result.stderr.strip() if result.stderr else "unknown error"
            return {"handled": True, "action": "triage",
                    "response": f"Triage failed: {err}\n\nFailed: triage error"}
        return {"handled": True, "action": "triage", "response": output}
    except subprocess.TimeoutExpired:
        return {"handled": True, "action": "triage",
                "response": "Triage timed out.\n\nFailed: timeout"}
    except Exception as e:
        return {"handled": True, "action": "triage",
                "response": f"Triage error: {e}\n\nFailed: error"}


def _format_triage_response(plan):
    """Format a triage plan for Telegram display."""
    s = plan.get("summary", {})
    lines = [
        "Triage Summary:",
        f"\u2022 Sources: {s.get('total_queued_sources', 0)} queued",
        f"\u2022 News: {s.get('total_queued_news', 0)} queued",
        f"\u2022 Duplicates removed: {s.get('duplicates_found', 0)}",
        "",
        f"Notebook Plan ({s.get('total_notebooks_planned', 0)} notebooks):",
    ]

    for nb in plan.get("notebook_groups", []):
        flags = []
        if nb.get("is_merged"):
            flags.append("merged")
        if nb.get("is_split"):
            flags.append("split")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"\u2022 {nb.get('notebook_label', '???')}: {nb.get('source_count', 0)} sources{flag_str}")

    if plan.get("warnings"):
        lines.append("")
        for w in plan["warnings"]:
            lines.append(f"\u26a0\ufe0f {w}")

    lines.append(f"\nGenerated: {plan.get('generated_at', 'unknown')}")
    lines.append("\nDone.")

    return {"handled": True, "action": "triage", "response": "\n".join(lines)}


def cmd_model(args):
    args = (args or "").strip()
    config_path = Path.home() / ".openclaw" / "openclaw.json"

    if not args:
        # Show current model
        current = "unknown"
        if config_path.exists():
            config = load_json(config_path)
            current = config.get("agents", {}).get("defaults", {}).get("model", {}).get("primary", "unknown")
        return {"handled": True, "action": "model", "response": (
            f"Current model: {current}\n\n"
            "Available models:\n"
            "\u2022 gpt-4o-mini \u2014 fast, cheap, high rate limits\n"
            "\u2022 gpt-4o \u2014 more capable, lower rate limits\n"
            "\u2022 gpt-5.1 \u2014 most capable\n\n"
            "Usage: /model gpt-4o-mini\n\nDone."
        )}

    valid_models = ["gpt-4o-mini", "gpt-4o", "gpt-5.1"]
    if args not in valid_models:
        return {"handled": True, "action": "model",
                "response": f"Unknown model: {args}\nAvailable: {', '.join(valid_models)}\n\nFailed: invalid model"}

    if config_path.exists():
        config = load_json(config_path)
        config.setdefault("agents", {}).setdefault("defaults", {}).setdefault("model", {})["primary"] = f"openai/{args}"
        save_json(config_path, config)
        return {"handled": True, "action": "model",
                "response": f"Switched to {args}. Restart gateway to apply: `openclaw gateway stop && openclaw gateway --port 18789 --bind loopback`\n\nDone."}

    return {"handled": True, "action": "model", "response": "Failed: config file not found"}


def cmd_generate(args):
    """Route /generate subcommands — all fetch-only, no creation."""
    sub = (args or "").strip().lower()

    if sub in ("report", "podcast", "report+podcast"):
        artifact_map = {
            "report": "report",
            "podcast": "podcast",
            "report+podcast": "both",
        }
        return {
            "handled": False,
            "route": "llm",
            "reason": "command_generate_fetch",
            "command": "generate",
            "args": sub,
            "artifact": artifact_map[sub],
            "category": "research",
        }

    if sub == "news":
        return {
            "handled": False,
            "route": "llm",
            "reason": "command_generate_fetch",
            "command": "generate",
            "args": sub,
            "artifact": "podcast",
            "category": "news",
        }

    if sub == "daily":
        return {
            "handled": False,
            "route": "llm",
            "reason": "command_generate_fetch",
            "command": "generate",
            "args": sub,
            "artifact": "report",
            "category": "daily",
        }

    # Unknown subcommand — return usage help
    return {
        "handled": True,
        "action": "generate",
        "response": (
            "Usage: /generate <subcommand>\n\n"
            "\u2022 /generate report \u2014 fetch latest research report\n"
            "\u2022 /generate podcast \u2014 fetch latest research podcast\n"
            "\u2022 /generate report+podcast \u2014 fetch both\n"
            "\u2022 /generate news \u2014 fetch latest news podcast\n"
            "\u2022 /generate daily \u2014 fetch latest daily feed\n"
            "\nAll /generate commands deliver existing artifacts.\n"
            "To create new content, use /process or wait for the cron.\n"
            "\nDone."
        ),
    }


# ── Router ─────────────────────────────────────────────────────────


# Commands that need LLM (pass through)
LLM_COMMANDS = {"process", "chat"}


def route(message):
    """Route a message to the appropriate handler.

    Returns JSON: {"handled": bool, "response": str, ...}
    """
    msg = message.strip()
    if not msg:
        return {"handled": False, "route": "llm", "reason": "empty_message"}

    # Strip leading /
    normalized = msg.lstrip("/")

    # Check for URL (raw URL message)
    url_match = re.match(r"(https?://\S+)", msg)

    # Check for X/Twitter URL that might be a track-intent vs source-add
    # If message is JUST a URL (no command), treat as source ingestion
    if url_match and not re.match(r"(?:/?)(track|untrack)\s", normalized, re.IGNORECASE):
        # But if it's an x.com/twitter.com URL with no command, ask if they want to track or add as source
        url = url_match.group(1)
        if ("x.com/" in url or "twitter.com/" in url) and "/" in urlparse(url).path.strip("/"):
            # X/Twitter profile URL — could be track or source, add as x_post source
            return cmd_ingest_url(url)
        return cmd_ingest_url(url)

    # Parse command + args
    parts = normalized.split(None, 1)
    cmd = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""

    # Fixed commands (no LLM needed)
    if cmd == "manual" or cmd == "help" or cmd == "start":
        return cmd_manual(args)
    if cmd == "track":
        return cmd_track(args)
    if cmd == "untrack":
        return cmd_untrack(args)  # backwards compat alias
    if cmd == "list":
        return cmd_list(args)
    if cmd == "status":
        return cmd_status(args)
    if cmd == "addcategory" or (cmd == "add" and args.lower().startswith("category")):
        if cmd == "add":
            args = args[len("category"):].strip()
        return cmd_addcategory(args)
    if cmd == "putdata" or (cmd == "put" and args.lower().startswith("data")):
        return cmd_putdata(args)
    if cmd == "note":
        return cmd_note(args)
    if cmd == "reset":
        return cmd_reset(args)
    if cmd == "model":
        return cmd_model(args)
    if cmd == "subscribe":
        return cmd_subscribe(args)
    if cmd == "unsubscribe":
        return cmd_unsubscribe(args)  # backwards compat alias
    if cmd == "generate":
        return cmd_generate(args)
    if cmd == "news":
        return cmd_news_status(args)  # backwards compat alias → /status news
    if cmd == "daily":
        return cmd_daily_status(args)  # backwards compat alias → /status daily
    if cmd == "triage":
        return cmd_triage(args)  # backwards compat alias → /status triage

    # Commands that need LLM
    if cmd in LLM_COMMANDS:
        return {"handled": False, "route": "llm", "reason": f"command_{cmd}", "command": cmd, "args": args}

    # Default: plain text = Q&A (needs LLM)
    return {"handled": False, "route": "llm", "reason": "chat_query", "query": msg}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"handled": False, "route": "llm", "reason": "no_input"}))
        sys.exit(0)

    message = " ".join(sys.argv[1:])
    result = route(message)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
