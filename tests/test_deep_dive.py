"""Tests for the Deep Dive Review generator (pipeline/deep_dive_generator.py)."""

from lib.url_utils import normalize_url_deep
from pipeline.deep_dive_generator import (
    PaperCandidate,
    select_top_papers,
    meets_content_floor,
    explain_paper,
    build_plan,
    build_deepdive_markdown,
    _clean_explainer,
    _is_paper_url,
    _sig_rank,
)


def _url_map_entry(title, significance, tldr="A grounded summary."):
    return {"summary": tldr, "significance": significance,
            "tldr": tldr, "body": tldr, "title": title}


def _make_url_map(specs):
    """specs: list of (url, significance, title)."""
    return {normalize_url_deep(u): _url_map_entry(t, s) for (u, s, t) in specs}


def _smap(mapping):
    """Wrap {url: source_id} into the nlm_source_map shape {url: {source_id, title}}."""
    return {k: {"source_id": v, "title": ""} for k, v in mapping.items()}


# ── Paper detection ──────────────────────────────────────────────────────────

class TestIsPaperUrl:
    def test_arxiv_variants(self):
        for u in ("https://arxiv.org/abs/2605.07912",
                  "https://arxiv.org/pdf/2605.07912.pdf",
                  "https://arxiv.org/html/2605.07912v2"):
            assert _is_paper_url(normalize_url_deep(u)), u

    def test_research_domains(self):
        assert _is_paper_url("https://openreview.net/forum?id=abc")
        assert _is_paper_url("https://huggingface.co/papers/2605.01234")

    def test_non_papers(self):
        assert not _is_paper_url("https://github.com/org/tool")
        assert not _is_paper_url("https://techcrunch.com/x")
        assert not _is_paper_url("https://huggingface.co/models/foo")
        assert not _is_paper_url("")

    def test_sig_rank_ordering(self):
        assert _sig_rank("high") > _sig_rank("medium") > _sig_rank("low") > _sig_rank("")


# ── Selection ────────────────────────────────────────────────────────────────

class TestSelectTopPapers:
    def test_no_papers(self):
        url_map = _make_url_map([("https://github.com/a/b", "high", "Repo")])
        explainers, podcasts = select_top_papers(url_map, {}, {})
        assert explainers == [] and podcasts == []

    def test_single_high_paper_with_source(self):
        u = normalize_url_deep("https://arxiv.org/abs/2601.00001")
        url_map = _make_url_map([(u, "high", "Paper A")])
        explainers, podcasts = select_top_papers(url_map, _smap({u: "sid-a"}), {})
        assert len(explainers) == 1
        assert len(podcasts) == 1 and podcasts[0].source_id == "sid-a"

    def test_low_only_day_no_podcasts(self):
        url_map = _make_url_map([
            ("https://arxiv.org/abs/2601.00002", "low", "Low Paper 1"),
            ("https://arxiv.org/abs/2601.00003", "low", "Low Paper 2"),
        ])
        smap = _smap({k: f"sid-{i}" for i, k in enumerate(url_map)})
        explainers, podcasts = select_top_papers(url_map, smap, {})
        assert len(explainers) == 2
        assert podcasts == []  # low never gets a podcast

    def test_under_supply_not_padded(self):
        url_map = _make_url_map([
            ("https://arxiv.org/abs/2601.00004", "high", "P1"),
            ("https://arxiv.org/abs/2601.00005", "medium", "P2"),
        ])
        smap = _smap({k: f"sid-{i}" for i, k in enumerate(url_map)})
        explainers, podcasts = select_top_papers(url_map, smap, {}, max_podcast=3)
        assert len(explainers) == 2
        assert len(podcasts) == 2  # min(N, 3), never padded

    def test_over_supply_capped_at_three(self):
        specs = [(f"https://arxiv.org/abs/2601.1000{i}", "high", f"P{i}") for i in range(6)]
        url_map = _make_url_map(specs)
        smap = _smap({k: f"sid-{i}" for i, k in enumerate(url_map)})
        explainers, podcasts = select_top_papers(url_map, smap, {}, max_podcast=3)
        assert len(explainers) == 6
        assert len(podcasts) == 3

    def test_podcast_requires_source_id(self):
        u_with = normalize_url_deep("https://arxiv.org/abs/2601.00010")
        u_without = normalize_url_deep("https://arxiv.org/abs/2601.00011")
        url_map = _make_url_map([(u_with, "high", "Has source"),
                                 (u_without, "high", "No source")])
        explainers, podcasts = select_top_papers(url_map, _smap({u_with: "sid"}), {})
        assert len(explainers) == 2                      # both get explainers
        assert [p.source_id for p in podcasts] == ["sid"]  # only the scoped one gets a podcast

    def test_ranking_high_before_medium(self):
        url_map = _make_url_map([
            ("https://arxiv.org/abs/2601.00020", "medium", "Med"),
            ("https://arxiv.org/abs/2601.00021", "high", "High"),
        ])
        smap = _smap({k: "s" for k in url_map})
        explainers, _ = select_top_papers(url_map, smap, {})
        assert explainers[0].title == "High"

    def test_confidence_tiebreak(self):
        a = normalize_url_deep("https://arxiv.org/abs/2601.00030")
        b = normalize_url_deep("https://arxiv.org/abs/2601.00031")
        url_map = _make_url_map([(a, "high", "A"), (b, "high", "B")])
        explainers, _ = select_top_papers(url_map, {}, {a: 0.5, b: 0.9})
        assert explainers[0].url == b  # higher confidence first

    def test_deterministic_across_runs(self):
        specs = [(f"https://arxiv.org/abs/2601.200{i}", "high", f"P{i}") for i in range(4)]
        url_map = _make_url_map(specs)
        smap = _smap({k: f"s{i}" for i, k in enumerate(url_map)})
        r1 = [p.url for p in select_top_papers(url_map, smap, {})[0]]
        r2 = [p.url for p in select_top_papers(url_map, smap, {})[0]]
        assert r1 == r2  # stable order (url is the unique final tiebreak)


