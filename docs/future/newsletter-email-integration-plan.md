# Newsletter & Email Integration Plan

## Overview

Extend the OpenClaw Research Automation System to ingest newsletters via email, extract and classify links (research papers, news articles, GitHub repos), and produce two distinct podcast outputs:

1. **Research Podcast** (existing pipeline) — papers and technical content from newsletters feed into the same research ingestion pipeline alongside X/Twitter scrapes
2. **News Digest Podcast** (new pipeline) — news articles, product launches, and industry updates get a separate "AI News Briefing" audio output

---

## Architecture

```
Newsletter Emails (Gmail via himalaya CLI)
  │
  ├── newsletter-monitor skill (Python, cron)
  │   ├── Connect via IMAP (himalaya or imaplib)
  │   ├── Fetch new newsletter emails since last check
  │   ├── Parse HTML with email + BeautifulSoup
  │   ├── Extract & classify links
  │   ├── Filter tracking/junk URLs
  │   │
  │   ├── Research papers (arxiv, openreview, etc.)
  │   │   └── → data/sources.json (type: "url", origin: "newsletter")
  │   │       └── → Existing pipeline: content-processor → notebooklm → research podcast
  │   │
  │   ├── GitHub repos
  │   │   └── → data/sources.json (type: "url", category: "github", origin: "newsletter")
  │   │       └── → Existing pipeline
  │   │
  │   └── News articles + blog posts
  │       └── → data/news-queue.json (new file)
  │           └── → news-digest skill → NotebookLM "brief" → news podcast
  │
  └── Delivered via Telegram (existing digest-deliver skill)
```

---

## Email Service Decision

### Recommended: Gmail + himalaya CLI

| Criteria | Gmail + himalaya | Fastmail + JMAP | Cloudflare Workers |
|----------|-----------------|-----------------|-------------------|
| Cost | Free | $5/mo | Free (needs domain) |
| Setup | Low (App Password) | Medium (JMAP token) | Medium (Worker + DNS) |
| macOS native | Yes (brew install) | Yes (Python) | Serverless |
| Push/real-time | No (polling via cron) | JMAP push | Yes (instant) |
| OpenClaw integration | himalaya skill exists | No skill | No skill |
| Privacy | Low (Google scans) | High | High |

**Why Gmail + himalaya:**
- OpenClaw already has a `himalaya` skill (just needs installation)
- Simplest setup: Gmail App Password + IMAP config
- Matches the existing cron-based pattern (Saturday 9AM alongside x-monitor)
- Free, no new accounts needed if user already has Gmail
- The `gog` CLI is an alternative if push notifications are needed later

**Dedicated newsletter address:** Create a Gmail filter or use Gmail's `+` alias feature (e.g., `yourname+newsletters@gmail.com`) to auto-label all newsletters for easy IMAP filtering.

**Future upgrade path:** If real-time processing becomes important, switch to `gog` CLI (Google Pub/Sub push) or Cloudflare Email Workers.

---

## Newsletter Sources to Subscribe

### Research-Heavy
| Newsletter | Frequency | Key Content |
|-----------|-----------|-------------|
| Ahead of AI (Sebastian Raschka) | Monthly | Deep ML/LLM research dives |
| Alpha Signal | Daily/Weekly | Trending papers, repos, tweets |
| Import AI (Jack Clark) | Weekly | AI research analysis + policy |
| Papers With Code | Weekly | Papers with code implementations |
| The Gradient | Biweekly | Long-form ML research essays |

### News + Industry
| Newsletter | Frequency | Key Content |
|-----------|-----------|-------------|
| The Batch (Andrew Ng) | Weekly | Curated AI news |
| TLDR AI | Daily | 5-min digest: research + tools + launches |
| Ben's Bites | Daily | Product launches, research, news |
| Hugging Face Newsletter | Weekly | Open-source models, community |
| The Neuron | Daily | AI tools, research |

---

## Technical Components

### 1. newsletter-monitor skill

**Purpose:** Fetch newsletter emails, parse HTML, extract links, classify and route them.

