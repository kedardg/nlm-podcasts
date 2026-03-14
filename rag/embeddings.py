#!/usr/bin/env python3
"""
RAG Embeddings Indexer for OpenClaw Research Automation System.

Two indexing modes:
1. Report indexing: Chunks full report text into ChromaDB "research_reports" collection.
2. Notebook source indexing: Indexes source metadata + companion notes into
   "notebook_sources" collection for notebook-routed RAG queries.

Usage:
    # Report indexing (legacy)
    python embeddings.py --report-dir <path> --run-id <run-id>
    python embeddings.py --report-file <path> --run-id <run-id>

    # Notebook source indexing
    python embeddings.py --notebook-id <id> --notebook-alias <alias> --category <slug> --source-ids <csv>
    python embeddings.py --notebook-id <id> --notebook-alias <alias> --category <slug> --source-ids <csv> --companion-note <path>
"""

import argparse
import json
import os
import sys
from pathlib import Path

import chromadb
from openai import OpenAI

try:
    import llm_tracer as _llm_tracer
    _llm_tracer.init(app="openclaw-research")
    _LLM_TRACER = True
except ImportError:
    _LLM_TRACER = False


class _NullCtx:
    def __enter__(self): return self
    def __exit__(self, *_): pass

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
CHROMA_DB_PATH = Path(__file__).parent / "chroma_db"
SOURCES_FILE = PROJECT_ROOT / "data" / "sources.json"
NOTEBOOKS_FILE = PROJECT_ROOT / "data" / "notebooks.json"
CATEGORIES_FILE = PROJECT_ROOT / "data" / "categories.json"

# Embedding config
EMBEDDING_MODEL = "text-embedding-3-small"
CHUNK_SIZE = 500  # approximate token count per chunk
CHUNK_OVERLAP = 50  # overlap between chunks in tokens
COLLECTION_NAME = "research_reports"
NOTEBOOK_SOURCES_COLLECTION = "notebook_sources"


def get_openai_client():
    """Initialize OpenAI client using environment variable."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY environment variable not set.", file=sys.stderr)
        sys.exit(1)
    return OpenAI(api_key=api_key)


def get_chroma_client():
    """Initialize persistent ChromaDB client."""
    CHROMA_DB_PATH.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(CHROMA_DB_PATH))


def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Split text into overlapping chunks of approximately chunk_size tokens.

    Uses a simple word-based approximation (1 token ≈ 0.75 words).
    """
    words = text.split()
    words_per_chunk = int(chunk_size * 0.75)
    words_overlap = int(overlap * 0.75)

    if len(words) <= words_per_chunk:
        return [text]

    chunks = []
    start = 0
    while start < len(words):
        end = start + words_per_chunk
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        start = end - words_overlap
        if start >= len(words):
            break

    return chunks


def embed_texts(client, texts):
    """Embed a list of texts using OpenAI embeddings API."""
    with (_llm_tracer.stage("rag_index") if _LLM_TRACER else _NullCtx()):
        response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=texts,
        )
    return [item.embedding for item in response.data]


def load_notebook_metadata(notebook_id):
    """Load metadata for a notebook from the registry."""
    if not NOTEBOOKS_FILE.exists():
        return {}

    with open(NOTEBOOKS_FILE) as f:
        data = json.load(f)

    for nb in data.get("notebooks", []):
        if nb["id"] == notebook_id:
            return nb

    return {}


def load_source_urls(source_ids):
    """Load original URLs for a list of source IDs."""
    if not SOURCES_FILE.exists():
        return []

    with open(SOURCES_FILE) as f:
        data = json.load(f)

    urls = []
    for src in data.get("sources", []):
        if src["id"] in source_ids and src.get("url"):
            urls.append(src["url"])

    return urls


def index_report(chroma_client, openai_client, report_text, metadata):
    """Index a single report into ChromaDB.

    Args:
        chroma_client: ChromaDB client
        openai_client: OpenAI client
        report_text: Full text of the report
        metadata: Dict with keys: notebook_id, category, run_id, source_ids, generated_date
    """
    collection = chroma_client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    chunks = chunk_text(report_text)
    if not chunks:
        print("No content to index.", file=sys.stderr)
        return 0

    # Prepare metadata for each chunk
    original_urls = load_source_urls(metadata.get("source_ids", []))
    chunk_metadatas = []
    chunk_ids = []
    for i, _chunk in enumerate(chunks):
        chunk_id = f"{metadata.get('notebook_id', 'unknown')}_{metadata.get('run_id', 'unknown')}_{i}"
        chunk_metadatas.append({
            "notebook_id": metadata.get("notebook_id", ""),
            "category": metadata.get("category", ""),
            "run_id": metadata.get("run_id", ""),
            "generated_date": metadata.get("generated_date", ""),
            "original_urls": json.dumps(original_urls),
            "chunk_index": i,
            "total_chunks": len(chunks),
        })
        chunk_ids.append(chunk_id)

    # Embed chunks in batches of 100
    all_embeddings = []
    batch_size = 100
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        embeddings = embed_texts(openai_client, batch)
        all_embeddings.extend(embeddings)

    # Upsert into ChromaDB
    collection.upsert(
        ids=chunk_ids,
        embeddings=all_embeddings,
        documents=chunks,
        metadatas=chunk_metadatas,
    )

    print(f"Indexed {len(chunks)} chunks for notebook {metadata.get('notebook_id', 'unknown')}")
    return len(chunks)


