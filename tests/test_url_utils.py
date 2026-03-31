"""Tests for url_utils.normalize_url_deep and dedup_urls."""

import pytest
from lib.url_utils import normalize_url_deep, dedup_urls


# ── arXiv normalization ──

class TestArxivNormalization:
    def test_abs_unchanged(self):
        assert normalize_url_deep("https://arxiv.org/abs/2301.12345") == "https://arxiv.org/abs/2301.12345"

    def test_pdf_to_abs(self):
        assert normalize_url_deep("https://arxiv.org/pdf/2301.12345.pdf") == "https://arxiv.org/abs/2301.12345"

    def test_html_version_stripped(self):
        assert normalize_url_deep("https://arxiv.org/html/2301.12345v2") == "https://arxiv.org/abs/2301.12345"

    def test_www_stripped(self):
        assert normalize_url_deep("http://www.arxiv.org/abs/2301.12345") == "https://arxiv.org/abs/2301.12345"

    def test_five_digit_id(self):
        assert normalize_url_deep("https://arxiv.org/abs/2301.00001") == "https://arxiv.org/abs/2301.00001"

    def test_all_forms_equivalent(self):
        forms = [
            "https://arxiv.org/abs/2301.12345",
            "https://arxiv.org/pdf/2301.12345.pdf",
            "https://arxiv.org/html/2301.12345v2",
        ]
        normalized = {normalize_url_deep(u) for u in forms}
        assert len(normalized) == 1


# ── GitHub normalization ──

class TestGithubNormalization:
    def test_case_insensitive(self):
        assert normalize_url_deep("https://github.com/User/Repo") == "https://github.com/user/repo"

    def test_strip_git_suffix(self):
        assert normalize_url_deep("https://github.com/User/Repo.git") == "https://github.com/user/repo"

    def test_strip_trailing_slash(self):
        assert normalize_url_deep("https://github.com/user/repo/") == "https://github.com/user/repo"

    def test_full_uppercase(self):
        assert normalize_url_deep("https://GITHUB.COM/HuggingFace/Transformers") == "https://github.com/huggingface/transformers"

    def test_all_forms_equivalent(self):
        forms = [
            "https://github.com/User/Repo.git",
            "https://github.com/user/repo",
            "https://github.com/USER/REPO/",
        ]
        normalized = {normalize_url_deep(u) for u in forms}
        assert len(normalized) == 1


# ── YouTube normalization ──

class TestYoutubeNormalization:
    def test_short_url(self):
        assert normalize_url_deep("https://youtu.be/dQw4w9WgXcQ") == "https://youtube.com/watch?v=dQw4w9WgXcQ"

    def test_watch_with_tracking(self):
        assert normalize_url_deep("https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=abc") == "https://youtube.com/watch?v=dQw4w9WgXcQ"

    def test_embed(self):
        assert normalize_url_deep("https://youtube.com/embed/dQw4w9WgXcQ") == "https://youtube.com/watch?v=dQw4w9WgXcQ"

    def test_shorts(self):
        assert normalize_url_deep("https://youtube.com/shorts/dQw4w9WgXcQ") == "https://youtube.com/watch?v=dQw4w9WgXcQ"

    def test_mobile(self):
        assert normalize_url_deep("https://m.youtube.com/watch?v=dQw4w9WgXcQ") == "https://youtube.com/watch?v=dQw4w9WgXcQ"

    def test_all_forms_equivalent(self):
        forms = [
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/embed/dQw4w9WgXcQ",
        ]
        normalized = {normalize_url_deep(u) for u in forms}
        assert len(normalized) == 1


# ── HuggingFace normalization ──

class TestHuggingFaceNormalization:
    def test_paper_to_papers(self):
        assert normalize_url_deep("https://huggingface.co/paper/2301.12345") == "https://huggingface.co/papers/2301.12345"


# ── Tracking param stripping ──

