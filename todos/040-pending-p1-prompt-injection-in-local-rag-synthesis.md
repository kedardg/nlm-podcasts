---
status: pending
priority: p1
issue_id: "040"
tags: [code-review, security, rag]
dependencies: ["031"]
---

# Local RAG synthesis is a new prompt-injection surface the plan never mentions

## Problem Statement

Replacing `nlm notebook query` with local retrieval + GPT-4o synthesis over scraped page text is a step change in exposure. Today NotebookLM's own grounded model does the synthesis; afterwards, attacker-influenced text is fed to GPT-4o and the answer is relayed verbatim to Telegram.

## Findings

- Today's untrusted-content exposure is thin: `src/pipeline/magazine_generator.py:285-300` passes only `{id, title, url, content_type}`.
- Phase 2 deletes `query_notebook()` (`src/pipeline/rag_chat.py:74-91`) and feeds full `nlm source content` text — which the plan's own findings note carries nav chrome (`"Skip to content\n\nNavigation Menu\n\nSign in…"`).
- The content is attacker-controlled in practice: anyone who can get a URL into a monitored newsletter, an RSS feed in `data/feeds.json`, or a tracked X account can plant instructions that will be captured, indexed, retrieved, and synthesized.
- The result is relayed by the same `tg_send_message` that carries the `curl -F` file-read primitive (see todo 041).
- `archive_sources` becomes a permanent injection store — retrievable forever, including from notebooks already deleted upstream and therefore no longer inspectable.

## Proposed Solutions

1. **Controls land with Phase 2, not after (recommended).**
   - Retrieved passages go in a **`user`** message inside explicit `<<<SOURCE id=… >>>` delimiters, never in `system`. System prompt states delimited content is untrusted data and that instructions found inside must be reported, never followed.
   - Settle Open Question 2 as **yes, trim**: run `trafilatura` (already a dependency) at capture time; drop `<script>`, HTML comments, hidden text; normalize zero-width and bidi controls. (The repo already carries `\x02` in notebook titles — same class.)
   - Cap per-passage and total context so one source cannot dominate.
   - No tools/function-calling on the synthesis call; bounded `max_tokens`.
   - Never interpolate model output into a shell command, file path, or `-F` value.
   - Every claim carries its `source_id` so an injected instruction is attributable.
   - Store `content_sha256` + capture timestamp per chunk and a `quarantine` flag; pre-index scan for `ignore previous`, `system:`, `you are an AI`, long base64 runs → `needs_human` instead of the index.

## Acceptance Criteria

- [ ] Retrieved content never appears in a `system` message
- [ ] `trafilatura` trimming applied at capture time
- [ ] No tool-calling on the synthesis call; `max_tokens` bounded
- [ ] Quarantine scanner runs pre-index
- [ ] Test: a source containing "ignore previous instructions" does not change the answer's behaviour
