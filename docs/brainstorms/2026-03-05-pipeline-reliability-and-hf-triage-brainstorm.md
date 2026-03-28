---
topic: Pipeline Reliability + HF-Powered Triage Pipeline
date: 2026-03-05
status: complete
participants: [kedar, claude]
---

# Pipeline Reliability + HF-Powered Triage Pipeline

## Context

Two connected problems surfaced on 2026-03-05:

1. **Weekly pipeline keeps failing** — the cron job message tells the OpenClaw LLM agent to "Execute newsletter-monitor skill" instead of running `bash weekly_pipeline.sh` directly. Both `gpt-5-mini` and `gpt-4.1-mini` timeout at 32s trying to interpret it. The daily pipeline works because it says "Execute: bash .../daily_pipeline.sh".

2. **Data backlog is growing** — because the weekly pipeline never completes, triage/dedup/processing never runs. Current state: 961 queued news articles, 159 sources stuck in "processing" since Feb 18 with no notebook_id, 157 duplicates + 129 skipped per daily run.

These led to a broader discussion about pipeline architecture.

## What We're Building

### Part 1: Immediate Fixes

- **Fix weekly cron message** to match the working daily pattern (direct bash command)
- **Investigate** the 159 stuck "processing" items (check `processing-log.json` for the Feb 18 run)
- **Archive** news items older than 30 days, process only recent ones
- **Add safeguards** so items don't get stuck again (auto-reset stale "processing" items)

### Part 2: HF-Powered Magazine Pipeline

A new pipeline architecture for both daily and weekly digests:

```
Items arrive (newsletters, blogs, X/Twitter)
       |
       v
[1] HF Local Model (7-8B, dockerized)
    - Classify: research vs news/blog
    - Prioritize by significance
    - Output: triage JSON with categories + priority scores
       |
       v
[2] NLM (NotebookLM)
    - Deep-dive key research items only
    - Create notebook with top links
    - Generate comprehensive summaries + podcast
       |
       v
[3] OpenAI API (GPT-4o)
    - Takes: NLM summaries + all items with triage scores
    - Creates: structured magazine JSON
    - Research items → full NLM analysis + links
    - Everything else → 2-3 line briefs + links
       |
       v
[4] PDF Renderer
    - Renders magazine from structured JSON
    - Detailed cards for research, brief entries for news
    - Every item has its source links
```

**Key principle**: This is a research bot. Research papers and technical content get the full NLM deep-dive treatment. News, blog posts, and announcements get concise briefs with links. The magazine structure makes both tiers useful.

## Why This Approach

### HF Local Model for Triage (not OpenAI)

- **Cost**: Triage runs on every item — using GPT-4o for classification would be expensive at scale (1000+ items/week)
- **Latency**: Local model avoids API roundtrips and timeouts (the exact problem that broke the weekly pipeline)
- **Privacy**: Source URLs and titles stay local during triage
- **Dockerized**: Start container on demand, shut down after — no always-on cost
- **7-8B is sufficient**: Classification and prioritization don't need frontier model reasoning

### OpenAI for Magazine Structuring (not local model)

- **Quality**: Magazine layout requires understanding themes, grouping related items, writing coherent briefs — GPT-4o excels here
- **Context**: GPT-4o's 128K context handles NLM output + all items comfortably
- **Existing integration**: OpenAI API key already configured, `openai` SDK already installed

### NLM for Deep Research (unchanged)

- **Grounding**: NLM's strength is synthesizing multiple sources with citations — perfect for research papers
- **Podcast**: NLM's audio generation is unique and valuable
- **Selective**: Only research items go to NLM (not all 1000+ items) — keeps notebooks focused

## Key Decisions

1. **HF model: 7-8B params** — Llama-3.1-8B or Mistral-7B, dockerized, start on demand
2. **Triage is local, structuring is OpenAI** — cost/quality tradeoff
3. **Research items get full NLM treatment** — deep summaries, citations, podcast
4. **Everything else gets 2-3 line briefs + links** — concise, useful, not noise
5. **Apply to both daily + weekly** — start with weekly as pilot
6. **Fix the cron message immediately** — 2-minute fix, don't block on the bigger work
7. **Archive old items** — news older than 30 days gets archived before processing

## Resolved Questions

### Q: What about the 159 stuck "processing" items?
**A**: Investigate first — check `processing-log.json` and gateway logs for the Feb 18 run to understand why they got stuck. Then reset or fix based on findings.

### Q: Docker or always-on for the HF model?
**A**: Dockerized, start container when needed. No always-on cost. The pipeline runs once daily/weekly — the container lifecycle matches that pattern.

### Q: Does this replace `daily_feed_generator.py`?
**A**: Eventually yes — the new magazine pipeline replaces the current NLM report → magazine flow. But the existing pipeline keeps working until the new one is proven.

## Open Questions

None — all questions resolved during brainstorm.

## Out of Scope

- Full OpenAI replacement (RAG embeddings, chat, interactive queries stay on OpenAI)
- Ollama/LM Studio exploration (Docker + HF is the chosen path)
- Podcastfy local podcast generation (separate project, see `docs/guide/podcastfy-evaluation.md`)
