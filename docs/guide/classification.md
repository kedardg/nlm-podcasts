# Link Classification & URL Normalization

## Two-Layer Classification System

Link classification happens at two levels:

1. **URL-level classification** — What *type* of content is this? (research, github, youtube, news, blog, social)
2. **Category-level classification** — What *topic* does this belong to? (ml, nlp, robotics, oss, tech, etc.)

```
URL arrives (e.g., "https://arxiv.org/abs/2301.12345")
        │
        ▼
URL Classification (newsletter_parser.py: classify_url)
        │ → "research"
        │
        ▼
Category Classification (router.py or newsletter_parser.py)
        │
        ├── URL pattern match (categories.json url_patterns)
        │   → "arxiv.org" matches → category: "ml"
        │
        ├── Keyword match (categories.json keywords)
        │   → title contains "transformer" → category: "ml"
        │
        └── Fallback → category: "general"
```

## URL Classification (`newsletter_parser.py`)

The `classify_url()` function determines the content *type*:

| Pattern | Classification | Destination |
|---|---|---|
| `arxiv.org`, `openreview.net`, `paperswithcode.com` | `research` | sources.json |
| `github.com`, `gitlab.com` | `github` | sources.json |
| `youtube.com`, `youtu.be` | `youtube` | sources.json |
| `twitter.com`, `x.com` | `social` | **skipped** (noise from newsletters) |
| Known news domains | `news` | news-queue.json |
| Known blog platforms (medium.com, substack, dev.to) | `blog` | news-queue.json |
| Everything else | `unknown` | news-queue.json (if title looks real) |

This determines which data file the link goes into. Research-type content goes to `sources.json` for the weekly deep-dive pipeline. News-type content goes to `news-queue.json` for the daily feed and weekly news digest.

## Category Classification (`categories.json`)

After URL classification, the link gets a fine-grained category for grouping into notebooks and daily feed sections.

### 9 Categories

```json
{
  "categories": [
    {"name": "Machine Learning",     "slug": "ml",        "daily_section": "AI Research",  "url_patterns": ["arxiv.org", "openreview.net", "paperswithcode.com"]},
    {"name": "NLP & Language Models", "slug": "nlp",       "daily_section": "AI Research",  "url_patterns": []},
    {"name": "Computer Vision",       "slug": "cv",        "daily_section": "AI Research",  "url_patterns": []},
    {"name": "Robotics & Embodied AI","slug": "robotics",  "daily_section": "AI Research",  "url_patterns": []},
    {"name": "AI Safety & Policy",    "slug": "ai-safety", "daily_section": "News",         "url_patterns": []},
    {"name": "Open Source & Tools",   "slug": "oss",       "daily_section": "Development",  "url_patterns": ["github.com", "gitlab.com"]},
    {"name": "Tech Industry",         "slug": "tech",      "daily_section": "Tech",         "url_patterns": []},
    {"name": "Developer Experience",  "slug": "devx",      "daily_section": "Development",  "url_patterns": ["dev.to", "hashnode.dev"]},
    {"name": "General",               "slug": "general",   "daily_section": "News",         "url_patterns": []}
  ]
}
```

### Classification Priority

1. **URL pattern matching** (highest priority):
   ```python
   # router.py:80-88, newsletter_parser.py:766-774
   def categorize_by_url(url, categories):
       for cat in categories["categories"]:
           for pattern in cat["url_patterns"]:
               if pattern in url:
                   return cat["slug"]
       return None
   ```
   Simple substring match. If `"arxiv.org"` is in the URL → category `ml`.