**Python dependencies** (add to `.venv`):
```
beautifulsoup4    # HTML parsing + link extraction
lxml              # Fast HTML parser backend
trafilatura       # Article content extraction from URLs
ural              # URL canonicalization + dedup
arxiv             # arXiv paper metadata lookup
```

**Core flow:**
```python
# 1. Connect to IMAP (via himalaya CLI or imaplib directly)
# 2. Search for newsletters since last_checked timestamp
#    IMAP search: 'SINCE "15-Feb-2026" FROM "newsletter@"' or LABEL filter
# 3. For each email:
#    a. Extract HTML body from MIME parts
#    b. Parse with BeautifulSoup
#    c. Extract all <a href="..."> links
#    d. Filter out tracking/junk URLs (unsubscribe, click.*, list-manage, utm-heavy)
#    e. Follow redirect URLs to get canonical destination
#    f. Canonicalize URLs (strip utm params, lowercase, normalize)
#    g. Deduplicate against existing sources.json
#    h. Classify each link:
#       - Research paper → sources.json (existing pipeline)
#       - GitHub repo → sources.json (existing pipeline)
#       - News article → news-queue.json (new pipeline)
#       - Blog post → sources.json or news-queue.json based on content
#       - YouTube → sources.json
#    i. Record newsletter metadata (sender, subject, date, link count)
# 4. Update last_checked timestamp in data/newsletters.json
```

**Link classification rules:**
```python
RESEARCH_DOMAINS = [
    'arxiv.org', 'openreview.net', 'papers.nips.cc',
    'aclanthology.org', 'semanticscholar.org', 'proceedings.mlr.press',
    'jmlr.org', 'biorxiv.org', 'doi.org'
]
GITHUB_PATTERNS = ['github.com', 'gitlab.com', 'huggingface.co/spaces']
NEWS_DOMAINS = [
    'techcrunch.com', 'theverge.com', 'wired.com',
    'arstechnica.com', 'venturebeat.com', 'reuters.com',
    'bloomberg.com', 'cnbc.com', 'bbc.com'
]
BLOG_PATTERNS = ['medium.com', 'substack.com', 'dev.to', 'hashnode.dev', '/blog/']
JUNK_PATTERNS = [
    'unsubscribe', 'click.', 'track.', 'list-manage',
    'mailchimp.com', 'email.mg', 'sendgrid.net',
    'view-in-browser', 'manage-preferences'
]
```

**Tracking URL handling:**
Newsletter services (Substack, Mailchimp, etc.) wrap all links in tracking redirectors. The script must:
1. Detect tracking URLs by domain pattern
2. Follow HTTP redirects (up to 5 hops) to get the canonical URL
3. Strip UTM parameters from the final URL
4. Use the `ural` library for canonicalization

### 2. news-digest skill (NEW)

**Purpose:** Take accumulated news articles from `news-queue.json`, create a structured news briefing, and generate a podcast.

**Flow:**
```
news-queue.json (accumulated news links)
  │
  ├── Fetch article content (trafilatura)
  ├── Rank by recency + relevance
  ├── Group by theme (LLM-assisted)
  ├── Generate structured briefing note:
  │   ├── Top story (2-3 sentences)
  │   ├── Research roundup (2-3 notable papers)
  │   ├── Industry news (launches, funding)
  │   └── Quick hits (3-4 one-liners)
  │
  ├── Create NotebookLM notebook: "AI News - Week of {date}"
  │   ├── Add top article URLs as sources
  │   ├── Add structured briefing as text source
  │   └── Generate podcast: "brief" format (1-2 min) OR "deep_dive" (10-20 min)
  │
  └── Deliver via Telegram (existing digest-deliver)
```

**Podcast formats:**
- **Daily briefing:** Use NotebookLM `brief` format (1-2 min) — quick headline scan
- **Weekly roundup:** Use NotebookLM `deep_dive` format (10-20 min) — detailed discussion with connections between stories

### 3. New data files

**`data/newsletters.json`** — Tracked newsletter subscriptions:
```json
{
  "newsletters": [
    {
      "id": "nl_001",
      "name": "TLDR AI",
      "sender_pattern": "dan@tldrnewsletter.com",
      "label": "newsletters/tldr-ai",
      "content_type": "mixed",
      "added_at": "2026-02-16T10:00:00Z",
      "last_checked": null,
      "total_extracted": 0,
      "status": "active"
    }
  ],
  "config": {
    "imap_account": "gmail",
    "check_label": "Newsletters",
    "last_global_check": null
  }
}
```

