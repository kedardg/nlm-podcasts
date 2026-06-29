#!/usr/bin/env python3
"""Generate per-paper "Deep Dive Review" explainers from the daily NLM notebook.

Selects the day's research papers from the parsed NLM report, writes an in-depth
review-grade explainer for each (grounded via `nlm notebook query --source-ids`),
renders one combined PDF, and emits a one-way plan naming the top papers that
should also get an individual podcast (Phase 2, consumed by the shell pipeline).

Pure content + selection + PDF; the per-paper podcast loop lives in the shell.

Usage:
    DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python3 \
        src/pipeline/deep_dive_generator.py \
        --date 2026-06-28 \
        --notebook-id <uuid> \
        --report-md data/daily-feed/2026-06-28-report.md
    # add --dry-run to preview without writing
"""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lib.file_utils import load_json, save_json, now_iso
from lib.log_utils import StepTimer
from lib.url_utils import normalize_url_deep, ARXIV_ID_PATTERN

DATA_DIR = PROJECT_ROOT / "data"
DAILY_FEED_DIR = DATA_DIR / "daily-feed"
TRIAGE_FILE = DATA_DIR / "triage-results.json"
EXPLAINER_PROMPT_FILE = PROJECT_ROOT / "config" / "prompts" / "deep-dive-explainer-prompt.txt"
PODCAST_FOCUS_FILE = PROJECT_ROOT / "config" / "prompts" / "deep-dive-podcast-focus.txt"
NLM = str(Path.home() / ".local" / "bin" / "nlm")

# Significance is a high|medium|low string (never an enum) — give it an explicit
# ordering so we never lexically compare ("high" < "low" is True, the opposite of intent).
SIGNIFICANCE_RANK = {"high": 3, "medium": 2, "low": 1}
MIN_PODCAST_RANK = SIGNIFICANCE_RANK["medium"]  # only medium+ papers get a podcast

# Domains whose links we treat as research papers (Phase 1). Paper-discussion
# detection (a blog post discussing a paper) is deferred to Phase 3.
PAPER_DOMAINS = {
    "arxiv.org", "openreview.net", "paperswithcode.com",
    "proceedings.mlr.press", "jmlr.org", "dl.acm.org", "ieeexplore.ieee.org",
}

# A whole-answer refusal usually appears up front and is short; we only flag it
# in the opening of the text so a legitimate "limitations" mention doesn't trip it.
# Answer-shaped refusal phrases only — avoid bare substrings ("no information",
# "i cannot") that legitimately appear in technical ML prose.
REFUSAL_MARKERS = (
    "cannot answer", "can't answer", "do not have enough", "don't have enough",
    "not enough information", "unable to answer", "i'm unable", "i am unable",
    "do not contain enough", "cannot provide an",
)


@dataclass(frozen=True)
class PaperCandidate:
    url: str            # canonical (normalize_url_deep applied)
    source_id: str | None
    title: str
    significance: str   # "high" | "medium" | "low"
    confidence: float
    summary: str        # report tldr/body, used for the fallback stub

    def slug(self) -> str:
        s = re.sub(r"[^a-z0-9]+", "-", self.title.lower()).strip("-")
        return (s or "paper")[:50]

    def to_dict(self) -> dict:
        return {
            "url": self.url, "source_id": self.source_id, "title": self.title,
            "significance": self.significance, "confidence": self.confidence,
            "slug": self.slug(),
        }


def _sig_rank(s: str) -> int:
    return SIGNIFICANCE_RANK.get((s or "").lower(), 0)


def _is_paper_url(url: str) -> bool:
    """True if the (already-normalized) URL is a research paper."""
    if not url:
        return False
    if ARXIV_ID_PATTERN.search(url):
        return True
    netloc = re.sub(r"^https?://(www\.)?", "", url).split("/")[0].lower()
    if netloc in PAPER_DOMAINS:
        return True
    # HuggingFace papers section only (not general HF blog/models)
    if netloc == "huggingface.co" and "/papers/" in url:
        return True
    return False


# ── NLM I/O (module-level so tests can monkeypatch) ────────────────────────────

def nlm_source_map(notebook_id: str) -> dict:
    """Build {normalize_url_deep(url): {"source_id", "title"}} from
    `nlm source list --json`. This is the AUTHORITATIVE paper set — the notebook's
    actual sources reliably carry the arXiv URLs even when the report drops its
    per-article **Source:** lines. Returns {} on any failure (callers degrade)."""
    if not notebook_id:
        return {}
    try:
        out = subprocess.run(
            [NLM, "source", "list", notebook_id, "--json"],
            capture_output=True, text=True, timeout=60,
        ).stdout
        data = json.loads(out)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return {}
    items = data if isinstance(data, list) else data.get("sources", [])
    out_map = {}
    for it in items:
        url = it.get("url")
        sid = it.get("id") or it.get("source_id")
        if url and sid:
            out_map[normalize_url_deep(url)] = {"source_id": sid, "title": it.get("title") or ""}
    return out_map


