"""Tests for magazine PDF redesign: top story selection, domain pills, HTML structure."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lib.pdf_renderer import (
    DOMAIN_COLOR_DEFAULT,
    _extract_domain,
    _render_front_page,
    _sig_bars_html,
    domain_pill_color,
)
from pipeline.magazine_generator import select_top_stories


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_item(
    item_id="src_001",
    title="Test Item",
    url="https://arxiv.org/abs/1234",
    content_type="research",
    confidence=0.9,
    item_type="featured",
    summary="A detailed summary.",
    significance="high",
    section="AI Research",
):
    return {
        "id": item_id,
        "title": title,
        "url": url,
        "content_type": content_type,
        "confidence": confidence,
        "type": item_type,
        "summary": summary,
        "significance": significance,
        "category": "ml",
        "section": section,
    }


def _make_section(name, items):
    return {"name": name, "introduction": "", "preview": "", "items": items}


def _make_magazine(editorial_brief="This week in AI research.", top_stories=None, sections=None):
    return {
        "title": "Weekly Research Digest — 2026-03-07",
        "date": "2026-03-07",
        "generated_at": "2026-03-07T10:00:00Z",
        "editorial_brief": editorial_brief,
        "top_stories": top_stories or [],
        "sections": sections or [],
        "stats": {"total": 3, "research": 2, "news": 1, "blog": 0},
    }


# ── select_top_stories ────────────────────────────────────────────────────────


class TestSelectTopStories:
    def test_returns_featured_first(self):
        sections = [
            _make_section("AI Research", [
                _make_item("src_001", item_type="featured", significance="high"),
                _make_item("src_002", url="https://github.com/x/y", item_type="standard"),
            ])
        ]
        top = select_top_stories(sections)
        assert top[0]["id"] == "src_001"
        assert top[0]["type"] == "featured"

    def test_fills_from_standard_when_few_featured(self):
        sections = [
            _make_section("AI Research", [
                _make_item("src_001", item_type="featured"),
                _make_item("src_002", url="https://arxiv.org/abs/5678", item_type="standard", confidence=0.85),
                _make_item("src_003", url="https://arxiv.org/abs/9999", item_type="standard", confidence=0.7),
            ])
        ]
        top = select_top_stories(sections, max_stories=3)
        ids = [t["id"] for t in top]
        assert "src_001" in ids  # featured
        assert "src_002" in ids  # highest-confidence standard

    def test_returns_empty_when_no_items(self):
        assert select_top_stories([]) == []
        assert select_top_stories([_make_section("AI Research", [])]) == []

    def test_caps_at_max_stories(self):
        items = [
            _make_item(f"src_{i:03d}", url=f"https://arxiv.org/abs/{i}", item_type="featured")
            for i in range(10)
        ]
        sections = [_make_section("AI Research", items)]
        top = select_top_stories(sections, max_stories=5)
        assert len(top) <= 5

    def test_all_items_in_one_section(self):
        items = [
            _make_item("src_001", item_type="featured", confidence=0.95),
            _make_item("src_002", url="https://arxiv.org/abs/2", item_type="featured", confidence=0.85),
        ]
        sections = [_make_section("AI Research", items)]
        top = select_top_stories(sections, max_stories=5)
        assert len(top) == 2

    def test_section_diversity_preferred(self):
        sections = [
            _make_section("AI Research", [
                _make_item("src_001", item_type="featured", confidence=0.95),
            ]),
            _make_section("Tech", [
                _make_item("src_002", url="https://openai.com/blog/x", item_type="featured", confidence=0.90, section="Tech"),
            ]),
        ]
        top = select_top_stories(sections, max_stories=5)
        sections_in_top = {t["section"] for t in top}
        assert "AI Research" in sections_in_top
        assert "Tech" in sections_in_top

    def test_at_most_one_non_research(self):
        items = [
            _make_item("src_001", content_type="news", item_type="brief", summary="Big news.", significance="low"),
            _make_item("src_002", url="https://news.com/y", content_type="news", item_type="brief", summary="More news.", significance="low"),
        ]
        sections = [_make_section("News", items)]
        top = select_top_stories(sections, max_stories=5)
        non_research = [t for t in top if t.get("content_type") != "research"]
        assert len(non_research) <= 1

    def test_no_duplicate_urls(self):
        url = "https://arxiv.org/abs/same"
        items = [
            _make_item("src_001", url=url, item_type="featured"),
            _make_item("src_002", url=url, item_type="standard"),
        ]
        sections = [_make_section("AI Research", items)]
        top = select_top_stories(sections, max_stories=5)
        urls = [t["url"] for t in top]
        assert len(urls) == len(set(urls))


# ── domain_pill_color ─────────────────────────────────────────────────────────


class TestDomainPillColor:
    def test_known_domains(self):
        assert domain_pill_color("https://arxiv.org/abs/1234") == "#dc2626"
        assert domain_pill_color("https://github.com/user/repo") == "#16a34a"
        assert domain_pill_color("https://openai.com/blog/gpt") == "#2563eb"
        assert domain_pill_color("https://huggingface.co/models") == "#d97706"
        assert domain_pill_color("https://substack.com/newsletter") == "#ca8a04"
        assert domain_pill_color("https://youtube.com/watch?v=abc") == "#dc2626"

    def test_www_prefix_stripped(self):
        assert domain_pill_color("https://www.github.com/user/repo") == "#16a34a"
        assert domain_pill_color("https://www.arxiv.org/abs/1234") == "#dc2626"

    def test_blog_subdomain(self):
        assert domain_pill_color("https://blog.openai.com/gpt-5") == "#2563eb"

    def test_unknown_domain_returns_gray(self):
        assert domain_pill_color("https://unknown-site.xyz/article") == DOMAIN_COLOR_DEFAULT
        assert domain_pill_color("https://medium.com/ai-blog") == DOMAIN_COLOR_DEFAULT

    def test_empty_url(self):
        result = domain_pill_color("")
        assert result == DOMAIN_COLOR_DEFAULT


# ── _extract_domain ───────────────────────────────────────────────────────────


class TestExtractDomain:
    def test_strips_www(self):
        assert _extract_domain("https://www.github.com/user/repo") == "github.com"

    def test_no_www(self):
        assert _extract_domain("https://arxiv.org/abs/1234") == "arxiv.org"

    def test_empty_url(self):
        assert _extract_domain("") == ""


# ── _sig_bars_html ────────────────────────────────────────────────────────────


class TestSigBarsHtml:
    def test_high_has_three_filled(self):
        html = _sig_bars_html("high")
        assert html.count("sig-bar--filled") == 3
        assert html.count("sig-bar--empty") == 0

    def test_medium_has_two_filled(self):
        html = _sig_bars_html("medium")
        assert html.count("sig-bar--filled") == 2
        assert html.count("sig-bar--empty") == 1

    def test_low_has_one_filled(self):
        html = _sig_bars_html("low")
        assert html.count("sig-bar--filled") == 1
        assert html.count("sig-bar--empty") == 2

    def test_unknown_defaults_to_medium(self):
        html = _sig_bars_html("unknown")
        assert html.count("sig-bar--filled") == 2


# ── _render_front_page ────────────────────────────────────────────────────────


class TestRenderFrontPage:
    def test_returns_empty_when_no_editorial_brief(self):
        magazine = _make_magazine(editorial_brief="")
        assert _render_front_page(magazine) == ""

    def test_front_page_contains_editorial_brief(self):
        magazine = _make_magazine(editorial_brief="This week saw major advances in LLM reasoning.")
        html = _render_front_page(magazine)
        assert "This week saw major advances in LLM reasoning." in html
        assert "mag-v2-editorial" in html

    def test_front_page_contains_title(self):
        magazine = _make_magazine(editorial_brief="Test brief.")
        html = _render_front_page(magazine)
        assert "Weekly Research Digest" in html

    def test_front_page_has_page_break_class(self):
        magazine = _make_magazine(editorial_brief="Brief text.")
        html = _render_front_page(magazine)
        assert "mag-v2-front-page" in html

    def test_top_stories_rendered(self):
        story = _make_item("src_001", title="Big Paper")
        magazine = _make_magazine(
            editorial_brief="Key theme.",
            top_stories=[story],
        )
        html = _render_front_page(magazine)
        assert "Big Paper" in html
        assert "mag-v2-top-story" in html

    def test_top_stories_empty_when_no_stories(self):
        magazine = _make_magazine(editorial_brief="Brief.", top_stories=[])
        html = _render_front_page(magazine)
        # Should not crash; top-stories block should not appear
        assert "mag-v2-top-story" not in html

    def test_navigator_shows_sections(self):
        section = _make_section("AI Research", [_make_item()])
        section["preview"] = "Focus on transformers."
        magazine = _make_magazine(
            editorial_brief="Brief.",
            sections=[section],
        )
        html = _render_front_page(magazine)
        assert "AI Research" in html
        assert "mag-v2-navigator" in html
        assert "Focus on transformers." in html

    def test_domain_pills_in_top_stories(self):
        story = _make_item("src_001", url="https://arxiv.org/abs/1234", title="ArXiv Paper")
        magazine = _make_magazine(
            editorial_brief="Brief.",
            top_stories=[story],
        )
        html = _render_front_page(magazine)
        # ArXiv pill should use red color
        assert "#dc2626" in html
        assert "mag-v2-pill" in html


# ── render_magazine_pdf HTML structure ────────────────────────────────────────


class TestRenderMagazinePdfHtml:
    """Test the HTML structure without rendering a real PDF."""

    def _get_html_body(self, magazine):
        """Extract HTML body from render_magazine_pdf by intercepting render_html_pdf."""
        import lib.pdf_renderer as renderer
        captured = []
        original = renderer.render_html_pdf

        def capture(html_body, *args, **kwargs):
            captured.append(html_body)
            return "/tmp/captured.pdf"

        renderer.render_html_pdf = capture
        try:
            renderer.render_magazine_pdf(magazine, "/tmp/test.pdf")
        finally:
            renderer.render_html_pdf = original

        return captured[0] if captured else ""

    def test_semantic_article_tags(self):
        section = _make_section("AI Research", [
            _make_item("src_001", item_type="featured"),
        ])
        magazine = _make_magazine(sections=[section])
        html = self._get_html_body(magazine)
        assert "<article" in html
        assert 'data-article-id="src_001"' in html
        assert 'data-content-type="research"' in html
        assert 'data-section="AI Research"' in html

    def test_front_page_omitted_without_editorial_brief(self):
        section = _make_section("AI Research", [_make_item()])
        magazine = _make_magazine(editorial_brief="", sections=[section])
        html = self._get_html_body(magazine)
        assert "mag-v2-front-page" not in html

    def test_front_page_present_with_editorial_brief(self):
        section = _make_section("AI Research", [_make_item()])
        magazine = _make_magazine(
            editorial_brief="This week's theme.",
            sections=[section],
        )
        html = self._get_html_body(magazine)
        assert "mag-v2-front-page" in html

    def test_significance_bars_on_featured(self):
        section = _make_section("AI Research", [
            _make_item("src_001", item_type="featured", significance="high"),
        ])
        magazine = _make_magazine(sections=[section])
        html = self._get_html_body(magazine)
        assert "mag-v2-sig-bars" in html
        assert "sig-bar--filled" in html

    def test_domain_pills_present(self):
        section = _make_section("AI Research", [
            _make_item("src_001", url="https://arxiv.org/abs/1234", item_type="featured"),
        ])
        magazine = _make_magazine(sections=[section])
        html = self._get_html_body(magazine)
        assert "mag-v2-pill" in html
        assert "#dc2626" in html  # arxiv red

    def test_reference_overflow_inside_container(self):
        items = [
            _make_item(f"src_{i:03d}", url=f"https://example.com/{i}", item_type="brief", summary="", content_type="news")
            for i in range(35)
        ]
        section = _make_section("News", items)
        magazine = _make_magazine(sections=[section])
        html = self._get_html_body(magazine)
        assert "+ 5 more" in html

    def test_old_magazine_json_backward_compat(self):
        """Magazine JSON without editorial_brief renders without front page."""
        magazine = {
            "title": "Old Format — 2026-01-01",
            "date": "2026-01-01",
            "generated_at": "2026-01-01T10:00:00Z",
            # No editorial_brief, no top_stories
            "sections": [
                {
                    "name": "AI Research",
                    "introduction": "",
                    # No preview field
                    "items": [
                        _make_item("src_001"),
                    ],
                }
            ],
            "stats": {"total": 1, "research": 1, "news": 0, "blog": 0},
        }
        html = self._get_html_body(magazine)
        assert "mag-v2-front-page" not in html
        assert "<article" in html  # items still render

    def test_magazine_title_from_json(self):
        """Title comes from magazine JSON, not hardcoded."""
        section = _make_section("AI Research", [_make_item()])
        magazine = _make_magazine(editorial_brief="", sections=[section])
        magazine["title"] = "Custom Title — 2026-03-07"
        html = self._get_html_body(magazine)
        assert "Custom Title" in html
        assert "Weekly Research Digest" not in html.split("mag-title")[1].split("</")[0]

    def test_content_type_badge_present(self):
        section = _make_section("AI Research", [
            _make_item("src_001", content_type="research", item_type="featured"),
        ])
        magazine = _make_magazine(sections=[section])
        html = self._get_html_body(magazine)
        assert "mag-v2-badge" in html
        assert "RESEARCH" in html
