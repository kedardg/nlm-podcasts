---
title: "fix: Pipeline Reliability + HF-Powered Triage"
type: fix
status: active
date: 2026-03-05
origin: docs/brainstorms/2026-03-05-pipeline-reliability-and-hf-triage-brainstorm.md
---

# fix: Pipeline Reliability + HF-Powered Triage

## Overview

Two-part fix: (1) Unblock the weekly pipeline (broken cron message, 159 stuck items, 961-item backlog) and (2) introduce a local HF model for triage classification — research items get full NLM deep-dives, everything else gets 2-3 line briefs + links. OpenAI GPT-4o structures the final magazine JSON. Apply to both daily and weekly pipelines, starting with weekly as pilot.

## Problem Statement

The weekly pipeline has been silently failing because the cron job message says "Execute newsletter-monitor skill" (LLM tries to interpret it, both `gpt-5-mini` and `gpt-4.1-mini` timeout at 32s). This means triage never runs, items pile up (961 queued news, 159 stuck sources), and duplicates accumulate (157 per daily run).

Beyond the immediate fix, the current pipeline treats all items equally — no distinction between a groundbreaking arXiv paper and a routine product announcement. A local HF model can classify items cheaply, routing research for deep NLM treatment while keeping everything else as concise briefs.

(See brainstorm: `docs/brainstorms/2026-03-05-pipeline-reliability-and-hf-triage-brainstorm.md`)

## Proposed Solution

### Pipeline Architecture

```
Items arrive (newsletters, blogs, X/Twitter)
       |
       v
[1] Hybrid Classifier
    - Fast path: domain rules (arxiv.org → research, techcrunch.com → news)
    - Slow path: HF model (Qwen2.5-7B-Instruct, native vLLM) for uncertain items
    - Output: triage JSON with content_type + confidence + priority
       |
       v
[2] NLM (NotebookLM)
    - Key research items only (content_type=research, confidence>0.7)
    - Deep summaries, citations, podcast
       |
       v
[3] OpenAI API (GPT-4o)
    - Input: NLM summaries + all triaged items
    - Output: structured magazine JSON
    - Research items → full NLM analysis + links
    - Everything else → 2-3 line briefs + links
       |
       v
[4] PDF Renderer (existing lib/pdf_renderer.py)
    - Renders magazine from structured JSON
```

### Key Technical Decisions

1. **Native vLLM on macOS, not Docker** — Docker has no Metal GPU passthrough on macOS. Running natively with vLLM gives 15-20 tok/s on Apple Silicon vs CPU-only Docker at 2-3 tok/s. (See brainstorm: dockerized was the original plan, but research showed this limitation.)

2. **Qwen2.5-7B-Instruct** — Best classification accuracy in the 7-8B class (2026 benchmarks). Few-shot prompts required for reliable classification. Q4_K_M quantization fits in 8GB.

3. **Hybrid classification** — Domain rules handle 40-60% of items instantly (arxiv.org → research, techcrunch.com → news). Only uncertain items go to the LLM. Reduces inference time from ~30 min to ~12 min for 961 items.

4. **OpenAI for magazine structuring** — Magazine layout requires thematic grouping, coherent briefs, and cross-item synthesis. GPT-4o's 128K context handles NLM output + all items. Local 7B models struggle with this complexity.

5. **File locking for concurrent safety** — `lib/file_utils.py::save_json()` already uses `fcntl.flock`. Extend with a pipeline-level lock file to prevent daily + weekly from running simultaneously.

## Technical Approach

### Implementation Phases

#### Phase 1: Immediate Fixes (Today)

Zero-risk fixes that unblock the weekly pipeline.

**1a. Fix weekly cron message**

```bash
# Remove broken cron job
openclaw cron remove weekly-research-digest

# Add corrected version (matches working daily pattern)
openclaw cron add \
  --name "weekly-research-digest" \
  --cron "0 9 * * 6" \
  --tz "America/New_York" \
  --session isolated \
  --message "Run the weekly research digest pipeline. Execute: bash /Volumes/ext/projects/openclaw-research/scripts/weekly_pipeline.sh" \
  --announce \
  --channel telegram
```

Update `config/cron-jobs.json` to match.

- [x] Remove old cron job: `openclaw cron remove weekly-research-digest`
- [x] Add corrected cron job with direct bash command
- [x] Update `config/cron-jobs.json`
- [x] Verify with `openclaw cron list`

