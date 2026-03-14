"""Tests for NLM report parsing in daily_feed_generator.py."""

import pytest
from pathlib import Path
from daily_feed_generator import parse_nlm_report, _parse_nlm_report_v3


@pytest.fixture
def v3_report_text():
    """Load sample v3 report from fixtures."""
    fixture = Path(__file__).parent / "fixtures" / "sample_report_v3.md"
    return fixture.read_text()


class TestV3ReportParsing:
    def test_headline_extracted(self, v3_report_text):
        result = _parse_nlm_report_v3(v3_report_text)
        assert result["headline"]
        assert "reasoning" in result["headline"].lower() or "advances" in result["headline"].lower()

    def test_sections_extracted(self, v3_report_text):
        result = _parse_nlm_report_v3(v3_report_text)
        section_names = [s["name"] for s in result["sections"]]
        assert "AI Research" in section_names
        assert "Tech Industry" in section_names
        assert "Development" in section_names
        assert "News" in section_names

    def test_articles_extracted(self, v3_report_text):
        result = _parse_nlm_report_v3(v3_report_text)
        ai_section = next(s for s in result["sections"] if s["name"] == "AI Research")
        assert len(ai_section["articles"]) == 2
        assert ai_section["articles"][0]["title"] == "Chain-of-Thought Reasoning at Scale"

    def test_source_urls_extracted(self, v3_report_text):
        result = _parse_nlm_report_v3(v3_report_text)
        ai_section = next(s for s in result["sections"] if s["name"] == "AI Research")
        assert ai_section["articles"][0]["url"] == "https://arxiv.org/abs/2602.12345"

    def test_tldr_extracted(self, v3_report_text):
        result = _parse_nlm_report_v3(v3_report_text)
        ai_section = next(s for s in result["sections"] if s["name"] == "AI Research")
        assert "chain-of-thought" in ai_section["articles"][0]["tldr"].lower()

    def test_takeaways_extracted(self, v3_report_text):
        result = _parse_nlm_report_v3(v3_report_text)
        assert len(result["takeaways"]) == 3
        assert "reasoning" in result["takeaways"][0].lower()

    def test_url_map_populated(self, v3_report_text):
        result = _parse_nlm_report_v3(v3_report_text)
        assert len(result["url_map"]) > 0

    def test_body_text_extracted(self, v3_report_text):
        result = _parse_nlm_report_v3(v3_report_text)
        ai_section = next(s for s in result["sections"] if s["name"] == "AI Research")
        # Body should contain the paragraph after TL;DR
        assert ai_section["articles"][0]["body"]


class TestV3MissingFields:
    def test_missing_tldr(self):
        report = """# Test Report

## AI Research

### Paper Without TL;DR

**Source:** https://arxiv.org/abs/2301.99999

This paper explores something interesting but has no TL;DR line.
"""
        result = _parse_nlm_report_v3(report)
        ai_section = next(s for s in result["sections"] if s["name"] == "AI Research")
        assert len(ai_section["articles"]) == 1
        # Should still have extracted something as body
        assert ai_section["articles"][0]["body"] or ai_section["articles"][0]["tldr"]

    def test_missing_source_url(self):
        report = """# Test Report

## AI Research

### Paper Without Source

**TL;DR:** This is an interesting paper.

Some analysis text.
"""
        result = _parse_nlm_report_v3(report)
        ai_section = next(s for s in result["sections"] if s["name"] == "AI Research")
        assert len(ai_section["articles"]) == 1
        assert ai_section["articles"][0]["url"] == ""

    def test_unexpected_heading_levels(self):
        report = """# Main Title

## Section One

#### Deep Nested Heading

**Source:** https://example.com

**TL;DR:** Summary here.

## Section Two

### Normal Article

**Source:** https://example.com/2

**TL;DR:** Another summary.
"""
        result = _parse_nlm_report_v3(report)
        assert len(result["sections"]) >= 2


class TestAutoDetection:
    def test_v3_detection(self, v3_report_text):
        result = parse_nlm_report(v3_report_text)
        assert result is not None
        assert len(result["sections"]) > 0

    def test_non_markdown_input(self):
        # Non-markdown input (e.g. XML) returns empty sections (v2 XML no longer supported)
        v2_text = """<BRIEFING>
<HEADLINE>Test Headline</HEADLINE>
<SECTION name="AI Research">
<ARTICLE><TITLE>Test</TITLE><SOURCE>https://example.com</SOURCE></ARTICLE>
</SECTION>
</BRIEFING>"""
        result = parse_nlm_report(v2_text)
        assert result is not None
        # v2 XML is no longer parsed — returns structure but no sections extracted
        assert isinstance(result["sections"], list)

    def test_empty_input(self):
        result = parse_nlm_report("")
        assert result is not None
        assert result["sections"] == []

    def test_none_input(self):
        result = parse_nlm_report(None)
        assert result is not None
        assert result["sections"] == []

    def test_whitespace_only(self):
        result = parse_nlm_report("   \n\n  ")
        assert result["sections"] == []