**`data/news-queue.json`** — Accumulated news articles awaiting digest generation:
```json
{
  "articles": [
    {
      "id": "news_001",
      "url": "https://techcrunch.com/...",
      "canonical_url": "https://techcrunch.com/...",
      "title": "Article Title",
      "source_newsletter": "nl_001",
      "extracted_at": "2026-02-16T10:00:00Z",
      "category": "industry",
      "content_snippet": "First 200 chars...",
      "status": "queued",
      "digest_id": null
    }
  ]
}
```

### 4. Updated router.py commands

Add new commands to the deterministic router:

| Command | Action |
|---------|--------|
| `subscribe <newsletter-name> from: <sender@email>` | Add newsletter to tracking |
| `unsubscribe <newsletter-name>` | Stop tracking a newsletter |
| `list newsletters` | Show tracked newsletters |
| `news status` | Show news queue count and last digest |
| `generate news` | Trigger news digest + podcast generation |
| `generate news+research` | Trigger both news and research pipelines |

### 5. Updated plugin commands

Register in `research-router` plugin:
- `/subscribe` — Add newsletter tracking
- `/unsubscribe` — Remove newsletter tracking
- `/news` — Show news queue status

### 6. Updated cron job

The Saturday cron expands to include newsletter processing:

```
Saturday 9:00 AM ET — Weekly Research + News Digest:
  1. newsletter-monitor → fetch emails, extract links, classify
  2. x-monitor → scrape tracked X/Twitter accounts
  3. content-processor → categorize research sources
  4. notebooklm-orchestrator → research notebooks + podcasts
  5. news-digest → create news briefing + podcast
  6. digest-deliver → send all artifacts via Telegram
```

Optional: Add a **daily** cron for news-only digests:
```
Monday-Friday 8:00 AM ET — Daily News Brief:
  1. newsletter-monitor → fetch today's newsletters
  2. news-digest → brief format (1-2 min podcast)
  3. digest-deliver → send via Telegram
```

---

## Email Setup Steps

### Gmail Setup (One-Time)

1. **Create or use existing Gmail account** for newsletters
2. **Enable IMAP** in Gmail Settings → Forwarding and POP/IMAP → Enable IMAP
3. **Create App Password** (requires 2FA enabled):
   - Google Account → Security → 2-Step Verification → App passwords
   - Generate password for "Mail" on "Mac"
4. **Create Gmail filter/label:**
   - Settings → Filters → Create filter
   - From: list of newsletter sender addresses (comma-separated)
   - Action: Apply label "Newsletters", Skip Inbox (optional)
5. **Install himalaya:**
   ```bash
   brew install himalaya
   ```
6. **Configure himalaya:**
   ```toml
   # ~/.config/himalaya/config.toml
   [accounts.newsletters]
   email = "yourname@gmail.com"
   display-name = "Research Bot"

   backend.type = "imap"
   backend.host = "imap.gmail.com"
   backend.port = 993
   backend.encryption.type = "tls"
   backend.auth.type = "password"
   backend.auth.cmd = "cat /path/to/app-password.txt"
   # Or use keychain: backend.auth.keyring = "gmail-newsletters"
   ```
7. **Test:**
   ```bash
   himalaya --account newsletters envelope list --folder "Newsletters" --max 5
   ```

### Alternative: Direct IMAP via Python (No himalaya)

If himalaya installation is problematic, the newsletter-monitor skill can use Python's built-in `imaplib` directly:

```python
import imaplib
import email

mail = imaplib.IMAP4_SSL('imap.gmail.com')
mail.login('yourname@gmail.com', 'app-password-here')
mail.select('"Newsletters"')  # Gmail label as IMAP folder

# Search for recent emails
status, messages = mail.search(None, 'SINCE "15-Feb-2026"')
```

This avoids an external dependency but lacks himalaya's nice CLI for manual inspection.

---

## Link Extraction Pipeline (Detailed)

