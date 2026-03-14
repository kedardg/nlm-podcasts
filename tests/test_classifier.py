"""Tests for lib/classifier.py — hybrid content classifier (domain + title + LLM)."""

import pytest
from lib.classifier import (
    classify_by_domain, classify_by_title, classify_batch,
    ContentType, Classification, _parse_llm_response,
)


class TestDomainRules:
    """Test fast-path domain classification."""

    # Research domains
    def test_arxiv(self):
        r = classify_by_domain("https://arxiv.org/abs/2503.01234")
        assert r.content_type == ContentType.research
        assert r.confidence >= 0.90

    def test_openreview(self):
        r = classify_by_domain("https://openreview.net/forum?id=abc")
        assert r.content_type == ContentType.research

    def test_huggingface(self):
        r = classify_by_domain("https://huggingface.co/meta-llama/Llama-3")
        assert r.content_type == ContentType.research

    def test_apple_ml(self):
        r = classify_by_domain("https://machinelearning.apple.com/research/paper")
        assert r.content_type == ContentType.research

    def test_lesswrong(self):
        r = classify_by_domain("https://lesswrong.com/posts/abc/title")
        assert r.content_type == ContentType.research

    # News domains
    def test_techcrunch(self):
        r = classify_by_domain("https://techcrunch.com/2026/03/article")
        assert r.content_type == ContentType.news

    def test_venturebeat(self):
        r = classify_by_domain("https://venturebeat.com/ai/article")
        assert r.content_type == ContentType.news

    def test_cnbc(self):
        r = classify_by_domain("https://cnbc.com/2026/03/05/article.html")
        assert r.content_type == ContentType.news

    def test_wsj(self):
        r = classify_by_domain("https://wsj.com/articles/article-title")
        assert r.content_type == ContentType.news

    def test_axios(self):
        r = classify_by_domain("https://axios.com/2026/03/05/article")
        assert r.content_type == ContentType.news

    # Blog domains
    def test_medium(self):
        r = classify_by_domain("https://medium.com/@user/article")
        assert r.content_type == ContentType.blog

    def test_substack(self):
        r = classify_by_domain("https://newsletter.substack.com/p/article")
        assert r.content_type == ContentType.blog

    def test_dev_to(self):
        r = classify_by_domain("https://dev.to/user/post")
        assert r.content_type == ContentType.blog

    def test_theneuron(self):
        r = classify_by_domain("https://theneuron.ai/tutorial/article")
        assert r.content_type == ContentType.blog

    # Aggregator domains
    def test_aiweekly(self):
        r = classify_by_domain("https://aiweekly.co/?sid=abc")
        assert r.content_type == ContentType.news
        assert r.method == "domain_rule"

    # Product domains
    def test_claude(self):
        r = classify_by_domain("https://claude.com/artifacts/123")
        assert r.content_type == ContentType.blog

    def test_perplexity(self):
        r = classify_by_domain("https://perplexity.ai/search/test")
        assert r.content_type == ContentType.blog

    # Company path-based rules
    def test_openai_research(self):
        r = classify_by_domain("https://openai.com/research/paper-title")
        assert r.content_type == ContentType.research

    def test_openai_blog(self):
        r = classify_by_domain("https://openai.com/blog/announcement")
        assert r.content_type == ContentType.blog

    def test_anthropic_research(self):
        r = classify_by_domain("https://anthropic.com/research/paper")
        assert r.content_type == ContentType.research

    def test_anthropic_news(self):
        r = classify_by_domain("https://anthropic.com/news/announcement")
        assert r.content_type == ContentType.blog

    def test_blog_google(self):
        r = classify_by_domain("https://blog.google/technology/ai/article")
        assert r.content_type == ContentType.blog

    # GitHub: low-confidence research
    def test_github(self):
        r = classify_by_domain("https://github.com/user/repo")
        assert r.content_type == ContentType.research
        assert r.confidence < 0.80  # Low confidence

    # Unknown / uncertain domains
    def test_youtube_uncertain(self):
        r = classify_by_domain("https://youtube.com/watch?v=abc")
        assert r is None  # YouTube is too uncertain

    def test_reddit_uncertain(self):
        r = classify_by_domain("https://reddit.com/r/MachineLearning/post")
        assert r is None

    def test_unknown_domain(self):
        r = classify_by_domain("https://randomsite.com/page")
        assert r is None

    def test_www_stripped(self):
        r = classify_by_domain("https://www.arxiv.org/abs/2503.01234")
        assert r.content_type == ContentType.research

    def test_empty_url(self):
        r = classify_by_domain("")
        assert r is None


