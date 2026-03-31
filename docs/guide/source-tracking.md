# X/Twitter & Email Tracking

## Two Ingestion Channels

```
              ┌── X/Twitter Accounts ──→ x-monitor skill ──→ data/sources.json
Tracked       │
Sources   ────┤
              └── Newsletter Emails ──→ newsletter_parser.py ──┬→ data/sources.json (research)
                                                               └→ data/news-queue.json (news)
```

Both channels feed into the same downstream pipelines (daily feed, weekly digest, RAG).

---

## X/Twitter Monitoring

### Account Management (via router.py)

Users manage tracked accounts via Telegram:

```
/track @karpathy          → adds to accounts.json, status: "active"
/track off @karpathy      → sets status: "paused"
/list accounts            → shows all accounts with status + last scraped date
```

The router (`router.py`) handles these commands deterministically:

**`cmd_track(args)`** (router.py:187-217):
1. Parses username from various formats: `@user`, `user`, `https://x.com/user`, `https://twitter.com/user`
2. Dedup check against existing accounts (case-insensitive)
3. If paused account exists, reactivates it
4. If new, appends to `data/accounts.json`:

```json
{
  "username": "@karpathy",
  "added_at": "2026-02-15T10:00:00Z",
  "last_scraped": null,
  "scrape_for": ["papers", "blogposts", "github_repos"],
  "status": "active"
}
```

**Username extraction** (`extract_x_username()`, router.py:121-136):
- URL format: `https://x.com/username` or `https://twitter.com/username`
- @-prefixed: `@username`
- Bare text: `username`
- All normalize to `@username`

### Scraping Flow (x-monitor skill)

The x-monitor skill runs during the weekly cron pipeline (Step 2, after newsletter-monitor):

1. **Load active accounts** from `data/accounts.json`
2. **For each account**: Use `aisa-twitter-api` (installed ClawHub skill) to fetch recent posts since `last_scraped`
3. **Extract URLs** from post content:
   - arXiv links → type `url`, category via keyword matching
   - GitHub repos → type `url`, category `oss`
   - YouTube videos → type `youtube`
   - Blog posts (Medium, Substack, etc.) → type `url`
   - Skip internal Twitter/X links
4. **Dedup** against existing `sources.json` entries (exact URL match + arXiv/GitHub normalization)
5. **Add new sources** to `sources.json` with `origin: "x_scrape"`, `from_account: "@username"`
6. **Update `last_scraped`** timestamp on each account

### GitHub Repo Enrichment

When a GitHub URL is found, the x-monitor tries to fetch:
- Repository description → used as `title`
- Primary language → added to `tags`
- Star count → added to `tags` as `stars:1200`
- Tags: `["github", "python", "stars:1200"]`

### Error Handling

- If the X/Twitter API is unavailable or rate-limited: log error, report which accounts couldn't be scraped
- If a specific account fails: continue with remaining accounts
- If no X tool is available: fallback to web search for recent posts

---

## Newsletter/Email Monitoring

### Architecture

```
Gmail IMAP (App Password auth)
        │
        ▼
newsletter_parser.py
        │
        ├── Connects to IMAP server
        ├── Fetches emails matching plus_alias (e.g., +ai)
        ├── Parses HTML body → extracts links
        ├── Classifies each link
        ├── Routes to appropriate data file
        │
        ├──→ Research/GitHub/YouTube → data/sources.json
        └──→ News/Blog → data/news-queue.json
```

### Gmail Setup

Uses a dedicated Gmail address with `+` alias trick:
- `kedar-research+ai@gmail.com` — subscribe all AI newsletters to this
- 2FA enabled, App Password generated at `myaccount.google.com/apppasswords`
- Credentials stored at `~/.config/openclaw-research/email-credentials.json`:

```json
{
  "email": "your-email+ai@gmail.com",
  "app_password": "xxxx xxxx xxxx xxxx",
  "imap_host": "imap.gmail.com",
  "imap_port": 993,
  "plus_alias": "ai"
}
```

### `newsletter_parser.py` — Code Walkthrough

