"""Pydantic v2 models for OpenClaw data file validation.

Validation is opt-in — called at load boundaries in pipelines.
Validation errors log warnings but don't block execution (graceful degradation).
"""

import sys
from typing import Optional

from pydantic import BaseModel, Field


class Source(BaseModel):
    id: str
    url: Optional[str] = None
    file_path: Optional[str] = None
    type: str = "url"
    origin: str = "manual"
    title: str = ""
    category: str = "general"
    status: str = "queued"
    added_at: str = ""
    tags: list[str] = []
    notebook_id: Optional[str] = None
    from_account: Optional[str] = None
    from_newsletter: Optional[str] = None


class NewsArticle(BaseModel):
    id: str
    url: str
    canonical_url: Optional[str] = None
    title: str = ""
    source_newsletter: Optional[str] = None
    source_newsletter_name: Optional[str] = None
    extracted_at: str = ""
    category: str = "general"
    content_type: str = "news"
    content_snippet: Optional[str] = None
    status: str = "queued"
    digest_id: Optional[str] = None


class Notebook(BaseModel):
    id: str
    alias: str = ""
    category: str = "general"
    category_name: str = ""
    created_at: str = ""
    source_count: int = 0
    source_ids: list[str] = []
    has_podcast: bool = False
    has_report: bool = False
    status: str = "created"
    run_id: Optional[str] = None


def validate_sources(data: dict) -> list[Source]:
    """Validate sources.json entries. Logs warnings for invalid items."""
    results = []
    for i, raw in enumerate(data.get("sources", [])):
        try:
            results.append(Source(**raw))
        except Exception as e:
            print(f"Warning: invalid source at index {i}: {e}", file=sys.stderr)
    return results


def validate_news(data: dict) -> list[NewsArticle]:
    """Validate news-queue.json entries. Logs warnings for invalid items."""
    results = []
    for i, raw in enumerate(data.get("articles", [])):
        try:
            results.append(NewsArticle(**raw))
        except Exception as e:
            print(f"Warning: invalid news article at index {i}: {e}", file=sys.stderr)
    return results


def validate_notebooks(data: dict) -> list[Notebook]:
    """Validate notebooks.json entries. Logs warnings for invalid items."""
    results = []
    for i, raw in enumerate(data.get("notebooks", [])):
        try:
            results.append(Notebook(**raw))
        except Exception as e:
            print(f"Warning: invalid notebook at index {i}: {e}", file=sys.stderr)
    return results
