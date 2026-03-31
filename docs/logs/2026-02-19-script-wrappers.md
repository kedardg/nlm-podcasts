# 2026-02-19: Script Wrappers — Move Orchestration Out of the LLM

## Summary

Moved pipeline orchestration logic out of TOOLS.md and into deterministic scripts. GPT-4.1-mini was being asked to parse JSON, branch on conditions, loop over notebooks, and chain 6-8 sequential commands — tasks where mini models struggle. Now the LLM just runs one script per pipeline and relays the output.

## Problem

TOOLS.md was 209 lines with 3 complex pipelines the LLM had to execute:

1. **Chat/RAG Pipeline** (5 steps): Parse args for category prefix → ChromaDB query → parse JSON → NLM auth → loop over 2-3 notebooks querying each → synthesize answers. Too much conditional logic and looping for a mini model.

2. **Fetch Latest Pipeline** (5 steps + branching): Run fetch_latest.py → parse JSON → branch on category (daily=filesystem copy vs research/news=NLM download+enhance+PDF) → deliver via Telegram. The branching was the highest risk — mini models follow one linear path well but struggle with conditional branches.

3. **Process Pipeline** (8 steps): Skills are LLM-executed (the LLM reads SKILL.md), so this can't be fully scripted. Kept in TOOLS.md but already concise.

The core issue: the LLM was being used as an **orchestrator** (parse JSON, branch, loop, error-handle) when it should be used for **language tasks** (chat, synthesis, classification).

## Solution

Created two wrapper scripts that encapsulate all orchestration logic:

### `scripts/rag_chat.py`
- Takes a question (optionally `category: question` format)
- Runs: ChromaDB notebook search → NLM auth → query top 2-3 notebooks → format output
- Outputs plain text ready to relay to the user
- Handles: empty results, auth failures, no ChromaDB

### `scripts/fetch_and_deliver.sh`
- Takes `<category> <artifact>` arguments
- Runs: fetch_latest.py → branch on category → download/enhance/PDF → Telegram delivery → summary
- All branching is deterministic in bash (daily path vs research/news path)
- Handles: NLM auth, PDF fallback to .md, share links

### Simplified TOOLS.md
- **209 → 90 lines** (57% reduction)
- Routing table + 3 sections: Chat Script (1 command), Fetch Script (1 command), Process Pipeline (7 skill executions + 1 bash command)
- The LLM's decision surface: "run router → pick one of 3 actions → run one script → relay output"

## What the LLM still does (correctly)

| Role | Why LLM needed |
|------|----------------|
| Chat/Q&A synthesis | Polishing RAG answers into coherent replies |
| Companion notes | Writing thematic summaries (content-processor skill) |
| News briefings | Writing structured briefings (news-digest skill) |
| Classification | Categorizing ambiguous sources (content-processor skill) |
| Conversational interface | Understanding natural language from Telegram |

## What the LLM no longer does

| Removed task | Now handled by |
|-------------|----------------|
| JSON parsing from fetch_latest.py | `fetch_and_deliver.sh` |
| Category branching (daily vs research) | `fetch_and_deliver.sh` |
| 6-command download→enhance→PDF→deliver chain | `fetch_and_deliver.sh` |
| ChromaDB→auth→notebook query loop | `rag_chat.py` |
| NLM auth check + error messaging | Both scripts |

## Changes Made

- `scripts/rag_chat.py` — new: complete Chat/RAG pipeline script
- `scripts/fetch_and_deliver.sh` — new: complete Fetch/Deliver pipeline script
- `~/.openclaw/workspace/TOOLS.md` — rewritten: 209→90 lines, thin dispatcher
- `CLAUDE.md` — added rag_chat.py and fetch_and_deliver.sh to components section

## Decisions & Rationale

- **Python for RAG chat, bash for fetch/deliver**: RAG chat needs JSON parsing of ChromaDB output (cleaner in Python). Fetch/deliver is mostly CLI commands (cleaner in bash).
- **Process pipeline stays in TOOLS.md**: OpenClaw skills are LLM-executed (the LLM reads SKILL.md files). Can't script skill execution — it's fundamentally an LLM task. But the process pipeline is already just a numbered list, which mini models handle fine.
- **"Relay output" pattern**: Scripts produce human-readable text on stdout. The LLM just sends it to the user. This minimizes LLM decision-making while keeping the conversational interface natural.
- **rag_chat.py outputs formatted text, not JSON**: The mini model doesn't need to parse JSON and format an answer. The script does both. The LLM can optionally enrich thin answers.

## Follow-up

- Monitor whether GPT-4.1-mini handles the simplified TOOLS.md reliably
- If process pipeline (skill execution) is also too complex, consider breaking skills into smaller, simpler SKILL.md files
- Consider moving the daily-digest cron into a standalone script too (it's currently a skill)
