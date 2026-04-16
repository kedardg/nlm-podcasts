#!/usr/bin/env python3
"""Hybrid content classifier: domain rules + local LLM.

Classifies items as research/news/blog to determine pipeline treatment:
- research: full NLM deep-dive (notebooks, podcasts, reports)
- news: 2-3 line briefs + links
- blog: 2-3 line briefs + links (tutorial/how-to content)

Fast path: domain-based rules handle ~60% of items instantly.
Slow path: local Qwen2.5-7B-Instruct via mlx-lm for uncertain items.

Usage:
    from lib.classifier import classify, classify_batch, ContentType

    result = classify("https://arxiv.org/abs/2503.01234", "Paper Title")
    results = classify_batch(items)  # items = [{"url": ..., "title": ...}, ...]
"""

import json
import re
from dataclasses import dataclass, asdict
from enum import Enum
from urllib.parse import urlparse

try:
    import llm_tracer as _llm_tracer
    _LLM_TRACER = True
except ImportError:
    _LLM_TRACER = False


class _NullCtx:
    def __enter__(self): return self
    def __exit__(self, *_): pass


class ContentType(str, Enum):
    research = "research"
    news = "news"
    blog = "blog"


@dataclass
class Classification:
    content_type: ContentType
    confidence: float
    method: str  # "domain_rule", "title_heuristic", or "llm"
    reasoning: str

    def to_dict(self):
        d = asdict(self)
        d["content_type"] = self.content_type.value
        return d


# ── Domain rules (fast path) ──

RESEARCH_DOMAINS = {
    "arxiv.org", "openreview.net", "paperswithcode.com",
    "proceedings.mlr.press", "jmlr.org",
    "acm.org", "dl.acm.org",
    "ieee.org", "ieeexplore.ieee.org",
    "nature.com", "science.org", "sciencedirect.com",
    "scholar.google.com",
    "machinelearning.apple.com",
    "ai.meta.com",
    "research.google", "deepmind.google",
    "huggingface.co",
    "lesswrong.com",
}

NEWS_DOMAINS = {
    "techcrunch.com", "theverge.com", "reuters.com", "bloomberg.com",
    "arstechnica.com", "wired.com", "cnbc.com",
    "venturebeat.com", "zdnet.com", "engadget.com",
    "thenextweb.com", "siliconangle.com",
    "apnews.com", "bbc.com", "bbc.co.uk",
    "theinformation.com", "axios.com", "wsj.com", "ft.com",
    "testingcatalog.com",
}

BLOG_DOMAINS = {
    "medium.com", "dev.to", "hashnode.dev",
    "substack.com", "beehiiv.com",
    "theneuron.ai", "rundown.ai",
    "aiadopters.club",
    "lastweekin.ai", "superhuman.ai",
    "latent.space",
}

# Company domains that publish both research and news
COMPANY_RESEARCH_PATHS = {
    "openai.com": ["/research", "/index/"],
    "anthropic.com": ["/research", "/news/core-views"],
    "google.com": ["/research"],
    "microsoft.com": ["/research"],
}

COMPANY_BLOG_PATHS = {
    "openai.com": ["/blog"],
    "anthropic.com": ["/news", "/engineering"],
    "google.com": ["/blog"],
    "microsoft.com": ["/blog"],
    "blog.google": ["/"],  # blog.google is always blog content
}

# Newsletter aggregator domains (low value — classify as news)
AGGREGATOR_DOMAINS = {
    "aiweekly.co", "app.therundown.ai",
}

# Product/tool domains (not research — classify as blog/news)
PRODUCT_DOMAINS = {
    "claude.com", "support.claude.com",
    "developers.openai.com",
    "perplexity.ai",
    "cursor.com", "you.com",
    "vercel.com",
}


def _extract_domain(url: str) -> str:
    """Extract domain from URL, stripping www. prefix."""
    parsed = urlparse(url)
    domain = parsed.hostname or ""
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _extract_path(url: str) -> str:
    """Extract path from URL."""
    parsed = urlparse(url)
    return parsed.path or ""


