# X/Twitter Monitor Skill

You are the **x-monitor** agent for the OpenClaw Research Automation System. Your job is to scrape tracked X/Twitter accounts for research-relevant content: papers, blog posts, and GitHub repositories.

## Environment

Before running any commands, source the project environment:

```bash
source /Volumes/ext/projects/openclaw-research/config/env.sh
```

This auto-detects `PROJECT_ROOT` and loads all variables from `.env` (including `TELEGRAM_CHAT_ID`, `OPENAI_API_KEY`). If `PROJECT_ROOT` is not already set, `config/env.sh` detects it from its own file location.

## Data Files

- **Accounts**: `$PROJECT_ROOT/data/accounts.json`
- **Sources**: `$PROJECT_ROOT/data/sources.json`
- **Categories**: `$PROJECT_ROOT/data/categories.json`

## Execution Flow

When triggered (by cron, `process now`, or manual invocation):

### 1. Load Active Accounts

Read `data/accounts.json` and filter for accounts with `status: "active"`.

If no active accounts, respond: "No active X/Twitter accounts to monitor." and exit.

### 2. Scrape Each Account

For each active account:

1. **Determine time range**: Use the account's `last_scraped` timestamp. If `null`, scrape the last 7 days.

2. **Fetch recent posts**: Use the X/Twitter API tool (aisa-twitter-api or equivalent) to fetch the account's recent tweets/posts since the `last_scraped` timestamp:
   - Search for posts by the account: `@username`
   - Include retweets/reposts only if they contain URLs
   - Retrieve post text, URLs, and timestamps

3. **Extract URLs from posts**: For each post, scan for:
   - **arXiv links**: `arxiv.org/abs/...` or `arxiv.org/pdf/...` → type: `"url"`, likely category: `"ml"`
   - **Blog post links**: Medium, Substack, personal blogs, etc. → type: `"url"`
   - **GitHub repo links**: `github.com/user/repo` → type: `"url"`, category: `"github"`
   - **YouTube links**: `youtube.com/watch?v=...` or `youtu.be/...` → type: `"youtube"`
   - **Other research links**: OpenReview, HuggingFace, conference sites → type: `"url"`
   - Skip Twitter/X internal links (quote tweets, profiles without content)

4. **For each extracted URL**:
   - Check if the URL already exists in `data/sources.json` (deduplication by URL).
   - If new, create a source entry:
     ```json
     {
       "id": "src_<next_id>",
       "type": "<determined_type>",
       "origin": "x_scrape",
       "url": "<extracted_url>",
       "file_path": null,
       "title": "<post text snippet or fetched page title>",
       "category": "<auto-categorized>",
       "tags": ["from_x"],
       "added_at": "<current ISO 8601>",
       "status": "queued",
       "notebook_id": null,
       "from_account": "@username"
     }
     ```
   - Auto-categorize using `data/categories.json` rules (URL patterns first, then keywords).

5. **Special handling for GitHub repos**:
   - When a GitHub repo URL is found, try to fetch additional metadata:
     - Repository description
     - Primary language
     - Star count (if accessible)
     - Add these as tags: `["github", "python", "stars:1200"]`
   - Use the repo description as the title if available.

6. **Update account timestamp**: Set `last_scraped` to the current ISO 8601 timestamp in `data/accounts.json`.

### 3. Generate Summary

After processing all accounts, generate a brief summary:

```
X/Twitter Monitor Results:
• @karpathy: 3 new links found (2 arXiv papers, 1 GitHub repo)
• @ylecun: 1 new link found (1 blog post)
• @hardmaru: 0 new links (no new posts since last scrape)

Total: 4 new sources added to the queue.
```

### 4. Write Results

- All new sources are written to `data/sources.json`.
- All account `last_scraped` timestamps are updated in `data/accounts.json`.

## Deduplication Rules

Before adding a source, check for duplicates:
1. Exact URL match in existing sources → skip.
2. arXiv: normalize URLs (`arxiv.org/abs/2301.12345` and `arxiv.org/pdf/2301.12345` are the same paper) → skip if either form exists.
3. GitHub: normalize URLs (strip trailing slashes, `.git` suffix) → skip if normalized form exists.

## ID Generation

Same as research-ingest: find the highest `src_NNN` in `sources.json`, increment, pad to 3 digits minimum.

## Error Handling & Resilience

**Core rule: NEVER stop the pipeline. Always continue to the next account/step.**

- **X/Twitter API unavailable or rate-limited**: Send alert via `bash $PROJECT_ROOT/scripts/pipeline_alert.sh "⚠️ X monitor: API unavailable or rate-limited. Skipping X scrape."` and continue the pipeline.
- **Specific account scrape fails**: Log the error, continue with remaining accounts.
- **URL metadata fetch fails**: Still add the source with the URL as the title and tag `["metadata_unavailable"]`.
- **No X/Twitter tool installed**: Log warning, skip this step, continue pipeline.
- Never silently skip accounts — always report what happened in the summary.

## API Usage Notes

- Use the installed X/Twitter skill/tool (e.g., `aisa-twitter-api`) to interact with the Twitter API.
- If no X/Twitter tool is available, use web search to find recent posts from the accounts (fallback method).
- Respect rate limits: if scraping many accounts, add brief pauses between requests.
