"""Tests for the self-contained responsive HTML magazine renderer."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lib.html_renderer import render_magazine_html
from lib.pdf_renderer import _safe_text, _safe_link, _safe_attr, _safe_url


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_magazine(
    title="Test Digest",
    date="2026-03-29",
    editorial_brief="A test editorial brief.",
    sections=None,
):
    if sections is None:
        sections = [
            {
                "name": "AI Research",
                "introduction": "Key developments.",
                "items": [
                    {
                        "id": "item1",
                        "title": "Paper on Transformers",
                        "url": "https://arxiv.org/abs/123",
                        "type": "featured",
                        "content_type": "research",
                        "summary": "A new architecture.",
                        "significance": "high",
                    },
                    {
                        "id": "item2",
                        "title": "LoRA Update",
                        "url": "https://github.com/example/lora",
                        "type": "standard",
                        "content_type": "tool",
                        "summary": "Fine-tuning method.",
                        "significance": "medium",
                    },
                ],
            },
            {
                "name": "Tech",
                "items": [
                    {
                        "id": "item3",
                        "title": "Startup News",
                        "url": "https://techcrunch.com/story",
                        "type": "brief",
                        "content_type": "news",
                        "summary": "Funding round.",
                    },
                ],
            },
        ]
    return {
        "title": title,
        "date": date,
        "editorial_brief": editorial_brief,
        "top_stories": [
            {"title": "Paper on Transformers", "url": "https://arxiv.org/abs/123", "summary": "First sentence."},
        ],
        "sections": sections,
        "stats": {"total": 3, "research": 1, "news": 1, "blog": 1},
    }


# ── HTML Renderer Tests ─────────────────────────────────────────────────────


def test_render_produces_valid_html(tmp_path):
    """Output contains DOCTYPE, style, and essential meta tags."""
    out = tmp_path / "test.html"
    render_magazine_html(_make_magazine(), out)
    content = out.read_text()

    assert "<!DOCTYPE html>" in content
    assert "<style>" in content
    assert "</style>" in content
    assert 'name="viewport"' in content
    assert 'name="color-scheme"' in content


def test_render_self_contained(tmp_path):
    """No external CSS, JS, or image references."""
    out = tmp_path / "test.html"
    render_magazine_html(_make_magazine(), out)
    content = out.read_text()

    assert '<link rel="stylesheet"' not in content
    assert "<script src" not in content
    assert '<img src="http' not in content


def test_render_has_collapsible_sections(tmp_path):
    """Sections are rendered as <details>/<summary> elements."""
    out = tmp_path / "test.html"
    render_magazine_html(_make_magazine(), out)
    content = out.read_text()

    assert "<details" in content
    assert "<summary" in content
    assert 'data-section="research"' in content
    assert 'data-section="tech"' in content


def test_first_section_expanded(tmp_path):
    """First mag-section has 'open' attribute, second does not."""
    out = tmp_path / "test.html"
    render_magazine_html(_make_magazine(), out)
    content = out.read_text()

    import re
    mag_sections = re.findall(r'<details class="mag-section"[^>]*>', content)
    assert len(mag_sections) >= 2
    assert "open" in mag_sections[0]
    assert "open" not in mag_sections[1]


# ── Security Tests ───────────────────────────────────────────────────────────


def test_xss_in_title_escaped(tmp_path):
    """Titles containing script tags are escaped."""
    magazine = _make_magazine()
    magazine["sections"][0]["items"][0]["title"] = '<script>alert("xss")</script>'
    out = tmp_path / "test.html"
    render_magazine_html(magazine, out)
    content = out.read_text()

    assert "<script>alert" not in content
    assert "&lt;script&gt;" in content


def test_xss_in_url_escaped(tmp_path):
    """URLs with attribute breakout are escaped."""
    magazine = _make_magazine()
    magazine["sections"][0]["items"][0]["url"] = 'https://evil.com" onclick="alert(1)" x="'
    out = tmp_path / "test.html"
    render_magazine_html(magazine, out)
    content = out.read_text()

    assert 'onclick="alert' not in content
    assert "&quot;" in content


def test_javascript_url_rejected(tmp_path):
    """javascript: URLs are not rendered as href links."""
    magazine = _make_magazine()
    magazine["sections"][0]["items"][0]["url"] = "javascript:alert(document.cookie)"
    out = tmp_path / "test.html"
    render_magazine_html(magazine, out)
    content = out.read_text()

    assert "javascript:" not in content


def test_xss_in_editorial_brief_escaped(tmp_path):
    """Editorial brief with script tags is escaped."""
    magazine = _make_magazine(editorial_brief='<img src=x onerror="alert(1)">')
    out = tmp_path / "test.html"
    render_magazine_html(magazine, out)
    content = out.read_text()

    assert 'onerror="alert' not in content
    assert "&lt;img" in content


# ── Escaping Helper Tests ────────────────────────────────────────────────────


def test_safe_text_escapes_html():
    assert _safe_text("<b>bold</b>") == "&lt;b&gt;bold&lt;/b&gt;"


def test_safe_link_escapes_both():
    result = _safe_link("https://example.com", "<script>evil</script>")
    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    assert 'href="https://example.com"' in result


def test_safe_link_rejects_javascript():
    result = _safe_link("javascript:alert(1)", "Click me")
    assert "javascript:" not in result
    assert result == "Click me"


def test_safe_url_rejects_non_http():
    assert _safe_url("javascript:alert(1)") == ""
    assert _safe_url("data:text/html,<h1>hi</h1>") == ""
    assert _safe_url("https://example.com") == "https://example.com"
    assert _safe_url("http://example.com") == "http://example.com"


def test_safe_attr_escapes_quotes():
    assert _safe_attr('value" onclick="alert(1)') == "value&quot; onclick=&quot;alert(1)"
