# RAG System — Notebook-Routed Queries

## Architecture

The RAG system routes user questions to the most relevant NotebookLM notebooks, then queries those notebooks for deep, source-grounded answers.

```
User: "chat what papers discuss attention mechanisms?"
        │
        ▼
Step 1: ChromaDB semantic search (notebook_sources collection)
        │ → Returns: [{notebook_id: "abc", category: "ml", hit_count: 3, best_score: 0.89}]
        │
        ▼
Step 2: Group by notebook_id, pick top 2-3
        │
        ▼
Step 3: nlm notebook query <id> "what papers discuss attention mechanisms?"
        │ → Returns: grounded answer from original sources
        │
        ▼
Step 4: Merge responses → answer with source citations
```

## Two Collections

| Collection | Purpose | What Gets Indexed |
|---|---|---|
| `notebook_sources` | Notebook routing — which notebook has relevant content | Source metadata (title, URL, category, keywords) + companion note chunks |
| `research_reports` | Legacy direct search over report text | Full report text chunks |

The `notebook_sources` collection is the primary query path. `research_reports` is legacy and used as a fallback.

## Indexing Pipeline (`rag/embeddings.py`)

### Notebook Source Indexing

Called after each notebook is created (orchestrator Step 8.5 or via `scripts/index_notebook.py`).

**`index_notebook_sources()`** (embeddings.py:288-399):

For each source in the notebook:
1. Build an embeddable document:
   ```python
   # build_source_document() — line 269-285
   # Combines: title + URL + category name + keywords
   "Attention Is All You Need\nhttps://arxiv.org/abs/1706.03762\nCategory: Machine Learning\nKeywords: transformer neural network deep learning"
   ```
2. Metadata per document:
   ```json
   {
     "notebook_id": "abc123",
     "notebook_alias": "ml-2026-02-15",
     "category": "ml",
     "source_id": "src_001",
     "source_url": "https://arxiv.org/abs/...",
     "doc_type": "source",
     "created_at": "2026-02-15T09:30:00Z"
   }
   ```

For the companion note:
1. Chunk into ~500-token pieces with 50-token overlap
2. Each chunk gets `doc_type: "companion_note"` metadata
3. Companion notes contain themes, connections, and summaries — high signal for routing

**Embedding model**: OpenAI `text-embedding-3-small` (same key as LLM)

**Chunking** (embeddings.py:59-81):
```python
# ~500 tokens per chunk, ~50 token overlap
# Uses word-based approximation: 1 token ≈ 0.75 words
words_per_chunk = int(500 * 0.75)  # 375 words
words_overlap = int(50 * 0.75)     # 37 words
```

**Batched embedding**: Texts are embedded in batches of 100 via the OpenAI API.

**Storage**: ChromaDB persistent client at `rag/chroma_db/`, cosine similarity space.

### Report Indexing (Legacy)

```python
# index_report() — line 124-177
# Chunks full report text, embeds, stores in "research_reports" collection
```

Used for direct text search over report content. Less useful than notebook routing since it doesn't know which notebook a chunk belongs to.

## Query Pipeline (`rag/query.py`)

### Notebook Routing Mode (Default)

**`query_notebook_router()`** (query.py:129-232):

1. Embed the user's question using `text-embedding-3-small`
2. Query `notebook_sources` collection (k=10 for diversity)
3. Optional category filter: `{"category": "ml"}`
4. Group results by `notebook_id`:
   ```python
   # For each hit, accumulate per notebook:
   notebook_hits[nb_id] = {
       "hit_count": 3,        # Number of matching documents
       "best_score": 0.89,    # Highest cosine similarity
       "sources_matched": ["https://arxiv.org/..."],
   }
   ```
5. Rank notebooks: `hit_count desc, best_score desc, created_at desc`
6. Return top 3 notebooks

**Fallback** (query.py:235-268): If the `notebook_sources` collection is empty or doesn't exist, falls back to the 3 most recent notebooks from `notebooks.json`, optionally filtered by category.

### Report Mode (Legacy)

**`query_chromadb()`** (query.py:64-126): Direct cosine search over report text chunks. Returns matched text with score, category, and original URLs.

## Chat-RAG Skill Flow

The `chat-rag` skill orchestrates the full query pipeline:

1. **Parse command**: `chat ml: attention mechanisms` → category=`ml`, question=`attention mechanisms`
2. **Notebook routing**:
   ```bash
   .venv/bin/python3 rag/query.py "attention mechanisms" --mode notebook --category ml
   ```
3. **Auth check**: `scripts/nlm_ensure_auth.sh`
4. **Query top notebooks** (up to 3):
   ```bash
   ~/.local/bin/nlm notebook query <notebook-id> "attention mechanisms"
   ```
5. **Merge responses**: Synthesize answers from multiple notebooks, deduplicate source citations
6. **Format**: Answer + source links with notebook/date attribution

### Query Output Format

```json
{
  "query": "attention mechanisms",
  "mode": "notebook",
  "notebooks": [
    {
      "notebook_id": "abc123",
      "notebook_alias": "ml-2026-02-15",
      "category": "ml",
      "created_at": "2026-02-15T09:30:00Z",
      "hit_count": 3,
      "best_score": 0.89,
      "sources_matched": [
        "https://arxiv.org/abs/1706.03762",
        "https://arxiv.org/abs/2301.12345"
      ]
    }
  ],
  "fallback_used": false
}
```

## Indexing Commands

```bash
# Index a specific notebook's sources
.venv/bin/python3 scripts/index_notebook.py --notebook-id <id>

# Include companion note
.venv/bin/python3 scripts/index_notebook.py --notebook-id <id> --companion-note path/to/note.md

# Re-index all notebooks
.venv/bin/python3 scripts/index_notebook.py --all

# Index most recent notebook (default)
.venv/bin/python3 scripts/index_notebook.py
```

The `scripts/index_notebook.py` wrapper reads `notebooks.json` to get source_ids and metadata, then calls `rag/embeddings.py:index_notebook_sources()`.

## Query Commands

```bash
# Notebook routing (default) — finds relevant notebooks
.venv/bin/python3 rag/query.py "attention mechanisms" --mode notebook

# With category filter
.venv/bin/python3 rag/query.py "SLAM algorithms" --mode notebook --category robotics

# Legacy report search
.venv/bin/python3 rag/query.py "popular repos" --mode report --category github --top-k 10
```

## What Gets Indexed vs What Gets Queried

```
At notebook creation time (orchestrator Step 8.5):
    ┌──────────────────────────┐
    │ ChromaDB notebook_sources │
    ├──────────────────────────┤
    │ Source docs:              │
    │   title + URL + category  │    ← Per source in the notebook
    │   + keywords              │
    │                           │
    │ Companion note chunks:    │
    │   ~500-token chunks of    │    ← Themes, connections, summaries
    │   the companion note      │
    └──────────────────────────┘

At query time (chat-rag skill):
    ChromaDB → finds notebook_ids → nlm notebook query → grounded answer
```

The ChromaDB layer is a routing layer — it tells us WHICH notebooks to ask. The actual answering is done by NotebookLM's `notebook query` command, which has access to the full source content (papers, articles, PDFs).

## Storage

- ChromaDB data: `rag/chroma_db/` (auto-created, persists between runs)
- Embedding model: `text-embedding-3-small` via `OPENAI_API_KEY`
- If `rag/chroma_db/` doesn't exist, queries return empty/fallback results
