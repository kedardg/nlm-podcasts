# OpenClaw Platform Features to Leverage

Features available in OpenClaw that we're not yet using (or underusing). Each is an actionable improvement opportunity.

> **Legend**: [DONE] = implemented, [FUTURE] = not yet implemented

## [FUTURE] Sub-Agents (`sessions_spawn`)

**What**: Spawn up to 8 concurrent sub-agent sessions that run in parallel and report results back.

**Use case**: The weekly pipeline runs sequentially (newsletter-monitor → x-monitor → triage → content-processor → orchestrator → news-digest → deliver). Several steps are independent and could run in parallel:
- newsletter-monitor + x-monitor (both gather sources independently)
- Per-category notebook creation in the orchestrator (each category is independent)

**Effort**: Medium — requires restructuring SKILL.md to use `sessions_spawn` instead of sequential steps.

## [DONE] Heartbeat / Thinking Clock

**What**: A periodic agent turn (every 30-60 minutes) where the agent proactively checks for events and can take action.

**Use case**:
- Monitor `data/sources.json` for new high-priority items and alert via Telegram
- Check NotebookLM generation status and notify when podcasts are ready
- Proactive "you have 12 unprocessed sources" reminders

**Configuration**: Edit `~/.openclaw/workspace/HEARTBEAT.md` with the desired behavior.

**Effort**: Low — just write the HEARTBEAT.md instructions. The platform handles scheduling.

**Status**: Implemented — `HEARTBEAT.md` configured with 4 research pipeline checks (source queue depth, NLM auth, daily feed gap, recent errors).

## [FUTURE] Webhooks

**What**: External services can POST to `http://localhost:18789/hooks/<name>` to trigger agent turns.

**Use case**: Replace cron-based newsletter polling with event-driven processing:
1. Set up a Gmail push notification (via Google Pub/Sub or a simple forwarding rule)
2. Forward notification to OpenClaw webhook
3. Agent immediately processes the new newsletter

This would reduce the delay between newsletter arrival and processing from hours (next cron) to seconds.

**Effort**: Medium — requires webhook setup + Gmail push notification configuration.

## [DONE] Model Fallback Chains

**What**: Configure fallback models so if the primary model is unavailable, the agent automatically switches.

**Use case**: If OpenAI has an outage during the Saturday 9 AM cron, the pipeline would fail entirely. With a fallback chain, the pipeline continues with a fallback model.

```bash
openclaw models fallbacks add openai/gpt-4o-mini
```

**Effort**: Low — single CLI command.

**Status**: Implemented — GPT-4o-mini configured as fallback. Stays on OpenAI (no cross-provider fallback).

## [FUTURE] Built-in Web Search

**What**: The agent has access to `web_search` tool (Brave, Tavily, Perplexity, Exa backends) for real-time web discovery.

**Use case**: Augment research discovery beyond X scraping and newsletters:
- When a new paper is found, search for related work and discussions
- Find blog posts and explainers for dense papers
- Discover trending repos not yet on anyone's radar

**Configuration**:
```bash
openclaw configure --section web
```

**Effort**: Low to enable, medium to integrate meaningfully into the pipeline.

## [FUTURE] Memory System

**What**: Persistent key-value memory that survives across sessions. The agent can store and retrieve facts.

**Use case**:
- Remember user preferences ("I'm most interested in robotics and NLP")
- Track which newsletters consistently produce high-quality content
- Store common troubleshooting fixes so the agent can self-diagnose

**Effort**: Low — the platform provides the primitives. Requires writing instructions in workspace files.

## [DONE] SHIELD.md — Safety Guardrails

**What**: A workspace file that defines safety boundaries for the agent's actions.

**Use case**: Prevent the agent from:
- Deleting data files without confirmation
- Running destructive commands (`rm -rf`, overwriting notebooks.json)
- Spending more than N API calls per session

**Effort**: Low — write the SHIELD.md file with rules.

**Status**: Implemented — `SHIELD.md` created with rules for data protection, destructive commands, Telegram targeting, and 50-call NLM API limit per session.

## [FUTURE] Context Compaction

**What**: Automatic context window management. When a session approaches the token limit, older messages are summarized to free space.

**Current state**: Already enabled by default, but we rely on `/reset` for manual clearing.

**Improvement**: Configure compaction behavior to preserve critical context (like pipeline state) while dropping verbose tool output. This would make long debugging sessions more reliable.

**Effort**: Low — configuration in openclaw.json.
