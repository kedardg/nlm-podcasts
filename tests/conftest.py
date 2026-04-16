"""Shared fixtures for OpenClaw tests."""

import json
import sys
from pathlib import Path

import pytest

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Create a temporary data directory with empty data files."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "sources.json").write_text(json.dumps({"sources": []}, indent=2))
    (data_dir / "news-queue.json").write_text(json.dumps({"articles": []}, indent=2))
    (data_dir / "categories.json").write_text(json.dumps({
        "categories": [
            {"name": "Machine Learning", "slug": "ml", "daily_section": "AI Research",
             "keywords": ["transformer", "neural network", "deep learning"],
             "url_patterns": ["arxiv.org", "openreview.net"]},
            {"name": "Open Source", "slug": "oss", "daily_section": "Development",
             "keywords": ["framework", "library", "open source"],
             "url_patterns": ["github.com", "gitlab.com"]},
            {"name": "General", "slug": "general", "daily_section": "News",
             "keywords": [], "url_patterns": []},
        ],
        "section_order": ["AI Research", "Tech", "Development", "News"],
    }, indent=2))
    return data_dir


@pytest.fixture
def sample_sources():
    """Return sample sources.json data."""
    return {
        "sources": [
            {
                "id": "src_001", "type": "url", "origin": "telegram",
                "url": "https://arxiv.org/abs/2301.12345", "file_path": None,
                "title": "Test Paper", "category": "ml", "tags": [],
                "added_at": "2026-02-27T10:00:00Z", "status": "queued",
                "notebook_id": None, "from_account": None,
            },
            {
                "id": "src_002", "type": "url", "origin": "newsletter",
                "url": "https://github.com/user/repo", "file_path": None,
                "title": "Test Repo", "category": "oss", "tags": [],
                "added_at": "2026-02-27T11:00:00Z", "status": "queued",
                "notebook_id": None, "from_account": None,
            },
        ]
    }


@pytest.fixture
def sample_news():
    """Return sample news-queue.json data."""
    return {
        "articles": [
            {
                "id": "news_001", "url": "https://techcrunch.com/2026/02/27/article",
                "canonical_url": "https://techcrunch.com/2026/02/27/article",
                "title": "Test Article", "source_newsletter": "nl_001",
                "source_newsletter_name": "Test Newsletter",
                "extracted_at": "2026-02-27T10:00:00Z", "category": "tech",
                "content_type": "news", "content_snippet": None,
                "status": "queued", "digest_id": None,
            },
        ]
    }


@pytest.fixture
def sample_v3_report():
    """Return a sample v3 markdown NLM report."""
    return Path(__file__).parent / "fixtures" / "sample_report_v3.md"
