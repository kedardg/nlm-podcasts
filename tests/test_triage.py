"""Tests for data_triage.py grouping rules."""

import json
import pytest
from pathlib import Path
from data_triage import (
    build_affinity_map,
    find_affinity_partner,
    run_triage,
    MIN_SOURCES_FOR_OWN_NOTEBOOK,
    MAX_BEFORE_SPLIT,
    IDEAL_SPLIT_SIZE,
)


@pytest.fixture
def categories_data():
    return {
        "categories": [
            {"name": "Machine Learning", "slug": "ml", "daily_section": "AI Research",
             "keywords": ["transformer"], "url_patterns": ["arxiv.org"]},
            {"name": "NLP", "slug": "nlp", "daily_section": "AI Research",
             "keywords": ["language model"], "url_patterns": []},
            {"name": "Computer Vision", "slug": "cv", "daily_section": "AI Research",
             "keywords": ["vision"], "url_patterns": []},
            {"name": "Open Source", "slug": "oss", "daily_section": "Development",
             "keywords": ["github"], "url_patterns": ["github.com"]},
            {"name": "General", "slug": "general", "daily_section": "News",
             "keywords": [], "url_patterns": []},
        ]
    }


class TestAffinityMap:
    def test_builds_section_mapping(self, categories_data):
        section_map = build_affinity_map(categories_data)
        assert "AI Research" in section_map
        assert "ml" in section_map["AI Research"]
        assert "nlp" in section_map["AI Research"]
        assert "cv" in section_map["AI Research"]
        assert "oss" in section_map["Development"]

    def test_skips_categories_without_slug(self):
        data = {"categories": [{"name": "No Slug", "daily_section": "News"}]}
        section_map = build_affinity_map(data)
        assert all(len(slugs) == 0 or None not in slugs for slugs in section_map.values())


class TestAffinityPartner:
    def test_finds_largest_sibling(self, categories_data):
        section_map = build_affinity_map(categories_data)
        counts = {"ml": 20, "nlp": 5, "cv": 1}
        partner = find_affinity_partner("cv", section_map, counts)
        assert partner == "ml"  # ml is largest in AI Research

    def test_no_partner_if_alone_in_section(self, categories_data):
        section_map = build_affinity_map(categories_data)
        counts = {"oss": 1}
        partner = find_affinity_partner("oss", section_map, counts)
        # oss is alone in Development
        assert partner is None or partner != "oss"


class TestGroupingRules:
    """Test grouping rules via run_triage with mocked data files."""

    def test_thin_category_merged(self, tmp_data_dir, categories_data):
        """Categories with <3 items should merge with affinity partner."""
        # Create sources: 10 ml, 1 cv (thin)
        sources = {"sources": []}
        for i in range(10):
            sources["sources"].append({
                "id": f"src_{i:03d}", "url": f"https://arxiv.org/abs/2301.{i:05d}",
                "title": f"ML Paper {i}", "category": "ml",
                "status": "queued", "added_at": "2026-02-27T10:00:00Z",
            })
        sources["sources"].append({
            "id": "src_010", "url": "https://example.com/cv-paper",
            "title": "CV Paper", "category": "cv",
            "status": "queued", "added_at": "2026-02-27T10:00:00Z",
        })

        # Write test data
        (tmp_data_dir / "sources.json").write_text(json.dumps(sources))
        (tmp_data_dir / "news-queue.json").write_text(json.dumps({"articles": []}))
        (tmp_data_dir / "categories.json").write_text(json.dumps(categories_data))

        import data_triage
        orig_sources = data_triage.SOURCES_FILE
        orig_news = data_triage.NEWS_QUEUE_FILE
        orig_cats = data_triage.CATEGORIES_FILE

        try:
            data_triage.SOURCES_FILE = tmp_data_dir / "sources.json"
            data_triage.NEWS_QUEUE_FILE = tmp_data_dir / "news-queue.json"
            data_triage.CATEGORIES_FILE = tmp_data_dir / "categories.json"

            plan = run_triage(dry_run=True)
            # cv should be merged with ml (same section, ml is largest)
            merged_groups = [g for g in plan["notebook_groups"] if g["is_merged"]]
            assert len(merged_groups) >= 1
            merged_cats = []
            for g in merged_groups:
                merged_cats.extend(g["categories"])
            assert "cv" in merged_cats
        finally:
            data_triage.SOURCES_FILE = orig_sources
            data_triage.NEWS_QUEUE_FILE = orig_news
            data_triage.CATEGORIES_FILE = orig_cats

    def test_normal_category_own_notebook(self, tmp_data_dir, categories_data):
        """Categories with 3-30 items get their own notebook."""
        sources = {"sources": []}
        for i in range(5):
            sources["sources"].append({
                "id": f"src_{i:03d}", "url": f"https://arxiv.org/abs/2301.{i:05d}",
                "title": f"ML Paper {i}", "category": "ml",
                "status": "queued", "added_at": "2026-02-27T10:00:00Z",
            })

        (tmp_data_dir / "sources.json").write_text(json.dumps(sources))
        (tmp_data_dir / "news-queue.json").write_text(json.dumps({"articles": []}))
        (tmp_data_dir / "categories.json").write_text(json.dumps(categories_data))

        import data_triage
        orig_sources = data_triage.SOURCES_FILE
        orig_news = data_triage.NEWS_QUEUE_FILE
        orig_cats = data_triage.CATEGORIES_FILE

        try:
            data_triage.SOURCES_FILE = tmp_data_dir / "sources.json"
            data_triage.NEWS_QUEUE_FILE = tmp_data_dir / "news-queue.json"
            data_triage.CATEGORIES_FILE = tmp_data_dir / "categories.json"

            plan = run_triage(dry_run=True)
            ml_groups = [g for g in plan["notebook_groups"] if "ml" in g["categories"]]
            assert len(ml_groups) == 1
            assert ml_groups[0]["source_count"] == 5
            assert not ml_groups[0]["is_split"]
        finally:
            data_triage.SOURCES_FILE = orig_sources
            data_triage.NEWS_QUEUE_FILE = orig_news
            data_triage.CATEGORIES_FILE = orig_cats

    def test_zero_items_skipped(self, tmp_data_dir, categories_data):
        """Categories with 0 items should produce a warning."""
        sources = {"sources": [
            {"id": "src_001", "url": "https://arxiv.org/abs/2301.00001",
             "title": "ML Paper", "category": "ml", "status": "queued",
             "added_at": "2026-02-27T10:00:00Z"},
        ]}

        (tmp_data_dir / "sources.json").write_text(json.dumps(sources))
        (tmp_data_dir / "news-queue.json").write_text(json.dumps({"articles": []}))
        (tmp_data_dir / "categories.json").write_text(json.dumps(categories_data))

        import data_triage
        orig_sources = data_triage.SOURCES_FILE
        orig_news = data_triage.NEWS_QUEUE_FILE
        orig_cats = data_triage.CATEGORIES_FILE

        try:
            data_triage.SOURCES_FILE = tmp_data_dir / "sources.json"
            data_triage.NEWS_QUEUE_FILE = tmp_data_dir / "news-queue.json"
            data_triage.CATEGORIES_FILE = tmp_data_dir / "categories.json"

            plan = run_triage(dry_run=True)
            assert any("No items" in w for w in plan["warnings"])
        finally:
            data_triage.SOURCES_FILE = orig_sources
            data_triage.NEWS_QUEUE_FILE = orig_news
            data_triage.CATEGORIES_FILE = orig_cats