def classify_by_domain(url: str) -> Classification | None:
    """Fast classification by URL domain. Returns None if uncertain."""
    domain = _extract_domain(url)
    path = _extract_path(url)

    if not domain:
        return None

    # Check both exact domain and parent domain (e.g., newsletter.substack.com → substack.com)
    parts = domain.split(".")
    parent_domain = ".".join(parts[-2:]) if len(parts) > 2 else domain

    # Exact domain matches (check both domain and parent)
    if domain in RESEARCH_DOMAINS or parent_domain in RESEARCH_DOMAINS:
        return Classification(ContentType.research, 0.95, "domain_rule",
                              f"Domain {domain} is a known research venue")
    if domain in NEWS_DOMAINS or parent_domain in NEWS_DOMAINS:
        return Classification(ContentType.news, 0.90, "domain_rule",
                              f"Domain {domain} is a known news outlet")
    if domain in BLOG_DOMAINS or parent_domain in BLOG_DOMAINS:
        return Classification(ContentType.blog, 0.85, "domain_rule",
                              f"Domain {domain} is a known blog/newsletter platform")
    if domain in AGGREGATOR_DOMAINS or parent_domain in AGGREGATOR_DOMAINS:
        return Classification(ContentType.news, 0.80, "domain_rule",
                              f"Domain {domain} is a newsletter aggregator")
    if domain in PRODUCT_DOMAINS or parent_domain in PRODUCT_DOMAINS:
        return Classification(ContentType.blog, 0.80, "domain_rule",
                              f"Domain {domain} is a product/tool site")

    # Company domains with path-based classification
    for company_domain, research_paths in COMPANY_RESEARCH_PATHS.items():
        if domain == company_domain or domain.endswith("." + company_domain):
            for rp in research_paths:
                if path.startswith(rp):
                    return Classification(ContentType.research, 0.90, "domain_rule",
                                          f"{domain}{rp} is a research section")

    for company_domain, blog_paths in COMPANY_BLOG_PATHS.items():
        if domain == company_domain or domain.endswith("." + company_domain):
            for bp in blog_paths:
                if path.startswith(bp):
                    return Classification(ContentType.blog, 0.85, "domain_rule",
                                          f"{domain}{bp} is a blog section")

    # GitHub: could be research repo, tool, or random — uncertain
    if domain == "github.com":
        return Classification(ContentType.research, 0.65, "domain_rule",
                              "GitHub — likely research/OSS repo")

    # YouTube: highly uncertain — could be tutorial, lecture, or entertainment
    if domain in ("youtube.com", "youtu.be"):
        return None  # Let title heuristics or LLM decide

    # Reddit: uncertain
    if domain == "reddit.com" or domain.endswith(".reddit.com"):
        return None

    return None


# ── Title heuristics (medium path) ──

RESEARCH_TITLE_PATTERNS = [
    r"\barxiv\b",
    r"\bpaper\b",
    r"\bmodel\b.*\btraining\b",
    r"\bstate[- ]of[- ]the[- ]art\b",
    r"\bbenchmark\b",
    r"\bdataset\b",
    r"\barchitecture\b",
    r"\bpre[- ]?train",
    r"\bfine[- ]?tun",
    r"\bRLHF\b",
    r"\breinforcement learning\b",
    r"\btransformer\b",
    r"\bdiffusion\b",
]

NEWS_TITLE_PATTERNS = [
    r"\braises?\b.*\$",
    r"\bacquir",
    r"\blaunch",
    r"\bpartner",
    r"\$\d+[BMK]?\b",
    r"\bvaluation\b",
    r"\bIPO\b",
    r"\bregulat",
    r"\bban\b",
    r"\bshutdown\b",
]

BLOG_TITLE_PATTERNS = [
    r"\bhow to\b",
    r"\btutorial\b",
    r"\bguide\b",
    r"\bcheat sheet\b",
    r"\btips?\b.*\bfor\b",
    r"\bintroduction to\b",
    r"\bgetting started\b",
    r"\bbuild(?:ing)?\b.*\bwith\b",
]