**Main pipeline** (`process_newsletters()`, line 833-925):

1. **Load credentials** from `~/.config/openclaw-research/email-credentials.json`
2. **Connect via IMAP** to `imap.gmail.com:993` with SSL
3. **Fetch emails** since `last_global_check` date (or last 7 days default), filtered to plus alias
4. **For each email**:
   - Parse sender, subject, HTML body
   - Find or auto-create newsletter entry in `newsletters.json`
   - Extract links from HTML using BeautifulSoup
   - Route links via `route_links()`
5. **Update** `last_global_check` timestamp in newsletters.json

**Link extraction** (`extract_links()`, line ~520-582):
- Parses all `<a href>` tags from HTML body
- Filters out junk: tracking pixels, tiny anchor text (<3 chars), boilerplate ("click here", "unsubscribe")
- Resolves tracking redirects (follows HTTP redirects to get canonical URL)
- Deduplicates within the email
- Returns list of `{url, original_url, anchor_text}`

**URL classification** (`classify_url()`, not shown but in the file):
Routes URLs to categories:
- `arxiv.org`, `openreview.net`, `paperswithcode.com` → `research`
- `github.com`, `gitlab.com` → `github`
- `youtube.com`, `youtu.be` → `youtube`
- `twitter.com`, `x.com` → `social` (skipped — noise from newsletters)
- Known news domains → `news`
- Known blog platforms → `blog`
- Unknown → `unknown` (routed to news if anchor text looks like a real title)

**Link routing** (`route_links()`, line 632-763):
```
For each classified link:
  ├── research/github/youtube → check dedup → add to sources.json
  ├── news/blog → check dedup → add to news-queue.json
  ├── social → skip (noise)
  └── unknown → if anchor text ≥3 words or ≥20 chars → add to news-queue.json
```

Cross-stream dedup: checks BOTH `sources.json` and `news-queue.json` to prevent the same URL appearing in both.

**Categorization within routing** (line 672-677 for research, line 708 for news):
```python
cat = _categorize_by_url(url, categories)      # URL pattern match first
if not cat:
    cat = _categorize_by_keywords(title, categories)  # Keyword match second
if not cat:
    cat = "general"                              # Fallback
```

### Newsletter Auto-Discovery

If an email arrives from an unknown sender (not matching any `sender_pattern` in newsletters.json), the parser auto-creates a newsletter entry:

```python
# find_or_create_newsletter() — line 797-827
# Extracts name from sender field: "TLDR AI <dan@tldrnewsletter.com>" → "TLDR AI"
# Uses email address as sender_pattern
```

### Commands

```bash
# Normal run (process since last check)
.venv/bin/python3 newsletter_parser.py

# Dry-run (preview extracted links without saving)
.venv/bin/python3 newsletter_parser.py --check

# Process since specific date
.venv/bin/python3 newsletter_parser.py --since 2026-02-01

# Process single email by IMAP ID
.venv/bin/python3 newsletter_parser.py --email-id 12345
```

### Newsletter Subscription Management (via router.py)

```
/subscribe TLDR AI from: dan@tldrnewsletter.com   → adds to newsletters.json
/subscribe off TLDR AI                              → sets status: "paused"
/list newsletters                                   → shows all with status + extracted count
```

`data/newsletters.json` schema:
```json
{
  "newsletters": [
    {
      "id": "nl_001",
      "name": "TLDR AI",
      "sender_pattern": "dan@tldrnewsletter.com",
      "content_type": "mixed",
      "added_at": "2026-02-01T10:00:00Z",
      "last_checked": "2026-02-15T18:00:00Z",
      "total_extracted": 47,
      "status": "active"
    }
  ],
  "config": {
    "last_global_check": "2026-02-15T18:00:00Z",
    "check_folder": "INBOX"
  }
}
```

### Multi-Project Reuse

The same Gmail account supports multiple projects via `+` aliases:
- `your-email+ai@gmail.com` — this project
- `your-email+travel@gmail.com` — future travel project
- Each project uses its own `plus_alias` in credentials file