### Step 1: Fetch Email HTML

```python
# Using imaplib (or himalaya --output json)
for msg_id in message_ids:
    status, data = mail.fetch(msg_id, '(RFC822)')
    msg = email.message_from_bytes(data[0][1])

    # Walk MIME parts to find HTML
    html_body = None
    for part in msg.walk():
        if part.get_content_type() == 'text/html':
            html_body = part.get_payload(decode=True).decode()
            break
```

### Step 2: Extract Links

```python
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

soup = BeautifulSoup(html_body, 'lxml')
links = []

for a_tag in soup.find_all('a', href=True):
    url = a_tag['href']
    anchor_text = a_tag.get_text(strip=True)

    # Skip empty anchors (tracking pixels) and junk
    if not anchor_text or len(anchor_text) < 3:
        continue
    if any(junk in url.lower() for junk in JUNK_PATTERNS):
        continue

    links.append({'url': url, 'text': anchor_text})
```

### Step 3: Resolve Tracking Redirects

```python
import requests

def resolve_url(url, max_redirects=5):
    """Follow redirects to get the canonical destination URL."""
    try:
        resp = requests.head(url, allow_redirects=True, timeout=10)
        return resp.url
    except:
        return url
```

### Step 4: Canonicalize & Deduplicate

```python
from ural import normalize_url

def canonicalize(url):
    """Strip tracking params and normalize."""
    normalized = normalize_url(url, strip_trailing_slash=True)
    # Additional: strip common tracking params
    # ural handles most of this
    return normalized
```

### Step 5: Classify

```python
def classify_link(url, anchor_text=""):
    """Classify a URL into: research, github, news, blog, other."""
    domain = urlparse(url).netloc.lower()
    path = urlparse(url).path.lower()

    # Research papers
    if any(d in domain for d in RESEARCH_DOMAINS):
        return "research"

    # GitHub repos
    if any(d in domain for d in GITHUB_PATTERNS):
        # Must be a repo, not just github.com
        if len(path.strip('/').split('/')) >= 2:
            return "github"

    # News
    if any(d in domain for d in NEWS_DOMAINS):
        return "news"

    # Blog posts
    if any(p in domain or p in path for p in BLOG_PATTERNS):
        return "blog"

    # YouTube
    if 'youtube.com' in domain or 'youtu.be' in domain:
        return "youtube"

    # Default: use trafilatura to fetch and check content
    return "unknown"
```

### Step 6: Route to Appropriate Pipeline

```python
def route_link(url, classification, newsletter_id):
    if classification in ("research", "github", "youtube"):
        # → Existing research pipeline (sources.json)
        add_to_sources(url, classification, origin="newsletter", from_newsletter=newsletter_id)
    elif classification in ("news", "blog"):
        # → News pipeline (news-queue.json)
        add_to_news_queue(url, classification, newsletter_id)
    else:
        # Unknown → try to classify by content, default to news
        add_to_news_queue(url, "uncategorized", newsletter_id)
```

---

## News Digest Generation

### Companion Note Structure

The news-digest skill generates a structured companion note for NotebookLM:

```markdown
# AI News Briefing — Week of February 16, 2026

## Top Story
[Title of the most significant development]
[2-3 sentence summary with context and significance]

## Research Highlights
1. **[Paper Title]** (arXiv:XXXX) — [One-line summary]
2. **[Paper Title]** — [One-line summary]
3. **[Paper Title]** — [One-line summary]

## Industry & Product News
- [Company] launched [product] — [significance]
- [Company] raised $[amount] for [purpose]
- [Regulatory development]

## Open Source & Tools
- [Repo name] (★ stars) — [what it does]
- [Tool name] — [what it does]

## Quick Hits
- [One-liner 1]
- [One-liner 2]
- [One-liner 3]
- [One-liner 4]

---
Sources: [count] articles from [count] newsletters
Period: [date range]
```

### NotebookLM Podcast Configuration

| Output | Format | Length | When |
|--------|--------|-------|------|
| Daily news brief | `brief` | 1-2 min | Mon-Fri 8AM (optional) |
| Weekly news roundup | `deep_dive` | 10-20 min | Saturday 9AM |
| Weekly research digest | `deep_dive` | 10-20 min | Saturday 9AM (existing) |