_research_re = [re.compile(p, re.IGNORECASE) for p in RESEARCH_TITLE_PATTERNS]
_news_re = [re.compile(p, re.IGNORECASE) for p in NEWS_TITLE_PATTERNS]
_blog_re = [re.compile(p, re.IGNORECASE) for p in BLOG_TITLE_PATTERNS]


def classify_by_title(title: str) -> Classification | None:
    """Classify by title keywords. Returns None if uncertain."""
    if not title:
        return None

    research_hits = sum(1 for p in _research_re if p.search(title))
    news_hits = sum(1 for p in _news_re if p.search(title))
    blog_hits = sum(1 for p in _blog_re if p.search(title))

    max_hits = max(research_hits, news_hits, blog_hits)
    if max_hits == 0:
        return None

    # Need at least 2 signal hits, or 1 strong hit with no competition
    if max_hits == 1 and (research_hits + news_hits + blog_hits) > 1:
        return None  # Ambiguous

    if research_hits >= news_hits and research_hits >= blog_hits and research_hits >= 1:
        conf = min(0.80, 0.65 + research_hits * 0.05)
        return Classification(ContentType.research, conf, "title_heuristic",
                              f"Title has {research_hits} research signal(s)")
    if news_hits >= research_hits and news_hits >= blog_hits and news_hits >= 1:
        conf = min(0.80, 0.65 + news_hits * 0.05)
        return Classification(ContentType.news, conf, "title_heuristic",
                              f"Title has {news_hits} news signal(s)")
    if blog_hits >= research_hits and blog_hits >= news_hits and blog_hits >= 1:
        conf = min(0.80, 0.65 + blog_hits * 0.05)
        return Classification(ContentType.blog, conf, "title_heuristic",
                              f"Title has {blog_hits} blog signal(s)")

    return None


# ── LLM classification (slow path) ──

# Module-level model cache (loaded once per process)
_model_cache = {"model": None, "tokenizer": None}

_FEW_SHOT_TEMPLATE = """You are a content classifier for an AI research newsletter pipeline.
Classify each item as exactly one of: research, news, or blog.

Definitions:
- research: Academic papers, technical reports, model releases with methodology, benchmark results, novel architectures
- news: Industry announcements, funding, acquisitions, product launches, regulatory updates, company news
- blog: Tutorials, how-to guides, opinion pieces, personal projects, cheat sheets, introductions

Examples:
Title: "Attention Is All You Need" | Domain: arxiv.org -> {{"type": "research", "confidence": 0.95}}
Title: "Google acquires AI startup DeepMind for $500M" | Domain: techcrunch.com -> {{"type": "news", "confidence": 0.95}}
Title: "How to Build a RAG Pipeline in 30 Minutes" | Domain: medium.com -> {{"type": "blog", "confidence": 0.90}}
Title: "LLaMA 3.1 Released: 405B Parameter Open Model" | Domain: ai.meta.com -> {{"type": "research", "confidence": 0.85}}
Title: "EU Parliament Votes to Regulate AI Systems" | Domain: reuters.com -> {{"type": "news", "confidence": 0.95}}
Title: "Getting Started with LangChain Agents" | Domain: dev.to -> {{"type": "blog", "confidence": 0.90}}

Now classify this item. Reply ONLY with a JSON object, no other text:
Title: "{title}" | Domain: {domain}"""


def load_model():
    """Load mlx-lm model. Cached after first call."""
    if _model_cache["model"] is not None:
        return _model_cache["model"], _model_cache["tokenizer"]

    try:
        from mlx_lm import load
        model, tokenizer = load("mlx-community/Qwen2.5-7B-Instruct-4bit")
        _model_cache["model"] = model
        _model_cache["tokenizer"] = tokenizer
        return model, tokenizer
    except ImportError:
        raise RuntimeError("mlx-lm not installed. Run: uv pip install mlx-lm")
    except Exception as e:
        raise RuntimeError(f"Failed to load model: {e}")


