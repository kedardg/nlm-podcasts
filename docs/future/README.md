# Future

Planned features and improvements, organized by priority.

## High Priority

Issues that fix current pain points or unblock workflows.

- **NotebookLM auth resilience** — Cookie-based auth expires every ~20 minutes. Explore managed browser auth via OpenClaw CDP, or implement a retry/queue mechanism so pipelines survive auth failures gracefully.
- **Deterministic notebook dates** — LLM still generates date strings in some orchestrator paths. All notebook names should use `$(date +%Y-%m-%d)` or Python `datetime.now()`, never LLM-generated dates.

## Medium Priority

Leveraging unused OpenClaw platform features. See [openclaw-platform-features.md](openclaw-platform-features.md) for details.

- **YouTube channel monitoring** — Follow YouTube channels via RSS, auto-ingest new videos with per-channel category routing and domain-separated NLM notebooks/podcasts. See [youtube-channel-monitor.md](youtube-channel-monitor.md).
- **Sub-agents** for parallel pipeline execution
- **Heartbeat** for proactive monitoring
- **Webhooks** for event-driven newsletter processing
- **Model fallback chains** for resilience

## Low Priority

Nice-to-have extensions and new source types.

- **Rescript podcast transcripts** — Ingest AI researcher podcast transcripts from rescript.info as a new source type
- **Instagram reels** — Monitor and extract transcripts from learning content reels
- **Travel sources** — New category with passive storage and chat-based querying

## Planning Docs

| Doc | Description |
|-----|-------------|
| [openclaw-platform-features.md](openclaw-platform-features.md) | OpenClaw features we could leverage (sub-agents, heartbeat, webhooks, etc.) |
| [newsletter-email-integration-plan.md](newsletter-email-integration-plan.md) | Original planning doc for newsletter/email ingestion (now implemented) |
| [youtube-channel-monitor.md](youtube-channel-monitor.md) | YouTube channel monitoring — RSS feeds, domain separation, ClawHub/MCP options |
