# YouTube Channel Monitor — Feature Plan

## Problem

The system can ingest individual YouTube videos (via newsletters, X/Twitter scrapes, or direct Telegram URLs), but there's no way to **follow a YouTube channel** and automatically ingest new videos. Users must manually send video URLs or hope they appear in a newsletter.

Additionally, videos from different domains (ML research, tech news, dev tutorials) should be separated into **per-category NLM notebooks** with their own podcasts and reports — not crammed into one generic notebook.

## Available Tools

### Option A: ClawHub `youtube-full` (OpenClaw skill)

- **Install**: `npx clawhub@latest install youtube-full`
- **Repo**: [ZeroPointRepo/youtube-skills](https://github.com/ZeroPointRepo/youtube-skills)
- 12 skills including `youtube-channels` (browse uploads, get latest videos, resolve @handles)
- Channel resolve + latest are **free** (0 credits). Other ops: 1 credit each.
- Free tier: 100 credits on signup, no credit card
- Already designed for OpenClaw agents — callable from SKILL.md
- **Limitation**: No persistent tracking/state — needs our own `channels.json` + orchestration script

### Option B: `mcp-youtube-intelligence` MCP server (recommended)

- **Install**: `pip install mcp-youtube-intelligence` or `uvx mcp-youtube-intelligence`
- **Repo**: [JangHyuckYun/mcp-youtube-intelligence](https://github.com/JangHyuckYun/mcp-youtube-intelligence)
- **`monitor_channel` tool** with built-in RSS tracking: `add`, `check`, `list`, `remove` actions
- RSS-based — no YouTube API key needed for monitoring
- 9 tools total: `get_video`, `get_transcript`, `search_youtube`, `get_playlist`, `get_comments`, `extract_entities`, `segment_topics`, `search_transcripts`, `monitor_channel`
- Needs `yt-dlp` for subtitle extraction (optional for monitoring)
- **Best fit**: has the exact subscribe/check/list/remove lifecycle needed

### Option C: YouTube RSS + feedparser (custom script)

- YouTube RSS feed: `https://www.youtube.com/feeds/videos.xml?channel_id=CHANNEL_ID`
- Free, no API key, returns ~15 most recent videos with titles/descriptions
- Need to resolve channel URL → channel_id (scrape HTML with requests + BeautifulSoup, both already installed)
- Only new dependency: `feedparser`
- **Most control**: follows `newsletter_parser.py` pattern exactly, zero external services

### Option D: `youtube-mcp-server` (YouTube Data API)

- **Install**: `npm install -g zubeid-youtube-mcp-server`
- **Repo**: [ZubeidHendricks/youtube-mcp-server](https://github.com/ZubeidHendricks/youtube-mcp-server)
- `listVideos` tool: list channel videos by channelId
- Requires `YOUTUBE_API_KEY` (Google Cloud, free tier 10K units/day)
- **Most reliable** but adds API key dependency

### Recommendation

**Primary**: Option B (mcp-youtube-intelligence) — built-in `monitor_channel` with RSS, no API key, MCP server integration.

**Fallback**: Option C (custom feedparser) — if zero external dependency is preferred.

**Supplement**: Option A (ClawHub youtube-full) — install for transcript extraction + video search alongside whichever monitoring approach is chosen.

## Design

### Domain Separation (per-channel category routing)

Each tracked channel gets:
- `default_category` — slug (e.g., `ml`, `nlp`, `tech`) assigned at track time
- `route_to` — `"research"` (→ sources.json → weekly pipeline) or `"news"` (→ news-queue.json → news digest)

Videos flow through the existing pipeline which creates **separate NLM notebooks per category**, each with its own podcast and report:

```
Channel (@3blue1brown, default_category: "ml", route_to: "research")
  → RSS → new video
    → category: "ml" (from channel default)
    → sources.json (origin: "youtube_channel")
      → content-processor groups with other ML sources
        → notebooklm-orchestrator: ML notebook
          → ML podcast + ML report

Channel (@TechLinked, default_category: "tech", route_to: "news")
  → RSS → new video
    → category: "tech" (from channel default)
    → news-queue.json
      → news-digest: tech news briefing + podcast
```

If no `default_category` is set, fall back to per-video keyword matching on title/description against `categories.json`.

### Data Model — `data/channels.json`

```json
{
  "channels": [
    {
      "id": "ch_001",
      "channel_id": "UCYO_jab_esuFRV4b17AJtAw",
      "channel_name": "3Blue1Brown",
      "handle": "@3blue1brown",
      "feed_url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCYO_jab_esuFRV4b17AJtAw",
      "original_url": "https://www.youtube.com/@3blue1brown",
      "default_category": "ml",
      "route_to": "research",
      "added_at": "2026-02-20T12:00:00Z",
      "last_checked": null,
      "last_video_published": null,
      "total_videos_ingested": 0,
      "status": "active"
    }
  ]
}
```

### Router UX — extend `/track`

```
/track https://youtube.com/@3blue1brown category: ml
  → tracks channel, default_category: "ml", route_to: "research"

/track https://youtube.com/@TechLinked category: tech route: news
  → tracks channel, default_category: "tech", route_to: "news"

/track https://youtube.com/@someChannel
  → tracks channel, categorization by per-video keyword matching

/track off 3Blue1Brown
  → pauses tracking

/list channels
  → shows all tracked channels with status, category, video count
```

Bare channel URLs sent as messages should suggest `/track` instead of ingesting.

### Core Script — `youtube_channel_monitor.py`

Follows `newsletter_parser.py` pattern (project root):

| Function | Purpose |
|----------|---------|
| `resolve_channel_id(url)` | Scrape channel page HTML to extract `channel_id` from meta tags. Handles `/@handle`, `/c/Name`, `/channel/UC...`. Uses requests + BeautifulSoup (already installed). |
| `fetch_channel_feed(channel)` | Parse RSS via feedparser (or mcp-youtube-intelligence `monitor_channel`). Return videos newer than `last_video_published`. |
| `categorize_video(video, channel, categories)` | Use `channel.default_category` if set. Fallback: keyword matching on title+description via `categories.json`. |
| `route_videos(videos, channel)` | Dedup via `normalize_url_deep()` against sources.json + news-queue.json. Route based on `channel.route_to`. |
| `monitor_channels(--check, --since)` | Main entry. Load active channels, fetch feeds, route, update timestamps. |

CLI:
```bash
.venv/bin/python3 youtube_channel_monitor.py           # Normal run
.venv/bin/python3 youtube_channel_monitor.py --check   # Dry-run
.venv/bin/python3 youtube_channel_monitor.py --since 2026-02-01
```

### New Skill — `skills/youtube-monitor/SKILL.md`

Runs in both pipelines:
- **Daily** (6 PM ET): Step 0.5 — before PDF generation, so new videos appear in same-day feed
- **Weekly** (Saturday 9 AM): Step 2.5 — after x-monitor, before data triage

### url_utils.py Changes

Add YouTube channel URL detection:
```python
YOUTUBE_CHANNEL_PATTERNS = [
    re.compile(r"youtube\.com/channel/(UC[a-zA-Z0-9_-]+)"),
    re.compile(r"youtube\.com/@([a-zA-Z0-9_.-]+)"),
    re.compile(r"youtube\.com/c/([a-zA-Z0-9_.-]+)"),
]

def is_youtube_channel_url(url): ...
def normalize_youtube_channel_url(url): ...
```

### router.py Changes

- Extend `cmd_track()` — detect YouTube channel URLs, route to `cmd_track_channel()`
- New `cmd_track_channel()` — resolve channel_id, accept `category:` and `route:` args
- Extend `cmd_untrack()` — also search `channels.json` by name/handle
- New `cmd_list_channels()` — list tracked channels
- Extend `cmd_status()` — add channel count
- Extend `cmd_manual()` — add YouTube commands
- Intercept bare channel URLs — suggest `/track` instead

## Files to Create/Modify

| File | Action |
|------|--------|
| `data/channels.json` | Create (empty schema) |
| `youtube_channel_monitor.py` | Create (core script) |
| `skills/youtube-monitor/SKILL.md` | Create (skill definition) |
| `url_utils.py` | Modify (add channel URL detection) |
| `router.py` | Modify (extend /track, /list, status, manual) |
| `requirements.txt` | Modify (add feedparser if Option C) |
| `Dockerfile` | Modify (add feedparser to pip install) |
| `setup.sh` | Modify (add feedparser to venv install) |
| `config/cron-jobs.json` | Modify (add youtube-monitor step) |
| `TOOLS.md` (workspace) | Modify (add step in daily + weekly pipelines) |
| `CLAUDE.md` | Modify (architecture, skills, data files, cron) |

## Implementation Order

1. `url_utils.py` — channel detection functions + self-tests
2. `data/channels.json` — empty data file
3. `youtube_channel_monitor.py` — core script (testable with `--check`)
4. `router.py` — extend /track, /list, cmd_status, cmd_manual
5. `skills/youtube-monitor/SKILL.md` — skill definition
6. Dependencies — feedparser in requirements.txt / Dockerfile / setup.sh
7. Pipeline — cron-jobs.json, TOOLS.md
8. Documentation — CLAUDE.md

## Verification

```bash
# url_utils self-tests (including channel detection)
.venv/bin/python3 url_utils.py

# No channels → clean exit
.venv/bin/python3 youtube_channel_monitor.py --check

# Track a channel
.venv/bin/python3 router.py "track https://www.youtube.com/@3blue1brown category: ml"

# Dry-run shows new videos
.venv/bin/python3 youtube_channel_monitor.py --check

# Real run adds to sources.json
.venv/bin/python3 youtube_channel_monitor.py

# Verify: sources.json has entries with origin: "youtube_channel", category: "ml"

# List + untrack
.venv/bin/python3 router.py "list channels"
.venv/bin/python3 router.py "track off 3Blue1Brown"

# Bare channel URL intercepted → suggests /track
.venv/bin/python3 router.py "https://youtube.com/@3blue1brown"
```
