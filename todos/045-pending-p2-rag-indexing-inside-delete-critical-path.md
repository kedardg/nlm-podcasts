---
status: pending
priority: p2
issue_id: "045"
tags: [code-review, architecture, rag]
dependencies: []
---

# RAG indexing sits inside the delete-authorizing critical path

## Problem Statement

The planned flow is `SQLite txn → archive_index → ChromaDB write → verify() → delete`. Cap relief then depends on the OpenAI embeddings API being up, and a successful Chroma write followed by a failed verify leaves `archive_sources` rows describing files the gate just rejected. The schema's own `sources.indexed_at` column implies the opposite design.

## Findings

- Plan architecture diagram, capture sequence.
- `sources.indexed_at` exists in the ERD — designed for deferred indexing.
- The AC "stale rows purged per-notebook immediately before re-index" makes purge-then-add non-atomic: if the purge lands and the re-index fails, RAG coverage silently shrinks *and the notebook is then deleted* (Chroma is not one of the 8 conditions).
- `src/rag/archive_index.py` would duplicate client setup, chunking and batch embedding already at `src/rag/embeddings.py:106-190, 301-400`.
- Three collections, one reader: the plan keeps daily Step 10.5 (`daily_pipeline.sh:774-783`) writing `report_summary` rows into a collection `--mode archive` will stop querying.

## Proposed Solutions

1. **Invert (recommended).** `capture → SQLite → verify → delete → index`, driven off `WHERE indexed_at IS NULL` as a separate idempotent, backfillable step. Add new rows under new ids, then purge old ids — never purge first. Extract the embeddings plumbing once and make `archive_index` a document builder. Fold `report_summary` into `archive_sources` behind a `doc_type` discriminator and retire the other two collections explicitly.

## Acceptance Criteria

- [ ] Indexing runs after delete, off `indexed_at IS NULL`
- [ ] Add-then-purge ordering, never purge-then-add
- [ ] No duplicated embedding plumbing
- [ ] Collection retirement stated explicitly