class TestTrackingParams:
    def test_utm_params_stripped(self):
        result = normalize_url_deep("https://example.com/page?utm_source=newsletter&utm_medium=email&id=42")
        assert result == "https://example.com/page?id=42"

    def test_fbclid_stripped(self):
        assert normalize_url_deep("https://example.com/page?fbclid=abc123") == "https://example.com/page"

    def test_gclid_stripped(self):
        assert normalize_url_deep("https://example.com/page?gclid=xyz") == "https://example.com/page"

    def test_beehiiv_stripped(self):
        assert normalize_url_deep("https://example.com/post?_bhlid=abc&_bhiiv=def") == "https://example.com/post"

    def test_substack_stripped(self):
        result = normalize_url_deep("https://example.com/p/article?publication_id=1&post_id=2&token=abc")
        assert "publication_id" not in result
        assert "post_id" not in result
        assert "token" not in result


# ── General normalization ──

class TestGeneralNormalization:
    def test_www_stripping(self):
        assert normalize_url_deep("https://www.example.com/path") == "https://example.com/path"

    def test_trailing_slash(self):
        assert normalize_url_deep("https://example.com/path/") == "https://example.com/path"

    def test_http_to_https(self):
        result = normalize_url_deep("http://example.com/path")
        assert result.startswith("https://")

    def test_fragment_stripped(self):
        result = normalize_url_deep("https://example.com/path#section")
        assert "#" not in result


# ── Edge cases ──

class TestEdgeCases:
    def test_empty_string(self):
        assert normalize_url_deep("") == ""

    def test_none(self):
        assert normalize_url_deep(None) == ""

    def test_non_string(self):
        assert normalize_url_deep(42) == ""

    def test_non_http_scheme(self):
        url = "mailto:test@example.com"
        assert normalize_url_deep(url) == url

    def test_very_long_url(self):
        url = "https://example.com/" + "a" * 10000
        result = normalize_url_deep(url)
        assert result.startswith("https://example.com/")


# ── dedup_urls ──

class TestDedupUrls:
    def test_no_duplicates(self):
        sources = {"sources": [
            {"id": "src_001", "url": "https://arxiv.org/abs/2301.11111", "status": "queued"},
            {"id": "src_002", "url": "https://github.com/user/repo", "status": "queued"},
        ]}
        news = {"articles": [
            {"id": "news_001", "url": "https://techcrunch.com/article", "canonical_url": "https://techcrunch.com/article", "status": "queued"},
        ]}
        result = dedup_urls(sources, news)
        assert len(result["sources_removed"]) == 0
        assert len(result["news_removed"]) == 0

    def test_within_sources_dedup(self):
        sources = {"sources": [
            {"id": "src_001", "url": "https://arxiv.org/abs/2301.12345", "status": "queued"},
            {"id": "src_002", "url": "https://arxiv.org/pdf/2301.12345.pdf", "status": "queued"},
        ]}
        news = {"articles": []}
        result = dedup_urls(sources, news)
        assert len(result["sources_removed"]) == 1
        assert result["sources_removed"][0]["id"] == "src_002"

    def test_cross_stream_dedup(self):
        sources = {"sources": [
            {"id": "src_001", "url": "https://example.com/article", "status": "queued"},
        ]}
        news = {"articles": [
            {"id": "news_001", "url": "https://example.com/article", "canonical_url": "https://example.com/article", "status": "queued"},
        ]}
        result = dedup_urls(sources, news)
        assert len(result["news_removed"]) == 1
        assert result["news_removed"][0]["reason"] == "cross-stream"

    def test_skips_already_duplicate_status(self):
        sources = {"sources": [
            {"id": "src_001", "url": "https://example.com/article", "status": "duplicate"},
            {"id": "src_002", "url": "https://example.com/article", "status": "queued"},
        ]}
        news = {"articles": []}
        result = dedup_urls(sources, news)
        assert len(result["sources_removed"]) == 0
