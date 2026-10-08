#!/usr/bin/env python3
"""
RAG Query Script for OpenClaw Research Automation System.

Two query modes:
1. report: Searches "research_reports" collection for report chunks (legacy).
2. notebook: Searches "notebook_sources" collection to route questions to
   the most relevant NotebookLM notebooks.

Usage:
    python query.py "<question>"
    python query.py "<question>" --mode notebook
    python query.py "<question>" --mode notebook --category ml
    python query.py "<question>" --mode report --top-k 10
"""

import argparse
import json
import os
import sys
from pathlib import Path

import chromadb
from openai import OpenAI

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CHROMA_DB_PATH = PROJECT_ROOT / "rag" / "chroma_db"
NOTEBOOKS_FILE = PROJECT_ROOT / "data" / "notebooks.json"
COLLECTION_NAME = "research_reports"
NOTEBOOK_SOURCES_COLLECTION = "notebook_sources"

# Config
EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_TOP_K = 5
MIN_NOTEBOOK_SCORE = 0.3  # Minimum best_score to consider a notebook relevant


def get_openai_client():
    """Initialize OpenAI client using environment variable."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print(json.dumps({"error": "OPENAI_API_KEY not set", "results": []}))
        sys.exit(1)
    return OpenAI(api_key=api_key)


def get_chroma_client():
    """Initialize persistent ChromaDB client."""
    if not CHROMA_DB_PATH.exists():
        print(json.dumps({"error": "ChromaDB not initialized. No reports indexed yet.", "results": []}))
        sys.exit(0)
    return chromadb.PersistentClient(path=str(CHROMA_DB_PATH))


def embed_query(client, question):
    """Embed a query string using OpenAI embeddings API."""
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=[question],
    )
    return response.data[0].embedding


def query_chromadb(question, category=None, top_k=DEFAULT_TOP_K):
    """Query ChromaDB for relevant report chunks.

    Args:
        question: The user's question
        category: Optional category slug to filter results
        top_k: Number of results to return

    Returns:
        List of result dicts with text, score, source_id, category, original_urls
    """
    openai_client = get_openai_client()
    chroma_client = get_chroma_client()

    try:
        collection = chroma_client.get_collection(name=COLLECTION_NAME)
    except Exception:
        return []

    # Embed the question
    query_embedding = embed_query(openai_client, question)

    # Build where filter for category
    where_filter = None
    if category:
        where_filter = {"category": category}

    # Query ChromaDB
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        where=where_filter,
        include=["documents", "metadatas", "distances"],
    )

    # Format results
    formatted = []
    if results and results["documents"] and results["documents"][0]:
        for i, doc in enumerate(results["documents"][0]):
            metadata = results["metadatas"][0][i] if results["metadatas"] else {}
            distance = results["distances"][0][i] if results["distances"] else 0

            # Convert cosine distance to similarity score (ChromaDB returns distance)
            score = 1 - distance

            original_urls = []
            if metadata.get("original_urls"):
                try:
                    original_urls = json.loads(metadata["original_urls"])
                except (json.JSONDecodeError, TypeError):
                    pass

            formatted.append({
                "text": doc,
                "score": round(score, 4),
                "source_id": metadata.get("notebook_id", ""),
                "category": metadata.get("category", ""),
                "original_urls": original_urls,
                "run_id": metadata.get("run_id", ""),
                "generated_date": metadata.get("generated_date", ""),
            })

    return formatted


def query_notebook_router(question, category=None, top_k=3):
    """Query ChromaDB notebook_sources to find the most relevant notebooks.

    Searches the notebook_sources collection, groups results by notebook_id,
    and returns the top notebooks ranked by hit count and best score.

    Args:
        question: The user's question
        category: Optional category slug to filter
        top_k: Number of notebooks to return (default 3)

    Returns:
        Dict with query, mode, notebooks list, and fallback_used flag.
    """
    openai_client = get_openai_client()
    chroma_client = get_chroma_client()

    try:
        collection = chroma_client.get_collection(name=NOTEBOOK_SOURCES_COLLECTION)
    except Exception:
        # Collection doesn't exist yet — use fallback
        notebooks = fallback_recent_notebooks(category, limit=top_k)
        return {
            "query": question,
            "mode": "notebook",
            "notebooks": notebooks,
            "fallback_used": True,
        }

    # Check if collection has any documents
    if collection.count() == 0:
        notebooks = fallback_recent_notebooks(category, limit=top_k)
        return {
            "query": question,
            "mode": "notebook",
            "notebooks": notebooks,
            "fallback_used": True,
        }

    query_embedding = embed_query(openai_client, question)

    where_filter = None
    if category:
        where_filter = {"category": category}

    # Fetch more results for diversity, then group by notebook
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=10,
        where=where_filter,
        include=["metadatas", "distances"],
    )

    if not results or not results["metadatas"] or not results["metadatas"][0]:
        notebooks = fallback_recent_notebooks(category, limit=top_k)
        return {
            "query": question,
            "mode": "notebook",
            "notebooks": notebooks,
            "fallback_used": True,
        }

    # Group by notebook_id
    notebook_hits = {}
    for i, metadata in enumerate(results["metadatas"][0]):
        nb_id = metadata.get("notebook_id", "")
        if not nb_id:
            continue

        distance = results["distances"][0][i] if results["distances"] else 0
        score = round(1 - distance, 4)

        if nb_id not in notebook_hits:
            notebook_hits[nb_id] = {
                "notebook_id": nb_id,
                "notebook_alias": metadata.get("notebook_alias", ""),
                "category": metadata.get("category", ""),
                "created_at": metadata.get("created_at", ""),
                "hit_count": 0,
                "best_score": 0,
                "sources_matched": [],
            }

        entry = notebook_hits[nb_id]
        entry["hit_count"] += 1
        entry["best_score"] = max(entry["best_score"], score)

        source_url = metadata.get("source_url", "")
        if source_url and source_url not in entry["sources_matched"]:
            entry["sources_matched"].append(source_url)

    # Filter out notebooks below minimum score threshold
    notebook_hits = {
        nb_id: nb for nb_id, nb in notebook_hits.items()
        if nb["best_score"] >= MIN_NOTEBOOK_SCORE
    }

    if not notebook_hits:
        return {
            "query": question,
            "mode": "notebook",
            "notebooks": [],
            "fallback_used": False,
        }

    # Composite score: 70% quality, 30% volume (normalized)
    max_hits = max(nb["hit_count"] for nb in notebook_hits.values())
    for nb in notebook_hits.values():
        nb["composite_score"] = round(
            nb["best_score"] * 0.7 + (nb["hit_count"] / max_hits) * 0.3, 4
        )

    ranked = sorted(
        notebook_hits.values(),
        key=lambda x: (x["composite_score"], x["created_at"]),
        reverse=True,
    )

    return {
        "query": question,
        "mode": "notebook",
        "notebooks": ranked[:top_k],
        "fallback_used": False,
    }


def fallback_recent_notebooks(category=None, limit=3):
    """Return most recent notebooks from notebooks.json as fallback.

    Used when ChromaDB notebook_sources collection is empty.
    """
    if not NOTEBOOKS_FILE.exists():
        return []

    with open(NOTEBOOKS_FILE) as f:
        data = json.load(f)

    notebooks = data.get("notebooks", [])
    if not notebooks:
        return []

    if category:
        notebooks = [nb for nb in notebooks if nb.get("category") == category]

    # Sort by created_at descending
    notebooks.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    result = []
    for nb in notebooks[:limit]:
        result.append({
            "notebook_id": nb.get("id", ""),
            "notebook_alias": nb.get("alias", ""),
            "category": nb.get("category", ""),
            "created_at": nb.get("created_at", ""),
            "hit_count": 0,
            "best_score": 0,
            "sources_matched": [],
        })

    return result


def main():
    parser = argparse.ArgumentParser(description="Query the research RAG store")
    parser.add_argument("question", help="The question to search for")
    parser.add_argument("--mode", choices=["notebook", "report"], default="notebook",
                        help="Query mode: 'notebook' routes to notebooks, 'report' searches report chunks (default: notebook)")
    parser.add_argument("--category", help="Filter by category slug (e.g., ml, robotics, github)")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help=f"Number of results (default: {DEFAULT_TOP_K})")
    args = parser.parse_args()

    if args.mode == "notebook":
        output = query_notebook_router(args.question, category=args.category, top_k=args.top_k)
    else:
        results = query_chromadb(args.question, category=args.category, top_k=args.top_k)
        output = {
            "query": args.question,
            "mode": "report",
            "category_filter": args.category,
            "result_count": len(results),
            "results": results,
        }

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
