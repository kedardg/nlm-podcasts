"""Tests for daily_feed_generator — news cap, semantic dedup, research passthrough."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import daily_feed_generator as dfg


# ── Helpers ───────────────────────────────────────────────────────────────────


def _write_sources(tmp_path, items):
    f = tmp_path / "sources.json"
    f.write_text(json.dumps({"sources": items}))
    return f


def _write_news(tmp_path, articles):
    f = tmp_path / "news-queue.json"
    f.write_text(json.dumps({"articles": articles}))
    return f


def _make_source(n, date="2026-03-10"):
    return {
        "id": f"src_{n:03d}",
        "type": "url",
        "url": f"https://arxiv.org/abs/{n:07d}",
        "title": f"Research Paper {n}",
        "category": "ml",
        "added_at": f"{date}T10:00:00Z",
        "status": "queued",
    }


def _make_article(n, date="2026-03-10"):
    return {
        "id": f"nws_{n:03d}",
        "url": f"https://example.com/article-{n}",
        "title": f"News Article {n}",
        "category": "tech",
        "content_type": "news",
        "extracted_at": f"{date}T10:00:00Z",
        "status": "queued",
    }


def _no_semantic_dedup(monkeypatch):
    """Disable semantic dedup (no API key) for tests focused on other behaviour."""
    monkeypatch.setattr(dfg, "_load_openai_key", lambda: None)


# ── Tests: news cap and filtering ─────────────────────────────────────────────


class TestCollectTodayItems:
    def test_research_only_all_returned(self, tmp_path, monkeypatch):
        """All research items from sources.json are returned, no cap."""
        _no_semantic_dedup(monkeypatch)
        sources_file = _write_sources(tmp_path, [_make_source(i) for i in range(50)])
        news_file = _write_news(tmp_path, [])
        monkeypatch.setattr(dfg, "SOURCES_FILE", sources_file)
        monkeypatch.setattr(dfg, "NEWS_QUEUE_FILE", news_file)

        items = dfg.collect_today_items("2026-03-10")
        assert len(items) == 50

    def test_news_under_limit_all_returned(self, tmp_path, monkeypatch):
        """News items under NEWS_DAILY_LIMIT are all returned."""
        _no_semantic_dedup(monkeypatch)
        sources_file = _write_sources(tmp_path, [])
        news_file = _write_news(tmp_path, [_make_article(i) for i in range(10)])
        monkeypatch.setattr(dfg, "SOURCES_FILE", sources_file)
        monkeypatch.setattr(dfg, "NEWS_QUEUE_FILE", news_file)

        items = dfg.collect_today_items("2026-03-10")
        assert len(items) == 10

    def test_news_over_limit_capped(self, tmp_path, monkeypatch):
        """News items exceeding NEWS_DAILY_LIMIT are capped."""
        _no_semantic_dedup(monkeypatch)
        sources_file = _write_sources(tmp_path, [])
        news_file = _write_news(tmp_path, [_make_article(i) for i in range(100)])
        monkeypatch.setattr(dfg, "SOURCES_FILE", sources_file)
        monkeypatch.setattr(dfg, "NEWS_QUEUE_FILE", news_file)

        items = dfg.collect_today_items("2026-03-10")
        assert len(items) == dfg.NEWS_DAILY_LIMIT

    def test_mixed_research_uncapped_news_capped(self, tmp_path, monkeypatch):
        """Research items are never capped; news is capped independently."""
        _no_semantic_dedup(monkeypatch)
        sources_file = _write_sources(tmp_path, [_make_source(i) for i in range(39)])
        news_file = _write_news(tmp_path, [_make_article(i) for i in range(213)])
        monkeypatch.setattr(dfg, "SOURCES_FILE", sources_file)
        monkeypatch.setattr(dfg, "NEWS_QUEUE_FILE", news_file)

        items = dfg.collect_today_items("2026-03-10")
        research = [i for i in items if i.get("data_file") == "sources"]
        news = [i for i in items if i.get("data_file") == "news-queue"]

        assert len(research) == 39
        assert len(news) == dfg.NEWS_DAILY_LIMIT

    def test_news_capped_keeps_most_recent(self, tmp_path, monkeypatch):
        """When capping news, the most recent items by extracted_at are kept."""
        _no_semantic_dedup(monkeypatch)
        articles = []
        for i in range(50):
            a = _make_article(i)
            a["extracted_at"] = f"2026-03-10T{i:02d}:00:00Z"
            articles.append(a)

        sources_file = _write_sources(tmp_path, [])
        news_file = _write_news(tmp_path, articles)
        monkeypatch.setattr(dfg, "SOURCES_FILE", sources_file)
        monkeypatch.setattr(dfg, "NEWS_QUEUE_FILE", news_file)

        items = dfg.collect_today_items("2026-03-10")
        assert len(items) == dfg.NEWS_DAILY_LIMIT

        cutoff_hour = 50 - dfg.NEWS_DAILY_LIMIT
        for item in items:
            hour = int(item["extracted_at"][11:13])
            assert hour >= cutoff_hour

    def test_date_filter_excludes_other_dates(self, tmp_path, monkeypatch):
        """Items from other dates are excluded."""
        _no_semantic_dedup(monkeypatch)
        sources_file = _write_sources(tmp_path, [
            _make_source(1, date="2026-03-09"),
            _make_source(2, date="2026-03-10"),
        ])
        news_file = _write_news(tmp_path, [
            _make_article(1, date="2026-03-09"),
            _make_article(2, date="2026-03-10"),
        ])
        monkeypatch.setattr(dfg, "SOURCES_FILE", sources_file)
        monkeypatch.setattr(dfg, "NEWS_QUEUE_FILE", news_file)

        items = dfg.collect_today_items("2026-03-10")
        assert len(items) == 2
        for item in items:
            assert "2026-03-10" in item["added_at"]


# ── Tests: semantic dedup ─────────────────────────────────────────────────────


class TestSemanticDedup:
    """Tests for _semantic_dedup() using mocked embeddings."""

    def _mock_embeddings(self, vectors):
        mock_resp = MagicMock()
        mock_resp.data = [MagicMock(embedding=v) for v in vectors]
        return mock_resp

    def test_removes_same_topic(self, tmp_path, monkeypatch):
        """Items with identical embeddings (same story) are deduplicated."""
        sources_file = _write_sources(tmp_path, [])
        news_file = _write_news(tmp_path, [_make_article(1), _make_article(2)])
        monkeypatch.setattr(dfg, "SOURCES_FILE", sources_file)
        monkeypatch.setattr(dfg, "NEWS_QUEUE_FILE", news_file)

        same_vec = [1.0] + [0.0] * 1535
        mock_resp = self._mock_embeddings([same_vec, same_vec])

        with patch("daily_feed_generator._load_openai_key", return_value="sk-test"), \
             patch("openai.OpenAI") as mock_cls:
            mock_cls.return_value.embeddings.create.return_value = mock_resp
            items = dfg.collect_today_items("2026-03-10")

        assert len(items) == 1

    def test_keeps_distinct_topics(self, tmp_path, monkeypatch):
        """Items with orthogonal embeddings (different topics) are both kept."""
        sources_file = _write_sources(tmp_path, [])
        news_file = _write_news(tmp_path, [_make_article(1), _make_article(2)])
        monkeypatch.setattr(dfg, "SOURCES_FILE", sources_file)
        monkeypatch.setattr(dfg, "NEWS_QUEUE_FILE", news_file)

        vec_a = [1.0] + [0.0] * 1535
        vec_b = [0.0, 1.0] + [0.0] * 1534
        mock_resp = self._mock_embeddings([vec_a, vec_b])

        with patch("daily_feed_generator._load_openai_key", return_value="sk-test"), \
             patch("openai.OpenAI") as mock_cls:
            mock_cls.return_value.embeddings.create.return_value = mock_resp
            items = dfg.collect_today_items("2026-03-10")

        assert len(items) == 2

    def test_prefers_sources_over_news(self, tmp_path, monkeypatch):
        """When a research source and a news article share a topic, the research source wins."""
        source = _make_source(1)
        article = _make_article(1)
        article["url"] = "https://techcrunch.com/gpt5-launch"

        sources_file = _write_sources(tmp_path, [source])
        news_file = _write_news(tmp_path, [article])
        monkeypatch.setattr(dfg, "SOURCES_FILE", sources_file)
        monkeypatch.setattr(dfg, "NEWS_QUEUE_FILE", news_file)

        same_vec = [1.0] + [0.0] * 1535
        mock_resp = self._mock_embeddings([same_vec, same_vec])

        with patch("daily_feed_generator._load_openai_key", return_value="sk-test"), \
             patch("openai.OpenAI") as mock_cls:
            mock_cls.return_value.embeddings.create.return_value = mock_resp
            items = dfg.collect_today_items("2026-03-10")

        assert len(items) == 1
        assert items[0]["data_file"] == "sources"

    def test_falls_back_on_api_error(self, tmp_path, monkeypatch):
        """If the embedding API fails, items are returned unmodified (no crash)."""
        sources_file = _write_sources(tmp_path, [_make_source(1)])
        news_file = _write_news(tmp_path, [_make_article(1)])
        monkeypatch.setattr(dfg, "SOURCES_FILE", sources_file)
        monkeypatch.setattr(dfg, "NEWS_QUEUE_FILE", news_file)

        with patch("daily_feed_generator._load_openai_key", return_value="sk-test"), \
             patch("openai.OpenAI") as mock_cls:
            mock_cls.return_value.embeddings.create.side_effect = Exception("timeout")
            items = dfg.collect_today_items("2026-03-10")

        assert len(items) == 2

    def test_skips_when_no_api_key(self, tmp_path, monkeypatch):
        """Without an API key, semantic dedup is skipped and all items are returned."""
        monkeypatch.setattr(dfg, "_load_openai_key", lambda: None)
        sources_file = _write_sources(tmp_path, [_make_source(i) for i in range(5)])
        news_file = _write_news(tmp_path, [_make_article(i) for i in range(5)])
        monkeypatch.setattr(dfg, "SOURCES_FILE", sources_file)
        monkeypatch.setattr(dfg, "NEWS_QUEUE_FILE", news_file)

        items = dfg.collect_today_items("2026-03-10")
        assert len(items) == 10