class TestTitleHeuristics:
    """Test title-based classification."""

    def test_research_signals(self):
        r = classify_by_title("Transformer Architecture with Pre-training on Large Dataset")
        assert r is not None
        assert r.content_type == ContentType.research

    def test_news_signals(self):
        r = classify_by_title("OpenAI Raises $10B in New Funding Round")
        assert r is not None
        assert r.content_type == ContentType.news

    def test_blog_signals(self):
        r = classify_by_title("How to Build a RAG Pipeline: A Beginner's Guide")
        assert r is not None
        assert r.content_type == ContentType.blog

    def test_ambiguous_returns_none(self):
        r = classify_by_title("AI Update")
        assert r is None

    def test_empty_title(self):
        r = classify_by_title("")
        assert r is None


class TestParseLlmResponse:
    """Test LLM response parsing."""

    def test_clean_json(self):
        r = _parse_llm_response('{"type": "research", "confidence": 0.9}', "https://test.com")
        assert r.content_type == ContentType.research
        assert r.confidence == 0.9

    def test_json_in_code_block(self):
        r = _parse_llm_response('```json\n{"type": "news", "confidence": 0.85}\n```', "https://test.com")
        assert r.content_type == ContentType.news

    def test_json_with_extra_text(self):
        r = _parse_llm_response('Here is my answer: {"type": "blog", "confidence": 0.8}', "https://test.com")
        assert r.content_type == ContentType.blog

    def test_invalid_type_defaults_to_news(self):
        r = _parse_llm_response('{"type": "podcast", "confidence": 0.5}', "https://test.com")
        assert r.content_type == ContentType.news
        assert r.confidence == 0.50

    def test_no_json_defaults_to_news(self):
        r = _parse_llm_response("I'm not sure about this one", "https://test.com")
        assert r.content_type == ContentType.news
        assert r.confidence == 0.50


class TestClassifyBatch:
    """Test batch classification (domain + title only, no LLM)."""

    def test_batch_no_llm(self):
        items = [
            {"url": "https://arxiv.org/abs/2503.01234", "title": "Paper Title"},
            {"url": "https://techcrunch.com/article", "title": "Company News"},
            {"url": "https://medium.com/@user/post", "title": "Tutorial"},
            {"url": "https://randomsite.com", "title": "Unknown Item"},
        ]
        results = classify_batch(items, use_llm=False)
        assert len(results) == 4

        # Check that classifications were added
        types = {r["classification"]["content_type"] for r in results}
        assert "research" in types
        assert "news" in types
        assert "blog" in types

    def test_batch_default_for_uncertain(self):
        items = [{"url": "https://randomsite.com", "title": "Vague Title"}]
        results = classify_batch(items, use_llm=False)
        assert results[0]["classification"]["method"] == "default"
        assert results[0]["classification"]["content_type"] == "news"


class TestClassificationDataclass:
    """Test Classification helper methods."""

    def test_to_dict(self):
        c = Classification(ContentType.research, 0.95, "domain_rule", "test")
        d = c.to_dict()
        assert d["content_type"] == "research"
        assert d["confidence"] == 0.95
        assert d["method"] == "domain_rule"
        assert d["reasoning"] == "test"