def index_report_directory(report_dir, run_id):
    """Index all report files in a directory.

    Looks for files matching *-report.md pattern.
    """
    openai_client = get_openai_client()
    chroma_client = get_chroma_client()

    report_dir = Path(report_dir)
    report_files = list(report_dir.glob("*-report.md"))

    if not report_files:
        print(f"No report files found in {report_dir}")
        return

    total_chunks = 0
    for report_file in report_files:
        print(f"Indexing: {report_file.name}")

        report_text = report_file.read_text()
        if not report_text.strip():
            print(f"  Skipping empty file: {report_file.name}")
            continue

        # Extract category from filename (e.g., "ml-2026-02-15-report.md" -> "ml")
        parts = report_file.stem.split("-")
        category = parts[0] if parts else "unknown"

        # Try to find notebook metadata
        notebook_meta = {}
        if NOTEBOOKS_FILE.exists():
            with open(NOTEBOOKS_FILE) as f:
                nb_data = json.load(f)
            for nb in nb_data.get("notebooks", []):
                if nb.get("category") == category:
                    notebook_meta = nb
                    break

        metadata = {
            "notebook_id": notebook_meta.get("id", report_file.stem),
            "category": category,
            "run_id": run_id,
            "source_ids": notebook_meta.get("source_ids", []),
            "generated_date": notebook_meta.get("created_at", ""),
        }

        chunks = index_report(chroma_client, openai_client, report_text, metadata)
        total_chunks += chunks

    print(f"\nIndexing complete. Total chunks indexed: {total_chunks}")


def index_single_report(report_file, run_id):
    """Index a single report file."""
    openai_client = get_openai_client()
    chroma_client = get_chroma_client()

    report_file = Path(report_file)
    if not report_file.exists():
        print(f"File not found: {report_file}", file=sys.stderr)
        sys.exit(1)

    report_text = report_file.read_text()
    parts = report_file.stem.split("-")
    category = parts[0] if parts else "unknown"

    metadata = {
        "notebook_id": report_file.stem,
        "category": category,
        "run_id": run_id,
        "source_ids": [],
        "generated_date": "",
    }

    index_report(chroma_client, openai_client, report_text, metadata)


## --- Notebook Source Indexing (notebook-routed RAG) --- ##


def load_categories():
    """Load categories from categories.json, keyed by slug."""
    if not CATEGORIES_FILE.exists():
        return {}
    with open(CATEGORIES_FILE) as f:
        data = json.load(f)
    return {cat["slug"]: cat for cat in data.get("categories", [])}


def build_source_document(source, category_info):
    """Build embeddable text from a source's metadata.

    Combines title, URL, category name, and keywords into a single
    text string optimized for semantic search.
    """
    parts = []
    if source.get("title"):
        parts.append(source["title"])
    if source.get("url"):
        parts.append(source["url"])
    if category_info:
        parts.append(f"Category: {category_info.get('name', '')}")
        keywords = category_info.get("keywords", [])
        if keywords:
            parts.append(f"Keywords: {' '.join(keywords)}")
    return "\n".join(parts)


def index_notebook_sources(
    notebook_id,
    notebook_alias,
    category_slug,
    source_ids,
    companion_note_text=None,
    created_at=None,
):
    """Index a notebook's source metadata and companion note into ChromaDB.

    Creates documents in the 'notebook_sources' collection so that
    query.py can route questions to the right notebooks.

    Args:
        notebook_id: The NotebookLM notebook ID
        notebook_alias: Human-readable alias (e.g., "ml-2026-02-15")
        category_slug: Category slug (e.g., "ml")
        source_ids: List of source IDs from sources.json
        companion_note_text: Optional companion note markdown text
        created_at: ISO 8601 timestamp of notebook creation
    """
    openai_client = get_openai_client()
    chroma_client = get_chroma_client()

    collection = chroma_client.get_or_create_collection(
        name=NOTEBOOK_SOURCES_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )

    categories = load_categories()
    category_info = categories.get(category_slug, {})

    # Load sources
    if not SOURCES_FILE.exists():
        print(f"Warning: {SOURCES_FILE} not found", file=sys.stderr)
        return 0

    with open(SOURCES_FILE) as f:
        sources_data = json.load(f)

    source_map = {s["id"]: s for s in sources_data.get("sources", [])}

    documents = []
    metadatas = []
    ids = []

    # Build per-source documents
    for sid in source_ids:
        source = source_map.get(sid)
        if not source:
            continue

        doc_text = build_source_document(source, category_info)
        if not doc_text.strip():
            continue

        documents.append(doc_text)
        metadatas.append({
            "notebook_id": notebook_id,
            "notebook_alias": notebook_alias,
            "category": category_slug,
            "source_id": sid,
            "source_url": source.get("url", ""),
            "doc_type": "source",
            "created_at": created_at or "",
        })
        ids.append(f"{notebook_id}_src_{sid}")

    # Chunk and index companion note if provided
    if companion_note_text and companion_note_text.strip():
        note_chunks = chunk_text(companion_note_text)
        for i, chunk in enumerate(note_chunks):
            documents.append(chunk)
            metadatas.append({
                "notebook_id": notebook_id,
                "notebook_alias": notebook_alias,
                "category": category_slug,
                "source_id": "",
                "source_url": "",
                "doc_type": "companion_note",
                "chunk_index": i,
                "created_at": created_at or "",
            })
            ids.append(f"{notebook_id}_companion_{i}")

    if not documents:
        print("No documents to index.", file=sys.stderr)
        return 0

    # Embed in batches
    all_embeddings = []
    batch_size = 100
    for i in range(0, len(documents), batch_size):
        batch = documents[i : i + batch_size]
        embeddings = embed_texts(openai_client, batch)
        all_embeddings.extend(embeddings)

    # Upsert into ChromaDB
    collection.upsert(
        ids=ids,
        embeddings=all_embeddings,
        documents=documents,
        metadatas=metadatas,
    )

    source_count = sum(1 for m in metadatas if m["doc_type"] == "source")
    note_count = sum(1 for m in metadatas if m["doc_type"] == "companion_note")
    print(
        f"Indexed {source_count} sources + {note_count} companion note chunks "
        f"for notebook {notebook_id} ({notebook_alias})"
    )
    return len(documents)