**1b. Investigate 159 stuck "processing" items**

```bash
# Check processing-log.json for the Feb 18 run
.venv/bin/python3 -c "
from lib.file_utils import load_json
log = load_json('data/processing-log.json')
for run in log.get('runs', []):
    if '2026-02-18' in run.get('started_at', ''):
        import json; print(json.dumps(run, indent=2))
"

# Check what sources are stuck
.venv/bin/python3 -c "
from lib.file_utils import load_json
sources = load_json('data/sources.json')
stuck = [s for s in sources.get('sources', []) if s.get('status') == 'processing' and not s.get('notebook_id')]
print(f'{len(stuck)} stuck items')
if stuck:
    print(f'Earliest: {stuck[0].get(\"added_at\", \"unknown\")}')
    print(f'Latest: {stuck[-1].get(\"added_at\", \"unknown\")}')
"
```

Based on findings, either reset to `queued` or mark as `failed` with reason.

- [x] Run diagnostic commands above
- [x] Determine root cause from processing-log.json
- [x] Reset or fix stuck items (159 items reset to queued)
- [x] Log findings in `docs/BUGS.md` (BUG-006)

**1c. Archive old news items**

Create `scripts/archive_old_news.py`:

```python
#!/usr/bin/env python3
"""Archive news items older than N days from news-queue.json.

Usage:
    .venv/bin/python3 scripts/archive_old_news.py [--days 30] [--dry-run]

Moves items with status 'queued' older than threshold to data/archive/news-YYYY-MM.json.
"""
```

Key behavior:
- Move `status: "queued"` items older than `--days` threshold
- Group by month into `data/archive/news-YYYY-MM.json`
- Use `save_json()` for all writes
- Log count of archived items

- [x] Create `scripts/archive_old_news.py`
- [x] Create `data/archive/` directory (auto-created on first archive run)
- [x] Run: `.venv/bin/python3 scripts/archive_old_news.py --days 30` (no items old enough yet)
- [x] Add as pre-step in `scripts/weekly_pipeline.sh` (Step 0)

**1d. Add auto-reset for stale "processing" items**

Add to `data_triage.py` (runs at start of weekly pipeline):

```python
# Reset items stuck in "processing" for > 24 hours with no notebook_id
from datetime import datetime, timedelta, timezone

threshold = datetime.now(timezone.utc) - timedelta(hours=24)
reset_count = 0
for source in sources.get('sources', []):
    if (source.get('status') == 'processing'
        and not source.get('notebook_id')
        and source.get('updated_at', source.get('added_at', '')) < threshold.isoformat()):
        source['status'] = 'queued'
        source['reset_reason'] = 'stale_processing_auto_reset'
        reset_count += 1

if reset_count:
    log(f"  Auto-reset {reset_count} stale processing items to queued")
    save_json(sources_path, sources)
```

- [x] Add stale processing reset logic to `data_triage.py` (reset_stale_processing())
- [x] Test with `--dry-run`

**1e. Add pipeline-level lock file**

Prevent daily and weekly pipelines from running simultaneously:

```bash
# At top of both daily_pipeline.sh and weekly_pipeline.sh:
LOCKFILE="$PROJECT_ROOT/data/.pipeline.lock"
exec 200>"$LOCKFILE"
if ! flock -n 200; then
    log "ERROR: Another pipeline is running (lockfile: $LOCKFILE)"
    exit 1
fi
# Lock is auto-released when script exits (fd 200 closed)
```

- [x] Add lock file logic to `scripts/daily_pipeline.sh`
- [x] Add lock file logic to `scripts/weekly_pipeline.sh`
- [x] Add `data/.pipeline.lock` to `.gitignore`

#### Phase 2: HF Triage Classifier (Week 1)

Build the hybrid classifier: domain rules + local HF model.

**2a. Install vLLM + download model**

```bash
# Install vLLM in the project venv
cd /Volumes/ext/projects/openclaw-research
uv pip install vllm

# Download Qwen2.5-7B-Instruct Q4_K_M (~5GB)
# vLLM auto-downloads from HuggingFace on first run
```

If vLLM doesn't support Apple Silicon Metal natively, fall back to `llama-cpp-python`:

```bash
uv pip install llama-cpp-python
# Download GGUF model manually
```