def nlm_query(notebook_id: str, question: str, source_ids: list[str] | None = None,
              timeout: float = 180.0) -> str:
    """Ask the notebook a question (optionally scoped to source_ids). Returns the
    answer text, or "" on failure. The CLI emits {"value": {"answer": ...}}."""
    cmd = [NLM, "notebook", "query", notebook_id, question, "--json",
           "--timeout", str(int(timeout))]
    if source_ids:
        cmd += ["--source-ids", ",".join(source_ids)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout + 30).stdout
        data = json.loads(out)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as e:
        # Transport/parse failure — distinguishable from an empty answer in logs so
        # an all-stub PDF (e.g. auth break) is diagnosable.
        print(f"WARN: nlm query failed: {e}", file=sys.stderr)
        return ""
    return (data.get("value", {}) or {}).get("answer", "") or ""


# ── Selection ──────────────────────────────────────────────────────────────────

def load_confidence_map() -> dict:
    """Best-effort {normalize_url_deep(url): confidence} from triage-results.json.
    Confidence is only a tiebreak; absence defaults to 0.0."""
    data = load_json(TRIAGE_FILE, default={})
    items = data.get("results", data.get("items", [])) if isinstance(data, dict) else []
    out = {}
    for it in items:
        url = it.get("url")
        conf = it.get("confidence")
        if url and isinstance(conf, (int, float)):
            out[normalize_url_deep(url)] = float(conf)
    return out


def select_top_papers(url_map: dict, source_map: dict, confidence_map: dict,
                      *, max_podcast: int = 3) -> tuple[list, list]:
    """Return (all_explainer_papers, podcast_papers).

    Paper set = the union of paper URLs in the notebook's source list (authoritative)
    and the report's url_map (enrichment). Each paper is enriched from the report
    where available (significance, title, analysis body); papers present only in the
    notebook default to "medium" significance so they still rank for a podcast.

    all_explainer_papers: every paper, ranked (PDF).
    podcast_papers: top `max_podcast` with significance >= medium AND a source_id
    (needed to scope `audio create --source-ids`). Never padded.
    """
    paper_urls = {u for u in source_map if _is_paper_url(u)} | {u for u in url_map if _is_paper_url(u)}

    candidates: list[PaperCandidate] = []
    for url in paper_urls:
        meta = url_map.get(url, {})
        src = source_map.get(url, {})
        title = (meta.get("title") or src.get("title") or url).strip()
        # Report significance when covered; "medium" floor otherwise (a paper the
        # report didn't rate is still presumably worth reviewing, and must not sort
        # below a notebook-only paper).
        significance = (meta.get("significance") or "medium").lower()
        candidates.append(PaperCandidate(
            url=url,
            source_id=src.get("source_id"),
            title=title,
            significance=significance,
            confidence=confidence_map.get(url, 0.0),
            summary=(meta.get("tldr") or meta.get("body") or "").strip(),
        ))

    # Deterministic order: significance band, then confidence desc, then url (unique).
    candidates.sort(key=lambda p: (-_sig_rank(p.significance), -p.confidence, p.url))

    podcast = [p for p in candidates
               if _sig_rank(p.significance) >= MIN_PODCAST_RANK and p.source_id][:max_podcast]
    return candidates, podcast


# ── Explainer generation ────────────────────────────────────────────────────────

def meets_content_floor(text: str, min_chars: int = 600) -> bool:
    """A real explainer, not a refusal/stub. Pure + parameterized."""
    if not text:
        return False
    non_ws = len(re.sub(r"\s+", "", text))
    if non_ws < min_chars:
        return False
    opening = text[:300].lower()
    if any(m in opening for m in REFUSAL_MARKERS):
        return False
    return True