def index_notebook_summary(
    notebook_id,
    notebook_alias,
    category_slug,
    summary_text,
    created_at=None,
):
    """Index a notebook's report TL;DR summary into ChromaDB.

    Creates a single 'report_summary' document in the notebook_sources collection,
    giving the notebook a rich semantic description for routing queries.

    Args:
        notebook_id: The NotebookLM notebook ID
        notebook_alias: Human-readable alias (e.g., "ml-2026-02-15")
        category_slug: Category slug (e.g., "ml")
        summary_text: The TL;DR or summary text to index
        created_at: ISO 8601 timestamp of notebook creation
    """
    if not summary_text or not summary_text.strip():
        print("No summary text to index.", file=sys.stderr)
        return 0

    openai_client = get_openai_client()
    chroma_client = get_chroma_client()

    collection = chroma_client.get_or_create_collection(
        name=NOTEBOOK_SOURCES_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )

    doc_id = f"{notebook_id}_summary"
    metadata = {
        "notebook_id": notebook_id,
        "notebook_alias": notebook_alias,
        "category": category_slug,
        "source_id": "",
        "source_url": "",
        "doc_type": "report_summary",
        "created_at": created_at or "",
    }

    embeddings = embed_texts(openai_client, [summary_text])

    collection.upsert(
        ids=[doc_id],
        embeddings=embeddings,
        documents=[summary_text],
        metadatas=[metadata],
    )

    print(f"Indexed report summary for notebook {notebook_id} ({notebook_alias})")
    return 1


def main():
    parser = argparse.ArgumentParser(description="Index research reports or notebook sources into ChromaDB")

    # Report indexing args (legacy)
    parser.add_argument("--report-dir", help="Directory containing report markdown files")
    parser.add_argument("--report-file", help="Single report file to index")
    parser.add_argument("--run-id", help="Processing run ID (required for report indexing)")

    # Notebook source indexing args
    parser.add_argument("--notebook-id", help="Notebook ID to index sources for")
    parser.add_argument("--notebook-alias", help="Notebook alias (e.g., ml-2026-02-15)")
    parser.add_argument("--category", help="Category slug (e.g., ml)")
    parser.add_argument("--source-ids", help="Comma-separated source IDs")
    parser.add_argument("--companion-note", help="Path to companion note markdown file")

    args = parser.parse_args()

    # Notebook source indexing mode
    if args.notebook_id:
        if not args.notebook_alias or not args.category or not args.source_ids:
            parser.error("--notebook-id requires --notebook-alias, --category, and --source-ids")

        source_id_list = [s.strip() for s in args.source_ids.split(",") if s.strip()]

        companion_text = None
        if args.companion_note:
            note_path = Path(args.companion_note)
            if note_path.exists():
                companion_text = note_path.read_text()
            else:
                print(f"Warning: companion note not found: {args.companion_note}", file=sys.stderr)

        index_notebook_sources(
            notebook_id=args.notebook_id,
            notebook_alias=args.notebook_alias,
            category_slug=args.category,
            source_ids=source_id_list,
            companion_note_text=companion_text,
        )
        return

    # Report indexing mode (legacy)
    if not args.report_dir and not args.report_file:
        parser.error("Either --report-dir/--report-file or --notebook-id is required")

    if not args.run_id:
        parser.error("--run-id is required for report indexing")

    if args.report_dir:
        index_report_directory(args.report_dir, args.run_id)
    elif args.report_file:
        index_single_report(args.report_file, args.run_id)


if __name__ == "__main__":
    main()