---

## Implementation Order

| # | Task | Depends On | Effort |
|---|------|-----------|--------|
| 1 | Create `data/newsletters.json` and `data/news-queue.json` schemas | — | Small |
| 2 | Install himalaya + Python deps (`beautifulsoup4`, `lxml`, `trafilatura`, `ural`) | — | Small |
| 3 | Set up Gmail App Password + IMAP + filter/label | — | Small (manual) |
| 4 | Write `newsletter-monitor` skill SKILL.md | #1 | Medium |
| 5 | Write `newsletter_parser.py` (link extraction + classification script) | #2 | Medium |
| 6 | Add newsletter commands to `router.py` (subscribe, unsubscribe, list newsletters) | #1 | Small |
| 7 | Register newsletter commands in plugin `index.ts` | #6 | Small |
| 8 | Write `news-digest` skill SKILL.md | #1 | Medium |
| 9 | Update cron job to include newsletter-monitor + news-digest | #4, #8 | Small |
| 10 | Update `digest-deliver` to handle news podcast delivery | #8 | Small |
| 11 | Test end-to-end: subscribe → receive → extract → classify → podcast | All | Medium |
| 12 | (Optional) Add daily news brief cron | #11 | Small |

**Total estimated new code:**
- `newsletter_parser.py` — ~300 lines (link extraction, classification, dedup)
- `newsletter-monitor/SKILL.md` — ~150 lines (skill instructions)
- `news-digest/SKILL.md` — ~100 lines (skill instructions)
- `router.py` additions — ~80 lines (new commands)
- `plugin index.ts` additions — ~20 lines (new registerCommand calls)

---

## Alternative Approaches Considered

### Cloudflare Email Workers (Future Upgrade)
- **Pro:** Instant processing, free, serverless, no polling
- **Con:** Requires a custom domain on Cloudflare, Worker code is JS not Python
- **When:** If real-time processing becomes important or daily cron isn't fast enough

### RSS Conversion (kill-the-newsletter)
- **Pro:** No email account needed, simple RSS polling
- **Con:** Many newsletters block the shared domain; self-hosting adds complexity
- **When:** If email setup proves problematic or for newsletters that offer RSS natively

### Fastmail + JMAP
- **Pro:** Modern API, privacy-respecting, push support
- **Con:** $5/month, smaller ecosystem, no OpenClaw skill
- **When:** If privacy is a top priority or Gmail becomes restrictive

### Podcastfy (Open Source Podcast Gen)
- **Pro:** Fully open source, self-hosted, 100+ LLM support, customizable voices
- **Con:** Requires TTS API keys, more setup than NotebookLM
- **When:** Fallback if NotebookLM's undocumented API breaks

---

## Security & Privacy Notes

- Gmail App Password should be stored securely (macOS Keychain via himalaya's keyring support, or an encrypted file)
- Never commit email credentials to git
- Newsletter content may contain tracking pixels — the parser strips these but doesn't block image loading at the IMAP level
- The `OPENAI_API_KEY` is needed for trafilatura's LLM-enhanced extraction (optional) and existing RAG pipeline
- All data stays local on the Mac; no cloud processing except NotebookLM and OpenAI API calls

---

## Verification Checklist

- [ ] himalaya installed and configured for Gmail IMAP
- [ ] Can list emails in "Newsletters" label via CLI
- [ ] newsletter_parser.py extracts links from a test newsletter email
- [ ] Links are correctly classified (research vs news vs github)
- [ ] Tracking URLs are resolved to canonical destinations
- [ ] Deduplication works against existing sources.json
- [ ] Research links appear in sources.json with `origin: "newsletter"`
- [ ] News links appear in news-queue.json
- [ ] news-digest skill creates NotebookLM notebook with briefing structure
- [ ] News podcast (brief format) is generated and delivered via Telegram
- [ ] Weekly cron includes newsletter-monitor + news-digest steps
- [ ] `/subscribe`, `/unsubscribe`, `/list newsletters` commands work
- [ ] `/generate news` triggers news digest manually