2. **Keyword matching** (secondary):
   ```python
   # router.py:91-105, newsletter_parser.py:777-791
   def categorize_by_keywords(text, categories):
       text_lower = text.lower()
       best_slug = None
       best_count = 0
       for cat in categories["categories"]:
           if cat["slug"] == "general":
               continue  # Skip general — it's the fallback
           count = sum(1 for kw in cat["keywords"] if kw.lower() in text_lower)
           if count > best_count:
               best_count = count
               best_slug = cat["slug"]
       return best_slug
   ```
   Counts keyword matches in the title/text. Highest count wins. General is excluded from matching (it's the fallback).

3. **Fallback**: `"general"` — the catch-all category.

### Where Classification Happens

| Entry Point | Code | Uses |
|---|---|---|
| Telegram URL submission | `router.py:cmd_ingest_url()` line 462-466 | `categorize_by_url()` → fallback "general" |
| Telegram `/note` command | `router.py:cmd_note()` line 418 | `categorize_by_keywords()` → fallback "general" |
| Newsletter link routing | `newsletter_parser.py:route_links()` line 672-677 | `_categorize_by_url()` → `_categorize_by_keywords()` → "general" |
| Content processor skill | `content-processor/SKILL.md` Step 2 | URL patterns → keywords → "general" (LLM-driven) |

The content-processor skill provides a second pass for sources still categorized as `"auto"` — it uses the LLM for more nuanced categorization including content analysis.

---

## URL Normalization (`url_utils.py`)

Deep URL normalization ensures the same content isn't added twice, even when URLs differ in format.

### `normalize_url_deep(url)` — The Core Function

Used by `router.py`, `newsletter_parser.py`, and `data_triage.py` for deduplication.

**Normalization rules** (url_utils.py:53-139):

1. **Scheme**: Always normalize to `https://`
2. **Domain**: Lowercase, strip `www.` prefix
3. **Trailing slash**: Strip from path
4. **Fragment**: Strip entirely (the `#section` part)
5. **Tracking parameters**: Strip 30+ known tracking params:
   ```
   utm_source, utm_medium, utm_campaign, utm_term, utm_content, utm_id, utm_cid,
   fbclid, gclid, gclsrc, mc_cid, mc_eid, ref, source, s, si, mkt_tok, ...
   ```

6. **arXiv normalization**:
   ```
   arxiv.org/abs/2301.12345       → https://arxiv.org/abs/2301.12345
   arxiv.org/pdf/2301.12345.pdf   → https://arxiv.org/abs/2301.12345
   arxiv.org/html/2301.12345v2    → https://arxiv.org/abs/2301.12345
   ```
   Strips version suffix (`v2`), normalizes abs/pdf/html to `abs/`.

7. **YouTube normalization**:
   ```
   youtu.be/dQw4w9WgXcQ                          → https://youtube.com/watch?v=dQw4w9WgXcQ
   youtube.com/watch?v=dQw4w9WgXcQ&si=abc         → https://youtube.com/watch?v=dQw4w9WgXcQ
   youtube.com/embed/dQw4w9WgXcQ                   → https://youtube.com/watch?v=dQw4w9WgXcQ
   youtube.com/shorts/dQw4w9WgXcQ                  → https://youtube.com/watch?v=dQw4w9WgXcQ
   ```
   Extracts the 11-character video ID from any format.

8. **GitHub/GitLab normalization**:
   ```
   github.com/User/Repo.git  → https://github.com/user/repo
   GITHUB.COM/HuggingFace/Transformers → https://github.com/huggingface/transformers
   ```
   Lowercase entire path, strip `.git` suffix.

9. **HuggingFace normalization**:
   ```
   huggingface.co/paper/2301.12345  → https://huggingface.co/papers/2301.12345
   ```
   Normalizes `/paper/` → `/papers/` (canonical form).

### Cross-Stream Deduplication

The `dedup_urls()` function (url_utils.py:142-224) checks for duplicates across both data files:

```python
# 1. Within sources.json — keep earliest by added_at
# 2. Within news-queue.json — keep earliest by extracted_at
# 3. Cross-stream: if same URL in both files, mark news item as duplicate
```

This is called by `data_triage.py` during the weekly pre-processing step.

### Dedup at Ingestion Points

Every entry point checks for duplicates before adding:

| Entry Point | Code | Dedup Method |
|---|---|---|
| Telegram URL | `router.py:cmd_ingest_url()` line 446-461 | `normalize_url_deep()` against sources.json AND news-queue.json |
| Newsletter links | `newsletter_parser.py:route_links()` line 656-703 | `is_duplicate_source()`, `is_duplicate_news()`, cross-stream check |
| X/Twitter scrape | x-monitor skill | Exact URL match + arXiv/GitHub normalization |
| Weekly triage | `data_triage.py:run_triage()` line 122-123 | Full `dedup_urls()` across both data files |

### Self-Test

```bash
# Run the built-in normalization tests
.venv/bin/python3 url_utils.py
```

Tests cover arXiv equivalence, GitHub case insensitivity, YouTube ID extraction, tracking param stripping, HuggingFace normalization, www stripping, and trailing slash removal.

---

## Data Triage (`data_triage.py`)

Pre-processing step that runs before the weekly pipeline. Combines dedup + volume analysis + notebook grouping.

### Grouping Rules (line 32-35)

```python
MIN_SOURCES_FOR_OWN_NOTEBOOK = 3    # Below this → merge with affinity partner
MAX_SOURCES_PER_NOTEBOOK = 50       # Hard limit per notebook
IDEAL_SPLIT_SIZE = 15               # Target size when splitting fat categories
MAX_BEFORE_SPLIT = 30               # Above this → split into sub-notebooks
```

| Condition | Action |
|---|---|
| 0 sources | Skip, log warning |
| <3 sources | Merge with affinity partner (same `daily_section`, largest sibling) |
| 3–30 sources | Own notebook |
| >30 sources | Split into sub-notebooks of ~15 each |
| >50 (hard limit) | Truncated |

**Affinity partners** (line 74-96): When a thin category needs merging, it joins the largest sibling in the same `daily_section`. For example, `cv` (2 sources) would merge with `ml` (15 sources) since both are in "AI Research".

### Output: `data/triage-plan.json`

```json
{
  "version": 1,
  "generated_at": "2026-02-15T09:00:00Z",
  "dedup_report": { "sources_removed": [...], "news_removed": [...] },
  "category_counts": { "ml": 8, "nlp": 3, "oss": 4 },
  "notebook_groups": [
    {
      "notebook_label": "Machine Learning",
      "notebook_slug": "ml",
      "categories": ["ml"],
      "source_ids": ["src_001", "src_002"],
      "source_count": 8,
      "is_merged": false,
      "is_split": false
    }
  ],
  "warnings": ["No items in category: cv"],
  "summary": { "total_queued_sources": 15, "total_notebooks_planned": 3, "duplicates_found": 2 }
}
```

The content-processor reads this plan and uses its `notebook_groups` for notebook creation instead of the default one-per-category approach.

### Commands

```bash
# Cron mode — apply rules and save plan
.venv/bin/python3 data_triage.py --auto

# Preview without saving
.venv/bin/python3 data_triage.py --dry-run

# Print summary (for Telegram /status triage)
.venv/bin/python3 data_triage.py --summary
```

The `--summary` mode checks for a fresh plan (<2h old) first. If found, formats it. Otherwise generates a fresh one in dry-run mode.
