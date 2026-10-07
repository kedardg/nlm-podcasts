---
status: complete
priority: p1
issue_id: "031"
tags: [code-review, architecture, rag, sequencing]
dependencies: []
---

# `/chat` is already broken — Phase 2's premise, and the plan's sequencing, are wrong

## Problem Statement

The plan sequences the local-RAG rewrite (Phase 2) *before* the reclaim (Phase 3) on the grounds that deleting notebooks would break `/chat`. Verified: **`/chat` is already broken and has been since the `refactor/repo-structure` move.** Deletion cannot regress it.

## Findings

- `src/pipeline/rag_chat.py:27` — `QUERY_SCRIPT = PROJECT_ROOT / "rag" / "query.py"`. Verified: that path does **not** exist. Root `rag/` contains only `__pycache__/` and `chroma_db/`. The query script lives at `src/rag/query.py`.
- Consequence: `chromadb_search()` (`:44-60`) always takes the exception/non-zero branch and returns `{"notebooks": [], "fallback_used": True}`, so `rag_chat.py` always prints "I don't have any indexed notebooks on this topic yet." `query_notebook()` at `:74-91` is never reached.
- `skills/chat-rag/SKILL.md:43` uses the correct path, so the LLM surface still works. The two surfaces have silently diverged; the plan treats them as one.
- Independently verified: ChromaDB holds **56 distinct `notebook_id`s**; the allowlist is 465. So **409 of 465 deletable notebooks are unroutable anyway** — even a working `/chat` could not reach them.

## Impact

Phase 2's gate ("`/chat` answers correctly with NLM auth deliberately broken") and risk R5 ("`/chat` quality drops vs NotebookLM synthesis") are both written against dead code. Weeks of work are sequenced ahead of the only urgent item, protecting a capability that does not currently exist.

## Proposed Solutions

1. **Resequence (recommended).** Move reclaim to Phase 1. Ship allowlist → reclaim → daily delete step first; production is live the same day. Local RAG becomes its own plan. Fix `rag_chat.py:27` now (one line) and capture a real before-baseline so R5's comparison is meaningful.
2. Keep the order, accept the delay. No protective benefit — the thing being protected is broken.

## Acceptance Criteria

- [ ] `rag_chat.py:27` points at `src/rag/query.py`
- [ ] A before-baseline of `/chat` behaviour is recorded prior to any deletion
- [ ] Reclaim is sequenced before the local-RAG rewrite
- [ ] Plan's R5 and Phase 2 gate rewritten against actual current behaviour
