"""Tests for URL classification and categorization."""

import pytest
from newsletter_parser import classify_url, _categorize_by_url, _categorize_by_keywords


class TestClassifyUrl:
    # Research
    def test_arxiv_is_research(self):
        assert classify_url("https://arxiv.org/abs/2301.12345") == "research"

    def test_openreview_is_research(self):
        assert classify_url("https://openreview.net/forum?id=abc") == "research"

    def test_paperswithcode_is_research(self):
        assert classify_url("https://paperswithcode.com/paper/test") == "research"

    def test_semanticscholar_is_research(self):
        assert classify_url("https://semanticscholar.org/paper/test") == "research"

    # HuggingFace
    def test_huggingface_papers_is_research(self):
        assert classify_url("https://huggingface.co/papers/2301.12345") == "research"

    def test_huggingface_repo_is_github(self):
        assert classify_url("https://huggingface.co/meta-llama/Llama-3") == "github"

    # GitHub / OSS
    def test_github_repo(self):
        assert classify_url("https://github.com/user/repo") == "github"

    def test_github_no_repo_path(self):
        # github.com without user/repo path
        result = classify_url("https://github.com")
        assert result != "github"

    def test_gitlab_repo(self):
        assert classify_url("https://gitlab.com/user/project") == "github"

    # YouTube
    def test_youtube_watch(self):
        assert classify_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"

    def test_youtube_shorts(self):
        assert classify_url("https://youtube.com/shorts/dQw4w9WgXcQ") == "youtube"

    def test_youtu_be(self):
        assert classify_url("https://youtu.be/dQw4w9WgXcQ") == "youtube"

    def test_youtube_channel_not_youtube(self):
        result = classify_url("https://youtube.com/@username")
        assert result != "youtube"

    # News
    def test_techcrunch_is_news(self):
        assert classify_url("https://techcrunch.com/2026/article") == "news"

    def test_theverge_is_news(self):
        assert classify_url("https://theverge.com/2026/1/article") == "news"

    def test_reuters_is_news(self):
        assert classify_url("https://reuters.com/technology/article") == "news"

    # Blog
    def test_medium_is_blog(self):
        assert classify_url("https://medium.com/@user/article") == "blog"

    def test_substack_is_blog(self):
        assert classify_url("https://newsletter.substack.com/p/article") == "blog"

    def test_dev_to_is_blog(self):
        assert classify_url("https://dev.to/user/post") == "blog"

    def test_openai_blog(self):
        assert classify_url("https://openai.com/blog/article") == "blog"

    def test_anthropic_blog(self):
        assert classify_url("https://anthropic.com/research/article") == "blog"

    def test_github_io_blog(self):
        assert classify_url("https://karpathy.github.io/post") == "blog"

    def test_blog_in_path(self):
        assert classify_url("https://somesite.com/blog/article") == "blog"

    # Social (filtered)
    def test_twitter_status_is_social(self):
        assert classify_url("https://twitter.com/user/status/12345") == "social"

    def test_x_status_is_social(self):
        assert classify_url("https://x.com/user/status/12345") == "social"

    # Unknown
    def test_unknown_domain(self):
        assert classify_url("https://randomsite.com/page") == "unknown"

    def test_empty_url(self):
        assert classify_url("") == "unknown"

    def test_none_url(self):
        assert classify_url(None) == "unknown"


class TestCategorizeByUrl:
    def test_arxiv_matches_ml(self):
        categories = {"categories": [
            {"slug": "ml", "url_patterns": ["arxiv.org"]},
        ]}
        assert _categorize_by_url("https://arxiv.org/abs/2301.12345", categories) == "ml"

    def test_github_matches_oss(self):
        categories = {"categories": [
            {"slug": "oss", "url_patterns": ["github.com"]},
        ]}
        assert _categorize_by_url("https://github.com/user/repo", categories) == "oss"

    def test_no_match_returns_none(self):
        categories = {"categories": [
            {"slug": "ml", "url_patterns": ["arxiv.org"]},
        ]}
        assert _categorize_by_url("https://example.com", categories) is None


class TestCategorizeByKeywords:
    def test_keyword_match(self):
        categories = {"categories": [
            {"slug": "ml", "keywords": ["transformer", "neural network"]},
            {"slug": "general", "keywords": []},
        ]}
        assert _categorize_by_keywords("New transformer architecture paper", categories) == "ml"

    def test_best_match_wins(self):
        categories = {"categories": [
            {"slug": "ml", "keywords": ["model", "training"]},
            {"slug": "nlp", "keywords": ["model", "language", "text"]},
            {"slug": "general", "keywords": []},
        ]}
        result = _categorize_by_keywords("Language model text generation", categories)
        assert result == "nlp"

    def test_no_match_returns_none(self):
        categories = {"categories": [
            {"slug": "ml", "keywords": ["transformer"]},
            {"slug": "general", "keywords": []},
        ]}
        assert _categorize_by_keywords("Cooking recipes", categories) is None