def _clean_explainer(text: str) -> str:
    """Strip code fences, a leading title heading, and [n] citation markers."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    # Drop up to two leading h1/h2 title lines (the generator adds its own h2).
    # `(?!#)` guarantees we never strip the ### beat headings (1-2 hashes only).
    text = re.sub(r"\A(#{1,2}(?!#).*\n+){1,2}", "", text)
    # Remove space-separated citation markers ("...transformer [1].") while sparing
    # attached bracketed quantities (array[0], x[12]): citations have whitespace
    # before the bracket; quantities attach directly to a token.
    text = re.sub(r"(?<=\w)[ \t]\[\d+(?:,\s*\d+)*\](?=[\s.,;:)]|$)", "", text)
    return text.strip()


def _stub_explainer(paper: PaperCandidate) -> str:
    """Fallback when NLM returns thin/refusal output — never an empty template."""
    body = paper.summary or "_The notebook sources did not contain enough detail for a full review of this paper._"
    return f"{body}\n\n_(Limited information available from the notebook sources.)_"


def explain_paper(notebook_id: str, paper: PaperCandidate, prompt_template: str,
                  *, query_fn=nlm_query, min_chars: int = 600) -> tuple[str, str]:
    """Return (explainer_markdown_body, method). method ∈ {nlm, stub}."""
    question = prompt_template.replace("{PAPER_TITLE}", paper.title).replace("{PAPER_URL}", paper.url)
    source_ids = [paper.source_id] if paper.source_id else None
    text = _clean_explainer(query_fn(notebook_id, question, source_ids))
    if meets_content_floor(text, min_chars):
        return text, "nlm"
    return _stub_explainer(paper), "stub"


# ── Assembly ─────────────────────────────────────────────────────────────────────

def build_deepdive_markdown(date_str: str, entries: list[dict]) -> str:
    """entries: [{paper: PaperCandidate, body: str, method: str}]."""
    out = [f"# Deep Dive Review — {date_str}", "",
           "_In-depth, review-grade explainers of the day's research papers._", ""]
    for e in entries:
        p: PaperCandidate = e["paper"]
        out += [f"## {p.title}", f"**Source:** {p.url}", "", e["body"], ""]
    return "\n".join(out)


def build_plan(date_str: str, podcast_papers: list, focus_template: str) -> dict:
    papers = []
    seen_slugs: dict[str, int] = {}
    for p in podcast_papers:
        slug = p.slug()
        if slug in seen_slugs:  # dedup so per-paper podcast files never clobber
            seen_slugs[slug] += 1
            slug = f"{slug}-{seen_slugs[slug]}"
        else:
            seen_slugs[slug] = 1
        focus = f"{focus_template}\n\nThe paper is: {p.title} ({p.url})."
        papers.append({"source_id": p.source_id, "slug": slug,
                       "title": p.title, "focus_prompt": focus})
    return {"date": date_str, "generated_at": now_iso(), "papers": papers}


# ── Orchestration ────────────────────────────────────────────────────────────────

def run(*, date_str: str, notebook_id: str, report_md_path: str,
        output_dir: Path | None = None, max_podcast: int = 3,
        dry_run: bool = False) -> dict:
    if str(PROJECT_ROOT) not in sys.path:  # for daily_feed_generator import
        sys.path.insert(0, str(PROJECT_ROOT))
    from daily_feed_generator import parse_nlm_report

    out_dir = output_dir or DAILY_FEED_DIR
    report_path = Path(report_md_path)
    if not report_path.exists():
        print(f"ERROR: report not found: {report_path}", file=sys.stderr)
        return {"status": "no_report", "papers": 0}

    url_map = parse_nlm_report(report_path.read_text()).get("url_map", {})
    source_map = nlm_source_map(notebook_id)
    confidence_map = load_confidence_map()

    with StepTimer("deepdive-select"):
        explainer_papers, podcast_papers = select_top_papers(
            url_map, source_map, confidence_map, max_podcast=max_podcast)

    if not explainer_papers:
        print("No qualifying papers — Deep Dive skipped.")
        return {"status": "no_papers", "papers": 0, "podcasts": 0}

    print(f"Deep Dive: {len(explainer_papers)} paper(s), "
          f"{len(podcast_papers)} podcast(s). "
          f"scoped={sum(1 for p in explainer_papers if p.source_id)}/{len(explainer_papers)}")

    prompt_template = EXPLAINER_PROMPT_FILE.read_text()
    entries = []
    with StepTimer("deepdive-explain"):
        for p in explainer_papers:
            body, method = explain_paper(notebook_id, p, prompt_template)
            print(f"  [{method}] {p.significance:<6} {p.title[:60]}")
            entries.append({"paper": p, "body": body, "method": method})

    markdown = build_deepdive_markdown(date_str, entries)
    focus_template = PODCAST_FOCUS_FILE.read_text() if PODCAST_FOCUS_FILE.exists() else ""
    plan = build_plan(date_str, podcast_papers, focus_template)

    md_path = out_dir / f"{date_str}-deepdive.md"
    pdf_path = out_dir / f"{date_str}-deepdive.pdf"
    plan_path = out_dir / f"{date_str}-deepdive-plan.json"

    if dry_run:
        print(f"\n(Dry run) would write {md_path.name}, {pdf_path.name}, {plan_path.name}")
        return {"status": "dry_run", "papers": len(explainer_papers),
                "podcasts": len(podcast_papers)}

    md_path.write_text(markdown)
    save_json(plan_path, plan)
    with StepTimer("deepdive-pdf"):
        from lib.pdf_renderer import render_pdf
        render_pdf(markdown, str(pdf_path))

    print(f"Wrote {md_path.name}, {pdf_path.name}, {plan_path.name}")
    return {"status": "ok", "papers": len(explainer_papers),
            "podcasts": len(podcast_papers),
            "pdf": str(pdf_path), "plan": str(plan_path)}


def main():
    parser = argparse.ArgumentParser(description="Deep Dive Review generator")
    parser.add_argument("--date", required=True, help="Date YYYY-MM-DD")
    parser.add_argument("--notebook-id", default="", help="NLM notebook ID")
    parser.add_argument("--report-md", required=True, help="Path to NLM report markdown")
    parser.add_argument("--output-dir", help="Output dir (default: data/daily-feed)")
    parser.add_argument("--max-podcast", type=int, default=3, help="Max per-paper podcasts")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    result = run(
        date_str=args.date,
        notebook_id=args.notebook_id,
        report_md_path=args.report_md,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        max_podcast=args.max_podcast,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