def unload_model():
    """Unload model to free memory."""
    _model_cache["model"] = None
    _model_cache["tokenizer"] = None


def classify_with_llm(title: str, url: str) -> Classification:
    """Classify using local Qwen2.5-7B-Instruct via mlx-lm."""
    from mlx_lm import generate

    model, tokenizer = load_model()
    domain = _extract_domain(url)

    prompt = _FEW_SHOT_TEMPLATE.format(title=title, domain=domain)
    messages = [{"role": "user", "content": prompt}]
    text = tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)

    with (_llm_tracer.stage("item_classification") if _LLM_TRACER else _NullCtx()):
        response = generate(model, tokenizer, prompt=text, max_tokens=80)

    # Parse JSON from response
    return _parse_llm_response(response, url)


def _parse_llm_response(response: str, url: str) -> Classification:
    """Parse JSON classification from LLM response."""
    # Try to extract JSON from response
    text = response.strip()

    # Handle potential markdown code blocks
    if "```" in text:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            text = match.group(1)

    # Find first JSON object
    match = re.search(r"\{[^}]+\}", text)
    if not match:
        return Classification(ContentType.news, 0.50, "llm",
                              f"Failed to parse LLM response: {text[:100]}")

    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return Classification(ContentType.news, 0.50, "llm",
                              f"Invalid JSON in response: {text[:100]}")

    content_type_str = data.get("type", "news").lower()
    confidence = float(data.get("confidence", 0.70))

    try:
        content_type = ContentType(content_type_str)
    except ValueError:
        content_type = ContentType.news
        confidence = 0.50

    reasoning = data.get("reasoning", f"LLM classified {_extract_domain(url)} content")
    return Classification(content_type, confidence, "llm", reasoning)


# ── Public API ──

def classify(url: str, title: str = "") -> Classification:
    """Hybrid classifier: domain rules → title heuristics → LLM.

    Args:
        url: Item URL
        title: Item title (optional but improves accuracy)

    Returns:
        Classification with content_type, confidence, method, reasoning
    """
    # Fast path: domain rules (~60% of items)
    result = classify_by_domain(url)
    if result is not None:
        return result

    # Medium path: title heuristics
    result = classify_by_title(title)
    if result is not None:
        return result

    # Slow path: LLM
    return classify_with_llm(title, url)


def classify_batch(items: list[dict], use_llm: bool = True,
                   progress_fn=None) -> list[dict]:
    """Classify a batch of items.

    Args:
        items: List of dicts with "url" and "title" keys
        use_llm: If False, only use domain rules + title heuristics (skip LLM)
        progress_fn: Optional callback(current, total) for progress reporting

    Returns:
        List of dicts: original item fields + "classification" dict
    """
    results = []
    llm_items = []

    # First pass: domain rules + title heuristics
    for i, item in enumerate(items):
        url = item.get("url", "")
        title = item.get("title", "")

        result = classify_by_domain(url)
        if result is None:
            result = classify_by_title(title)

        if result is not None:
            item_result = dict(item)
            item_result["classification"] = result.to_dict()
            results.append(item_result)
        else:
            llm_items.append((i, item))

        if progress_fn and (i + 1) % 50 == 0:
            progress_fn(i + 1, len(items))

    # Second pass: LLM for uncertain items
    if use_llm and llm_items:
        load_model()  # Pre-load once
        for idx, (orig_idx, item) in enumerate(llm_items):
            url = item.get("url", "")
            title = item.get("title", "")
            result = classify_with_llm(title, url)
            item_result = dict(item)
            item_result["classification"] = result.to_dict()
            results.append(item_result)

            if progress_fn and (idx + 1) % 10 == 0:
                progress_fn(len(items) - len(llm_items) + idx + 1, len(items))
    elif not use_llm and llm_items:
        # Default uncertain items to news
        for _, item in llm_items:
            item_result = dict(item)
            item_result["classification"] = Classification(
                ContentType.news, 0.50, "default",
                "No domain rule or title signal; defaulted to news"
            ).to_dict()
            results.append(item_result)

    return results