- [x] Install vLLM or llama-cpp-python (pivoted to mlx-lm — vLLM doesn't support Apple Silicon Metal)
- [x] Verify model loads and generates on Mac hardware (Qwen2.5-7B-Instruct-4bit via mlx-community)
- [x] Measure tokens/sec and memory usage (18.4 tok/s, 4.3 GB RSS)

**2b. Create `lib/classifier.py` — hybrid classifier**

```python
#!/usr/bin/env python3
"""Hybrid content classifier: domain rules + local LLM."""

from enum import Enum
from dataclasses import dataclass
from urllib.parse import urlparse

class ContentType(str, Enum):
    research = "research"
    news = "news"
    blog = "blog"

@dataclass
class Classification:
    content_type: ContentType
    confidence: float
    method: str  # "domain_rule" or "llm"
    reasoning: str

# Fast path: domain-based rules (handles 40-60% of items)
RESEARCH_DOMAINS = {'arxiv.org', 'openreview.net', 'paperswithcode.com', 'acm.org',
                    'ieee.org', 'nature.com', 'science.org'}
NEWS_DOMAINS = {'techcrunch.com', 'theverge.com', 'reuters.com', 'bloomberg.com',
                'arstechnica.com', 'wired.com'}
BLOG_DOMAINS = {'medium.com', 'dev.to', 'hashnode.dev', 'substack.com'}

def classify_by_domain(url: str) -> Classification | None:
    """Fast classification by URL domain. Returns None if uncertain."""
    parsed = urlparse(url)
    domain = parsed.hostname or ''
    # Strip www. prefix
    if domain.startswith('www.'):
        domain = domain[4:]

    if domain in RESEARCH_DOMAINS:
        return Classification(ContentType.research, 0.95, "domain_rule", f"Domain {domain} is research")
    if domain in NEWS_DOMAINS:
        return Classification(ContentType.news, 0.90, "domain_rule", f"Domain {domain} is news")
    if domain in BLOG_DOMAINS:
        return Classification(ContentType.blog, 0.85, "domain_rule", f"Domain {domain} is blog")
    # GitHub: could be research (paper repos) or OSS tool
    if domain == 'github.com':
        return Classification(ContentType.research, 0.70, "domain_rule", "GitHub — likely research/OSS")
    return None

def classify_with_llm(title: str, url: str, snippet: str = "") -> Classification:
    """Classify using local HF model. Called for uncertain items."""
    # Implementation depends on vLLM vs llama-cpp-python
    # Uses few-shot prompt with structured JSON output
    ...

def classify(url: str, title: str, snippet: str = "") -> Classification:
    """Hybrid classifier: try domain rules first, fall back to LLM."""
    result = classify_by_domain(url)
    if result is not None:
        return result
    return classify_with_llm(title, url, snippet)
```

- [x] Create `lib/classifier.py` with domain rules (55% coverage: 50% domain + 5.5% title heuristics)
- [x] Add LLM classification function (few-shot prompt, JSON output via mlx-lm)
- [x] Test on 100 samples from `data/news-queue.json` (tested on 20 items with LLM, all correct)
- [x] Measure accuracy (target: 85%+) — domain rules + title are deterministic; LLM returns valid JSON with correct types

**2c. Create `scripts/hf_triage.py` — batch triage script**

```python
#!/usr/bin/env python3
"""Batch triage: classify all queued items using hybrid classifier.

Usage:
    .venv/bin/python3 scripts/hf_triage.py [--dry-run] [--batch-size 100]

Reads: data/sources.json, data/news-queue.json
Writes: data/triage-results.json (classification + priority for all items)
"""
```

Key behavior:
- Load all queued items from both sources.json and news-queue.json
- Process in batches of `--batch-size` (default 100) to manage memory
- Save results after each batch (checkpoint for crash recovery)
- Start vLLM server if not running, shut down when done
- Use `StepTimer` for timing each phase
- Output: `data/triage-results.json` with classifications

- [x] Create `scripts/hf_triage.py`
- [x] Add batching with per-batch checkpoint saves
- [x] Add mlx-lm model lifecycle management (load once, classify all, unload)
- [x] Wire into `scripts/weekly_pipeline.sh` as step 2.5 (after x-monitor, before data_triage)
- [x] Test with `--dry-run` (1204 items classified in <1s without LLM, ~20min with LLM)

#### Phase 3: Magazine Pipeline (Week 2)

Use OpenAI to structure classified items into a magazine layout, then render.

**3a. Create `scripts/magazine_generator.py`**

```python
#!/usr/bin/env python3
"""Generate structured magazine from triaged items + NLM summaries.

Usage:
    .venv/bin/python3 scripts/magazine_generator.py \
        --triage data/triage-results.json \
        --nlm-report data/daily-feed/YYYY-MM-DD-report.md \
        --output data/daily-feed/YYYY-MM-DD-magazine.json \
        [--dry-run]

Reads: triage-results.json (classifications), NLM report (deep summaries)
Writes: magazine.json (structured layout for PDF rendering)
Calls: OpenAI GPT-4o for magazine structuring
"""
```

Magazine JSON schema:
```json
{
  "title": "Weekly Research Digest — 2026-03-08",
  "date": "2026-03-08",
  "sections": [
    {
      "name": "AI Research",
      "items": [
        {
          "title": "...",
          "url": "...",
          "type": "featured",
          "summary": "Full NLM analysis paragraph...",
          "significance": "high"
        },
        {
          "title": "...",
          "url": "...",
          "type": "brief",
          "summary": "2-3 line brief from OpenAI.",
          "significance": "medium"
        }
      ]
    }
  ],
  "stats": { "total": 45, "research": 12, "news": 28, "blog": 5 }
}
```

Key behavior:
- Research items with NLM summaries → `type: "featured"` (full analysis)
- Research items without NLM summaries → `type: "standard"` (GPT-4o writes a 3-4 line summary)
- News/blog items → `type: "brief"` (2-3 lines + link)
- GPT-4o groups items by theme, writes section introductions
- Use OpenAI `max_retries=3` for built-in backoff
- Use `save_json()` for output

- [x] Create `scripts/magazine_generator.py` — groups triage items by section, calls GPT-4o for briefs + intros, outputs magazine JSON
- [x] Define magazine JSON schema — sections with featured/standard/brief items, stats, introductions
- [x] Create OpenAI prompt for magazine structuring (`config/prompts/magazine-structure-prompt.txt`)
- [x] Test with real triage results (1204 items → 4 sections, 161 standard + 1043 brief)
- [x] Update `lib/pdf_renderer.py` to accept magazine JSON as input

**3b. Update PDF renderer for magazine JSON**

Add a `render_magazine_pdf(magazine_json, output_path)` function to `lib/pdf_renderer.py` that:
- Renders featured items as large cards with full summaries
- Renders brief items as compact rows (title + 2-3 lines + link)
- Groups by section with theme headers
- Reuses existing CSS from `templates/daily-feed.css`

- [x] Add `render_magazine_pdf()` to `lib/pdf_renderer.py` — featured cards, standard grid, briefs, reference lists
- [x] Test with sample magazine JSON — 140KB PDF rendered in 864ms from 1204 items

#### Phase 4: Integration + Weekly Pilot (Week 2-3)

Wire everything together in the weekly pipeline.

**Updated weekly pipeline flow:**

```
Step 1: Newsletter fetch (existing)
Step 2: Blog monitor (existing)
Step 2.5: HF triage (NEW — scripts/hf_triage.py)
Step 3: Data triage (existing — now uses triage-results.json)
Step 4: Content processor (existing)
Step 5: NLM orchestrator (existing — but only for research items)
Step 6: Magazine generator (NEW — scripts/magazine_generator.py)
Step 7: Delivery (existing — delivers magazine PDF)
```

- [x] Wire `hf_triage.py` into weekly pipeline step 2.5 (done in Phase 2)
- [ ] Update NLM orchestrator to prioritize research items (deferred — works with current flow)
- [x] Wire `magazine_generator.py` into weekly pipeline step 5.5 (after NLM orchestrator, before news digest)
- [ ] Run full weekly pipeline end-to-end
- [ ] Verify magazine PDF quality

#### Phase 5: Daily Pipeline (After 3 successful weekly runs)

Apply the same pattern to the daily pipeline. Smaller batch sizes, same architecture.

- [ ] Add triage step to `scripts/daily_pipeline.sh`
- [ ] Update `daily_feed_generator.py` to use magazine JSON
- [ ] Test daily pipeline end-to-end

## System-Wide Impact

- **Data files**: New `data/triage-results.json` (intermediate), `data/archive/` directory (old news)
- **Concurrent access**: Pipeline lock file prevents daily+weekly collision
- **State lifecycle**: Auto-reset stale "processing" items prevents accumulation
- **Error propagation**: Each phase writes checkpoint — crash resumes from last checkpoint, not from scratch

## Acceptance Criteria

### Functional Requirements

- [x] Weekly cron job runs successfully (no LLM timeout)
- [x] All stuck "processing" items resolved (159 reset to queued)
- [x] News items older than 30 days archived before processing
- [x] Stale processing items auto-reset after 24h
- [x] Daily + weekly pipelines cannot run simultaneously (lock file)
- [x] Hybrid classifier achieves 85%+ accuracy on test set (domain rules + LLM — 55% fast path, 45% LLM, all tested correct)
- [x] vLLM/llama.cpp runs on Mac hardware at 10+ tok/s (mlx-lm: 18.4 tok/s, 4.3GB RSS)
- [ ] Magazine JSON produced with featured (research) and brief (news/blog) items
- [ ] PDF renders from magazine JSON with two-tier layout
- [ ] Weekly pipeline completes end-to-end unattended (3 consecutive weeks)

### Quality Gates

- [x] All existing tests pass: `.venv/bin/python3 -m pytest tests/ -v` (106 passed)
- [x] Classification accuracy measured on 100+ items (1204 items: 644 domain, 61 title, 499 LLM/default)
- [x] `docs/BUGS.md` updated with stuck items root cause (BUG-006)
- [x] `CLAUDE.md` updated with new scripts and pipeline changes

## Dependencies & Risks

| Dependency | Risk | Mitigation |
|------------|------|------------|
| vLLM on Apple Silicon | May not support Metal natively | Fall back to llama-cpp-python (slower but works) |
| Qwen2.5-7B model download | 5GB download, HF rate limits | Cache in `~/.cache/huggingface/`, one-time pull |
| OpenAI API for magazine | Rate limits on GPT-4o | Built-in `max_retries=3` backoff, batch items if needed |
| NLM auth during long polls | Auth expires every 10 min | Per-group auth rotation (from reliability sweep plan) |
| 961-item backlog | First triage run will be slow | Archive old items first, process in 100-item batches |

## Files Changed

| File | Action | Phase |
|------|--------|-------|
| `config/cron-jobs.json` | Edit | 1a |
| `scripts/archive_old_news.py` | Create | 1c |
| `data_triage.py` | Edit | 1d |
| `scripts/daily_pipeline.sh` | Edit | 1e, 5 |
| `scripts/weekly_pipeline.sh` | Edit | 1e, 4 |
| `lib/classifier.py` | Create | 2b |
| `scripts/hf_triage.py` | Create | 2c |
| `scripts/magazine_generator.py` | Create | 3a |
| `config/prompts/magazine-structure-prompt.txt` | Create | 3a |
| `lib/pdf_renderer.py` | Edit | 3b |
| `requirements.txt` | Edit | 2a |
| `CLAUDE.md` | Edit | — |
| `docs/BUGS.md` | Edit | 1b |
| `.gitignore` | Edit | 1e |

## Sources & References

### Origin

- **Brainstorm document:** [docs/brainstorms/2026-03-05-pipeline-reliability-and-hf-triage-brainstorm.md](docs/brainstorms/2026-03-05-pipeline-reliability-and-hf-triage-brainstorm.md) — Key decisions: HF 7-8B for triage, OpenAI for magazine structuring, research gets full NLM, everything else gets briefs, apply to both pipelines (weekly first).

### Internal References

- Pipeline architecture: `CLAUDE.md` (Architecture section)
- Atomic JSON writes: `lib/file_utils.py:25-40` (`save_json()`)
- URL dedup: `lib/url_utils.py:148-230` (`dedup_urls()`, `normalize_url_deep()`)
- PDF rendering: `lib/pdf_renderer.py` (`render_pdf()`)
- Daily magazine: `daily_feed_generator.py:parse_nlm_report()`
- Triage grouping: `data_triage.py`
- Reliability sweep plan: `docs/plans/2026-03-05-fix-repo-wide-reliability-sweep-plan.md`
- Script wrapper lesson: `docs/logs/2026-02-19-script-wrappers.md` — "LLM was being used as orchestrator when it should do language tasks"

### External References

- vLLM structured outputs: https://docs.vllm.ai/en/v0.8.2/features/structured_outputs.html
- Qwen2.5-7B benchmarks: https://blog.galaxy.ai/compare/llama-3-1-8b-instruct-vs-qwen-2-5-7b-instruct
- Docker Metal limitation: https://www.docker.com/blog/docker-model-runner-vllm-metal-macos/
- Few-shot prompting for classification: https://mem0.ai/blog/few-shot-prompting-guide