# ── url→source_id matching (normalization) ────────────────────────────────────

class TestSourceMatching:
    def test_arxiv_abs_pdf_equivalence(self):
        # report references the abs form; the source was added as the pdf form
        abs_key = normalize_url_deep("https://arxiv.org/abs/2605.07912")
        pdf_key = normalize_url_deep("https://arxiv.org/pdf/2605.07912.pdf")
        assert abs_key == pdf_key
        url_map = {abs_key: _url_map_entry("Paper", "high")}
        source_map = _smap({pdf_key: "sid-x"})
        _, podcasts = select_top_papers(url_map, source_map, {})
        assert podcasts[0].source_id == "sid-x"


# ── Content floor ─────────────────────────────────────────────────────────────

class TestContentFloor:
    def test_empty_and_short(self):
        assert not meets_content_floor("")
        assert not meets_content_floor("too short")

    def test_refusal_marker_in_opening(self):
        text = "I cannot answer this based on the sources. " + ("padding " * 100)
        assert not meets_content_floor(text)

    def test_substantial_passes(self):
        assert meets_content_floor("A real grounded explainer paragraph. " * 30)

    def test_limitations_mention_not_flagged(self):
        # "no information" deep in a long answer must not trip the opening-only check
        text = ("This paper introduces a strong method with clear results. " * 25
                + "A limitation is that there is no information on long-context behavior.")
        assert meets_content_floor(text)

    def test_technical_no_information_phrase_passes(self):
        # "no information" is a real ML phrase (info bottleneck) — not a refusal marker
        assert meets_content_floor("The encoder enforces no information leakage. " * 30)


class TestCleanExplainer:
    def test_strips_spaced_citations_spares_attached_quantities(self):
        c = _clean_explainer("The transformer [1] uses array[0] and x[12] of input [2,3].")
        assert "array[0]" in c and "x[12]" in c   # attached quantities spared
        assert "[1]" not in c and "[2,3]" not in c  # spaced citations stripped

    def test_strips_leading_h1_h2_titles_keeps_beats(self):
        c = _clean_explainer("# Paper Title\n## Subtitle\n### 1. The hook\nbody")
        assert c.startswith("### 1. The hook")  # h1/h2 stripped, ### beat kept

    def test_strips_code_fence(self):
        c = _clean_explainer("```markdown\n### 1. The hook\ntext\n```")
        assert c.startswith("### 1. The hook") and "```" not in c


# ── Explainer generation (mocked NLM) ─────────────────────────────────────────

class TestExplainPaper:
    def _paper(self, source_id="sid"):
        return PaperCandidate(url="https://arxiv.org/abs/2601.1", source_id=source_id,
                              title="Paper", significance="high", confidence=0.0,
                              summary="Fallback summary.")

    def test_good_nlm_output(self):
        good = "### 1. The hook\n" + ("A substantial grounded review. " * 40)
        body, method = explain_paper("nb", self._paper(), "explain {PAPER_TITLE}",
                                     query_fn=lambda *a, **k: good)
        assert method == "nlm"
        assert "substantial grounded review" in body

    def test_thin_output_falls_back_to_stub(self):
        body, method = explain_paper("nb", self._paper(), "explain {PAPER_TITLE}",
                                     query_fn=lambda *a, **k: "too short")
        assert method == "stub"
        assert "Fallback summary." in body

    def test_citations_and_leading_title_stripped(self):
        raw = "# Paper\n### 1. The hook\n" + ("Grounded text [1] with markers [2,3]. " * 40)
        body, method = explain_paper("nb", self._paper(), "p", query_fn=lambda *a, **k: raw)
        assert method == "nlm"
        assert " [1]" not in body and " [2,3]" not in body
        assert not body.startswith("# Paper")

    def test_query_scoped_to_source_id(self):
        captured = {}
        def fake_query(nb, q, source_ids=None, **k):
            captured["source_ids"] = source_ids
            return "### 1. The hook\n" + ("ok " * 300)
        explain_paper("nb", self._paper("sid-7"), "p", query_fn=fake_query)
        assert captured["source_ids"] == ["sid-7"]


# ── Assembly ──────────────────────────────────────────────────────────────────

class TestAssembly:
    def test_markdown_structure(self):
        p = PaperCandidate("https://arxiv.org/abs/2601.1", "sid", "My Paper",
                           "high", 0.0, "")
        md = build_deepdive_markdown("2026-06-28",
                                     [{"paper": p, "body": "### 1. The hook\nHi", "method": "nlm"}])
        assert "# Deep Dive Review — 2026-06-28" in md
        assert "## My Paper" in md
        assert "**Source:** https://arxiv.org/abs/2601.1" in md

    def test_plan_shape(self):
        p = PaperCandidate("https://arxiv.org/abs/2601.1", "sid-1", "My Paper",
                           "high", 0.0, "")
        plan = build_plan("2026-06-28", [p], "FOCUS TEMPLATE")
        assert plan["date"] == "2026-06-28"
        entry = plan["papers"][0]
        assert entry["source_id"] == "sid-1"
        assert entry["slug"] == "my-paper"
        assert "My Paper" in entry["focus_prompt"]
        assert "FOCUS TEMPLATE" in entry["focus_prompt"]
