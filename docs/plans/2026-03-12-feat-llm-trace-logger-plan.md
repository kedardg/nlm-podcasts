---
title: "feat: LLM Trace Logger & SLM Dataset Curator"
type: feat
status: completed
date: 2026-03-12
origin: docs/brainstorms/2026-03-12-llm-trace-logger-brainstorm.md
---

# feat: LLM Trace Logger & SLM Dataset Curator (`llm-tracer`)

## Enhancement Summary

**Deepened on:** 2026-03-12
**Research agents:** repo-research-analyst, learnings-researcher, best-practices-researcher (SQLite/Streamlit), SpecFlow analyzer, frameworks landscape researcher

### Key Improvements Added

1. **App name sanitization** — `init()` must sanitize the app name to `[a-z0-9_-]` before using it as a filename. Raw names like `"openclaw research"` would corrupt the DB path.
2. **Schema version table** — add a `metadata` table (key/value) alongside `traces` so future schema changes can be detected and migrations applied rather than requiring a full DB wipe.
3. **mlx_lm signature fix** — `mlx_lm.generate(model, tokenizer, prompt, ...)` takes positional args; the wrapper must extract `prompt` from `args[2]` and model name from `args[1]` (the tokenizer object has no `.name`). Use `getattr(args[0], "model_type", "mlx-model")` for the model field.
4. **`st.cache_data(ttl=30)`** — wrap all `load_all_traces()` calls with 30-second TTL caching so the Streamlit UI doesn't hammer SQLite on every re-render. Pass all filter args as cache key parameters.
5. **Annotation auto-save** — "Save & Next" should write to DB immediately via `conn.execute(UPDATE ...)` + `conn.commit()` — no deferred queue. Also clear `st.cache_data` after a label write to force refresh.
6. **DPO pair preview** — Dataset Builder page should show side-by-side chosen/rejected previews for DPO pairs before export, not just a count.
7. **`busy_timeout` + WAL edge case** — WAL mode is safe for multiple readers + one writer, but `cached_statements=0` is still required (CPython bug #118172) even with WAL. Both settings are needed together.
8. **Fine-tuning target refined** — Qwen 2.5-7B-Instruct (current openclaw-research model) for classification tasks. Phi-4-mini for generation. Unsloth + TRL/QLoRA is the recommended training stack.

### SpecFlow Gaps Addressed

Seven gaps identified by SpecFlow analysis, all addressed in this plan:
- `init()` idempotency warning (✓ in Phase 1 — `UserWarning` + no-op on re-call)
- Async client patching (✓ in Phase 2 — `AsyncCompletions.create`, `AsyncEmbeddings.create`)
- Streaming captures (✓ in System-Wide Impact — `output_text="<streaming>"`, skipped by exporters)
- DPO pairing UX (✓ in Phase 3 — side-by-side preview in Dataset Builder)
- Schema migration path (✓ — `metadata` table with `schema_version`)
- App name sanitization (✓ — sanitize in `init()`)
- Dedup key for DPO pairs (✓ — `input_text` hash, not raw string comparison)

---

## Overview

Build a **new standalone Python project** at `/Volumes/ext/projects/llm-tracer` that automatically captures every LLM API call from any Python application, stores traces in a per-app SQLite database, and provides a Streamlit curation UI for annotating and exporting training datasets.

The immediate use case is openclaw-research (3 call surfaces: OpenAI GPT-4o for sponsor filtering + editorial briefs, `text-embedding-3-small` for RAG + semantic dedup, and local `Qwen2.5-7B` via mlx_lm for classification). The long-term goal is accumulating ~500–1000 labeled examples to fine-tune a SLM that replaces expensive GPT-4o calls at 1/20th the cost.

*(see brainstorm: docs/brainstorms/2026-03-12-llm-trace-logger-brainstorm.md)*

---

## Problem Statement

The openclaw-research pipeline makes ~50–200 LLM calls per day across 5 distinct call sites. Zero data is retained. Every classification decision, every editorial brief, every deduplication embedding is discarded after use. This data is the training signal for eventual SLM fine-tuning — and it's being thrown away.

---

## Proposed Solution

A local Python package that monkey-patches provider SDKs on `init()`, writes every call to SQLite, and surfaces the data in Streamlit for human curation and JSONL export.

```python
# In any application — 2 lines to instrument:
import llm_tracer
llm_tracer.init(app="openclaw-research")

# Optional: tag calls with pipeline stage for filtering
with llm_tracer.stage("sponsor_filter"):
    response = openai_client.chat.completions.create(...)
```

---

## Technical Approach

### Architecture

```
/Volumes/ext/projects/llm-tracer/
├── llm_tracer/
│   ├── __init__.py            # Public API: init(), stage(), disable(), status()
│   ├── core.py                # Global state, _TraceContext (threading.local), idempotency guard
│   ├── db.py                  # SQLite CRUD: open_db(), insert_trace(), query_traces(), update_label()
│   ├── schema.py              # Trace dataclass, cost estimator (per provider/model pricing table)
│   ├── exporters.py           # to_openai_chat(), to_alpaca(), to_dpo_pairs()
│   └── providers/
│       ├── __init__.py        # patch_all(), unpatch_all() registry
│       ├── openai_provider.py # Wraps sync + async chat + embeddings
│       ├── anthropic_provider.py  # Wraps sync + async messages
│       └── mlx_provider.py    # Wraps mlx_lm.generate()
├── ui/
│   ├── app.py                 # Streamlit entry point (st.navigation, page routing)
│   ├── pages/
│   │   ├── traces.py          # Filterable trace table
│   │   ├── detail.py          # Single trace view + annotation
│   │   ├── dataset.py         # Dataset builder + export
│   │   └── stats.py           # Cost/volume/label distribution stats
│   └── db_loader.py           # Aggregates ~/.llm-tracer/*.db into one view
├── tests/
│   ├── test_db.py
│   ├── test_providers.py
│   ├── test_exporters.py
│   └── conftest.py
├── pyproject.toml
└── README.md
```

### Phase 1: Project Scaffold + SQLite Layer

**Goal**: Working package with SQLite trace storage, no patching yet.

#### `pyproject.toml`

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "llm-tracer"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "wrapt>=1.16",
]

[project.optional-dependencies]
ui = ["streamlit>=1.36", "pandas>=2.2", "altair>=5"]
openai = ["openai>=1.0"]
anthropic = ["anthropic>=0.28"]
mlx = ["mlx-lm>=0.10"]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]

[project.scripts]
llm-tracer = "llm_tracer.cli:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

**Research insight:** `uv init --lib` generates pyproject.toml with hatchling automatically. Run it inside the project directory, then update dependencies in place — don't create the file manually.

#### `llm_tracer/db.py`

The SQLite layer uses WAL mode, thread-local connections, and a `busy_timeout` of 5 seconds. Includes a `metadata` table for schema versioning.

**Research insight (CPython #118172):** `cached_statements=0` is required regardless of WAL mode — they solve different problems. WAL prevents file-level corruption from concurrent writers; `cached_statements=0` prevents a CPython-specific bug where prepared statement caches become stale across thread boundaries in multi-threaded use.

**Research insight (WAL durability):** `PRAGMA synchronous = NORMAL` with WAL is safe — data is not lost on OS crash (only on power loss, which is acceptable for trace data). `synchronous = FULL` would halve write throughput for no practical gain in this use case.

```python
# llm_tracer/db.py
import sqlite3, threading, os
from pathlib import Path

_local = threading.local()

DB_DIR = Path.home() / ".llm-tracer"

SCHEMA_VERSION = 1

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    app TEXT NOT NULL,
    pipeline_stage TEXT,
    provider TEXT NOT NULL,         -- "openai" | "anthropic" | "local-mlx"
    model TEXT NOT NULL,
    operation TEXT NOT NULL,        -- "chat" | "embeddings" | "messages"
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    latency_ms INTEGER,
    cost_usd REAL,
    input_text TEXT,               -- JSON: list of messages or plain string
    output_text TEXT,              -- JSON: response content
    metadata TEXT,                 -- JSON: arbitrary extras
    label TEXT,                    -- "good" | "bad" | NULL
    label_note TEXT,
    in_dataset INTEGER DEFAULT 0,
    dataset_split TEXT             -- "train" | "val" | "test"
);
CREATE INDEX IF NOT EXISTS idx_app ON traces(app);
CREATE INDEX IF NOT EXISTS idx_ts ON traces(ts);
CREATE INDEX IF NOT EXISTS idx_stage ON traces(pipeline_stage);
CREATE INDEX IF NOT EXISTS idx_label ON traces(label);
CREATE INDEX IF NOT EXISTS idx_in_dataset ON traces(in_dataset);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

def _open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        check_same_thread=False,
        cached_statements=0,  # avoids CPython #118172 multi-thread bug
    )
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        PRAGMA busy_timeout = 5000;
        PRAGMA temp_store = MEMORY;
        PRAGMA mmap_size = 134217728;
        PRAGMA cache_size = -16000;
    """)
    conn.executescript(CREATE_TABLE)
    # Write schema version on first create (INSERT OR IGNORE = idempotent)
    conn.execute(
        "INSERT OR IGNORE INTO metadata (key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION))
    )
    conn.commit()
    return conn

def get_conn(app: str) -> sqlite3.Connection:
    """Thread-local connection for the given app."""
    key = f"conn_{app}"
    if not hasattr(_local, key):
        setattr(_local, key, _open_db(DB_DIR / f"{app}.db"))
    return getattr(_local, key)

def get_schema_version(app: str) -> int:
    conn = get_conn(app)
    row = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
    return int(row[0]) if row else 0
```

#### `llm_tracer/schema.py`

```python
# llm_tracer/schema.py
from dataclasses import dataclass, field
from typing import Optional
import uuid, time, json

# Cost table in USD per 1K tokens (input, output)
COST_TABLE = {
    "gpt-4o":                     (0.0025, 0.010),
    "gpt-4o-mini":                (0.00015, 0.0006),
    "gpt-4-turbo":                (0.010, 0.030),
    "text-embedding-3-small":     (0.00002, 0.0),
    "text-embedding-3-large":     (0.00013, 0.0),
    "claude-3-5-sonnet-20241022": (0.003, 0.015),
    "claude-3-haiku-20240307":    (0.00025, 0.00125),
}

@dataclass
class Trace:
    app: str
    provider: str
    model: str
    operation: str
    input_text: str         # JSON string
    output_text: str        # JSON string
    pipeline_stage: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    metadata: Optional[str] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    @property
    def cost_usd(self) -> Optional[float]:
        if self.prompt_tokens is None:
            return None
        rates = COST_TABLE.get(self.model)
        if not rates:
            return None
        in_rate, out_rate = rates
        return (self.prompt_tokens / 1000 * in_rate +
                (self.completion_tokens or 0) / 1000 * out_rate)
```

**Research insight:** Add `_sanitize_app_name()` in `core.py` to normalize the app name before it reaches the DB layer. The filename `~/.llm-tracer/<app>.db` breaks on spaces, slashes, or unicode.

### Phase 2: Provider Patching

Using `wrapt.wrap_function_wrapper` — the same approach used by OpenLLMetry and the official OpenTelemetry OpenAI instrumentation. This is the production-proven pattern for intercepting SDK calls.

**Research insight (wrapt module vs method patching):** `wrapt.wrap_function_wrapper(module, name, wrapper)` works for both module-level functions (`mlx_lm.generate`) and bound methods (`Completions.create`). For module-level functions the `instance` arg in the wrapper is `None`; for methods it's the instance. The distinction matters for how to extract the model name.

**Research insight (mlx_lm signature):** `mlx_lm.generate(model, tokenizer, prompt, ...)` has positional signature — `args[0]` is the model object (has `.model_type` attribute, not `.name`), `args[1]` is the tokenizer, `args[2]` is the prompt string. Always use `kwargs.get("prompt") or (args[2] if len(args) > 2 else "")` to handle both positional and keyword call styles.

#### `llm_tracer/providers/openai_provider.py`

Key design decisions:
- Patch both sync `Completions.create` and `AsyncCompletions.create`
- Patch both `Embeddings.create` and `AsyncEmbeddings.create`
- **Streaming**: If `stream=True`, log with `output_text="<streaming — not captured>"` and `completion_tokens=None`. Full streaming capture is deferred to v2.
- **SDK version**: Requires `openai>=1.0` (new client interface). Older `openai<1.0` is not supported.

```python
# llm_tracer/providers/openai_provider.py
import json, time
from wrapt import wrap_function_wrapper
from llm_tracer.core import _get_active_stage, _get_active_app, _write_trace

_patched = False

def _wrap_chat_sync(wrapped, instance, args, kwargs):
    app = _get_active_app()
    if not app:
        return wrapped(*args, **kwargs)
    stage = _get_active_stage()
    is_stream = kwargs.get("stream", False)
    start = time.monotonic()
    result = wrapped(*args, **kwargs)
    latency = int((time.monotonic() - start) * 1000)
    if is_stream:
        _write_trace(app=app, provider="openai", model=kwargs.get("model","?"),
                     operation="chat", pipeline_stage=stage,
                     input_text=json.dumps(kwargs.get("messages", [])),
                     output_text="<streaming>", latency_ms=latency)
        return result
    _write_trace(
        app=app, provider="openai", model=result.model,
        operation="chat", pipeline_stage=stage,
        input_text=json.dumps(kwargs.get("messages", [])),
        output_text=json.dumps(result.choices[0].message.content),
        prompt_tokens=result.usage.prompt_tokens,
        completion_tokens=result.usage.completion_tokens,
        latency_ms=latency,
    )
    return result

async def _wrap_chat_async(wrapped, instance, args, kwargs):
    app = _get_active_app()
    if not app:
        return await wrapped(*args, **kwargs)
    stage = _get_active_stage()
    start = time.monotonic()
    result = await wrapped(*args, **kwargs)
    latency = int((time.monotonic() - start) * 1000)
    _write_trace(
        app=app, provider="openai", model=result.model,
        operation="chat", pipeline_stage=stage,
        input_text=json.dumps(kwargs.get("messages", [])),
        output_text=json.dumps(result.choices[0].message.content),
        prompt_tokens=result.usage.prompt_tokens,
        completion_tokens=result.usage.completion_tokens,
        latency_ms=latency,
    )
    return result

def _wrap_embeddings_sync(wrapped, instance, args, kwargs):
    app = _get_active_app()
    if not app:
        return wrapped(*args, **kwargs)
    stage = _get_active_stage()
    start = time.monotonic()
    result = wrapped(*args, **kwargs)
    latency = int((time.monotonic() - start) * 1000)
    _write_trace(
        app=app, provider="openai", model=result.model,
        operation="embeddings", pipeline_stage=stage,
        input_text=json.dumps(kwargs.get("input", "")),
        output_text=json.dumps(f"<{len(result.data)} embeddings>"),
        prompt_tokens=result.usage.total_tokens,
        latency_ms=latency,
    )
    return result

def patch():
    global _patched
    if _patched:
        return
    wrap_function_wrapper("openai.resources.chat.completions", "Completions.create", _wrap_chat_sync)
    wrap_function_wrapper("openai.resources.chat.completions", "AsyncCompletions.create", _wrap_chat_async)
    wrap_function_wrapper("openai.resources.embeddings", "Embeddings.create", _wrap_embeddings_sync)
    # AsyncEmbeddings.create — same pattern as async chat
    _patched = True

def unpatch():
    global _patched
    # wrapt doesn't provide a clean unpatch; reset flag only
    _patched = False
```

#### `llm_tracer/providers/mlx_provider.py`

mlx_lm exposes `generate` as a module-level function. **Research insight:** The correct positional signature is `generate(model, tokenizer, prompt, ...)`. Model name should be extracted from `getattr(args[0], "model_type", "mlx-model")` — the model object has `.model_type` but not `.name`.

```python
# llm_tracer/providers/mlx_provider.py
import json, time
from wrapt import wrap_function_wrapper
from llm_tracer.core import _get_active_stage, _get_active_app, _write_trace

_patched = False

def _wrap_mlx_generate(wrapped, instance, args, kwargs):
    app = _get_active_app()
    if not app:
        return wrapped(*args, **kwargs)
    stage = _get_active_stage()
    # mlx_lm.generate(model, tokenizer, prompt, ...)
    # args[0]=model, args[1]=tokenizer, args[2]=prompt
    prompt = kwargs.get("prompt") or (args[2] if len(args) > 2 else "")
    model_name = getattr(args[0], "model_type", "mlx-model") if args else "mlx-model"
    start = time.monotonic()
    result = wrapped(*args, **kwargs)
    latency = int((time.monotonic() - start) * 1000)
    _write_trace(
        app=app, provider="local-mlx",
        model=model_name,
        operation="generate", pipeline_stage=stage,
        input_text=json.dumps(str(prompt)),
        output_text=json.dumps(str(result)),
        latency_ms=latency,
    )
    return result

def patch():
    global _patched
    if _patched:
        return
    try:
        wrap_function_wrapper("mlx_lm", "generate", _wrap_mlx_generate)
        _patched = True
    except (ImportError, AttributeError):
        pass  # mlx_lm not installed — silently skip

def unpatch():
    global _patched
    _patched = False
```

#### `llm_tracer/core.py`

The global state, idempotency guard, and app name sanitization:

```python
# llm_tracer/core.py
import re
import threading
import warnings
from llm_tracer.db import get_conn
from llm_tracer.schema import Trace

_state = {
    "app": None,
    "initialized": False,
    "disabled": False,
}
_stage_local = threading.local()

def _sanitize_app_name(app: str) -> str:
    """Normalize app name to safe filename: lowercase, only [a-z0-9_-]."""
    sanitized = app.lower().strip()
    sanitized = re.sub(r"[^a-z0-9_-]", "-", sanitized)
    sanitized = re.sub(r"-+", "-", sanitized).strip("-")
    if not sanitized:
        raise ValueError(f"App name '{app}' produces an empty sanitized name")
    return sanitized

def _get_active_app() -> str | None:
    if _state["disabled"] or not _state["initialized"]:
        return None
    return _state["app"]

def _get_active_stage() -> str | None:
    return getattr(_stage_local, "stack", [None])[-1]

def _write_trace(**kwargs):
    app = _state["app"]
    if not app or _state["disabled"]:
        return
    try:
        t = Trace(**kwargs)
        conn = get_conn(app)
        conn.execute(
            "INSERT INTO traces VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (t.id, t.ts, t.app, t.pipeline_stage, t.provider, t.model,
             t.operation, t.prompt_tokens, t.completion_tokens, t.latency_ms,
             t.cost_usd, t.input_text, t.output_text, t.metadata,
             None, None, 0, None)
        )
        conn.commit()
    except Exception:
        pass  # Never let tracing break the application

class _StageContext:
    def __init__(self, name: str):
        self.name = name
    def __enter__(self):
        if not hasattr(_stage_local, "stack"):
            _stage_local.stack = []
        _stage_local.stack.append(self.name)
        return self
    def __exit__(self, *_):
        if hasattr(_stage_local, "stack") and _stage_local.stack:
            _stage_local.stack.pop()
```

#### `llm_tracer/__init__.py` — Public API

```python
# llm_tracer/__init__.py
import warnings
from llm_tracer.core import _state, _StageContext, _sanitize_app_name
from llm_tracer.providers import patch_all

def init(app: str, *, disabled: bool = False) -> None:
    """Initialize llm-tracer. Call once at application startup.

    Args:
        app: Application name. Used as the SQLite DB filename (~/.llm-tracer/<app>.db).
             Sanitized to [a-z0-9_-] automatically.
        disabled: If True, tracing is a no-op. Useful for tests.
    """
    if _state["initialized"]:
        warnings.warn(
            f"llm_tracer.init() called again (app='{app}'). "
            "Already initialized — this call is a no-op. "
            "Call llm_tracer.reset() first if you need to reinitialize.",
            stacklevel=2,
        )
        return
    sanitized = _sanitize_app_name(app)
    _state["app"] = sanitized
    _state["disabled"] = disabled
    _state["initialized"] = True
    if not disabled:
        patch_all()

def stage(name: str) -> _StageContext:
    """Context manager to tag all LLM calls within the block with pipeline_stage=name.

    Example:
        with llm_tracer.stage("sponsor_filter"):
            response = openai_client.chat.completions.create(...)
    """
    return _StageContext(name)

def disable() -> None:
    """Disable tracing for the current process (no-op all subsequent captures)."""
    _state["disabled"] = True

def reset() -> None:
    """Reset state. For testing only."""
    from llm_tracer.providers import unpatch_all
    _state["app"] = None
    _state["initialized"] = False
    _state["disabled"] = False
    unpatch_all()

def status() -> dict:
    """Return current tracer state summary."""
    from llm_tracer.db import DB_DIR
    import glob
    dbs = list(glob.glob(str(DB_DIR / "*.db")))
    return {
        "initialized": _state["initialized"],
        "app": _state["app"],
        "disabled": _state["disabled"],
        "db_files": dbs,
    }
```

### Phase 3: Streamlit UI

The UI uses Streamlit's `st.navigation` (available since Streamlit 1.36) for multi-page routing without file-based pages. It reads from all `~/.llm-tracer/*.db` files via `ui/db_loader.py`.

**Research insight (`st.cache_data`):** Wrap `load_all_traces()` calls with `@st.cache_data(ttl=30)`. Pass all filter args as parameters so Streamlit generates a distinct cache key per filter combination. After any label write, call `load_all_traces.clear()` to bust the cache and show the updated annotation immediately.

**Research insight (`st.dataframe`):** Use `st.dataframe(df, use_container_width=True, hide_index=True)` — the `use_container_width` flag makes tables responsive to sidebar width changes without manual column width math.

#### `ui/db_loader.py`

```python
# ui/db_loader.py
import sqlite3, glob, pandas as pd
import streamlit as st
from pathlib import Path
from llm_tracer.db import DB_DIR, _open_db

@st.cache_data(ttl=30)
def load_all_traces(
    app: str | None = None,
    pipeline_stage: str | None = None,
    label: str | None = None,
    in_dataset: bool | None = None,
) -> pd.DataFrame:
    """Aggregate traces from all ~/.llm-tracer/*.db files into one DataFrame.

    All filter args are cache key parameters — change any filter to get a fresh query.
    Call load_all_traces.clear() after a label write to bust the cache immediately.
    """
    dfs = []
    for db_path in sorted(DB_DIR.glob("*.db")):
        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            df = pd.read_sql("SELECT * FROM traces ORDER BY ts DESC", conn)
            dfs.append(df)
        except Exception:
            pass
    if not dfs:
        return pd.DataFrame()
    combined = pd.concat(dfs, ignore_index=True)
    if app:
        combined = combined[combined["app"] == app]
    if pipeline_stage:
        combined = combined[combined["pipeline_stage"] == pipeline_stage]
    if label:
        combined = combined[combined["label"] == label]
    if in_dataset is not None:
        combined = combined[combined["in_dataset"] == int(in_dataset)]
    return combined.sort_values("ts", ascending=False)
```

#### UI Pages

**`ui/pages/traces.py`** — Filterable table
- Sidebar: app filter, pipeline_stage filter, model filter, date range, label filter (all/good/bad/unlabeled)
- Main: `st.dataframe(df, use_container_width=True, hide_index=True)` with columns: ts, app, pipeline_stage, model, operation, prompt_tokens, completion_tokens, cost_usd, label, in_dataset
- Click row → navigate to detail page with trace ID in session state
- Pagination: show 100 rows at a time, total count in header

**`ui/pages/detail.py`** — Trace Detail + Annotation
- Renders `input_text` as a formatted conversation (system/user/assistant messages in styled boxes)
- Renders `output_text` with syntax highlighting
- Shows: model, provider, pipeline_stage, latency_ms, cost_usd, tokens
- Annotation controls: `st.radio` for label (good / bad / skip), `st.text_area` for note
- `in_dataset` checkbox + `dataset_split` selector (train/val/test)
- "Save & Next" button: writes label to DB immediately via `conn.execute(UPDATE ...)` + `conn.commit()`, calls `load_all_traces.clear()` to bust cache, advances to next unlabeled trace in current filter

**`ui/pages/dataset.py`** — Dataset Builder
- Left: filter panel (same as traces page) + "Add all filtered to dataset" button
- Middle: selected traces count, format selector (OpenAI chat / Alpaca / DPO pairs)
- **DPO pairs**: show side-by-side preview (chosen vs. rejected) for first 3 pairs before export. Show pair count and unpaired count separately. *(Research insight: DPO pairing UX gap from SpecFlow — side-by-side preview prevents exporting bad pairs without realizing it.)*
- Preview: first 5 items rendered as formatted JSONL
- Export: `st.download_button` for `.jsonl` file

**`ui/pages/stats.py`** — Stats
- Cost by app, by pipeline_stage, by model (bar charts via Altair)
- Volume by day (line chart)
- Label distribution (pie/donut)
- "SLM readiness": table showing `(pipeline_stage, labeled_count, target)` — target is 500 for classification, 1000 for generation. Color: green when ≥ target.

#### `ui/app.py` — Entry Point

```python
# ui/app.py
import streamlit as st
from ui.pages import traces, detail, dataset, stats

st.set_page_config(page_title="LLM Tracer", layout="wide", page_icon="🔍")

pages = st.navigation([
    st.Page(traces.render, title="Traces", icon="📋"),
    st.Page(detail.render, title="Trace Detail", icon="🔎"),
    st.Page(dataset.render, title="Dataset Builder", icon="📦"),
    st.Page(stats.render, title="Stats", icon="📊"),
])
pages.run()
```

Run with: `streamlit run /Volumes/ext/projects/llm-tracer/ui/app.py`

### Phase 4: Dataset Exporters

**Research insight (DPO dedup key):** `to_dpo_pairs()` groups by `input_text` raw string, but long prompts can have byte-identical content with whitespace differences. Use `hashlib.sha256(t["input_text"].encode()).hexdigest()` as the grouping key to normalize whitespace and avoid spurious mismatches. Store the original `input_text` from the first `good` trace as the DPO `prompt`.

#### `llm_tracer/exporters.py`

```python
# llm_tracer/exporters.py
import json, hashlib
from typing import Literal

ExportFormat = Literal["openai_chat", "alpaca", "dpo"]

def _to_messages(input_text: str) -> list[dict]:
    """Parse input_text (JSON list of messages or plain string) into messages list."""
    try:
        data = json.loads(input_text)
        if isinstance(data, list):
            return data
        return [{"role": "user", "content": str(data)}]
    except Exception:
        return [{"role": "user", "content": str(input_text)}]

def _to_output(output_text: str) -> str:
    try:
        data = json.loads(output_text)
        if isinstance(data, str):
            return data
        return json.dumps(data)
    except Exception:
        return str(output_text)

def to_openai_chat(traces: list[dict]) -> list[dict]:
    """Export as OpenAI fine-tuning chat format."""
    result = []
    for t in traces:
        if not t.get("output_text") or t["output_text"] == "<streaming>":
            continue
        messages = _to_messages(t["input_text"])
        messages.append({"role": "assistant", "content": _to_output(t["output_text"])})
        result.append({"messages": messages})
    return result

def to_alpaca(traces: list[dict]) -> list[dict]:
    """Export as Alpaca instruction-following format."""
    result = []
    for t in traces:
        messages = _to_messages(t["input_text"])
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user = next((m["content"] for m in messages if m["role"] == "user"), "")
        result.append({
            "instruction": system or "Complete the following:",
            "input": user,
            "output": _to_output(t["output_text"]),
        })
    return result

def to_dpo_pairs(traces: list[dict]) -> list[dict]:
    """Build DPO pairs: match good/bad traces with identical input_text (by SHA-256 hash)."""
    from collections import defaultdict
    # Group by content hash to normalize whitespace differences
    by_hash: dict[str, dict] = defaultdict(lambda: {"good": [], "bad": [], "input_text": None})
    for t in traces:
        if t.get("label") in ("good", "bad"):
            h = hashlib.sha256(t["input_text"].encode()).hexdigest()
            by_hash[h][t["label"]].append(t)
            if by_hash[h]["input_text"] is None:
                by_hash[h]["input_text"] = t["input_text"]
    pairs = []
    for bucket in by_hash.values():
        for good in bucket["good"]:
            for bad in bucket["bad"]:
                pairs.append({
                    "prompt": bucket["input_text"],
                    "chosen": _to_output(good["output_text"]),
                    "rejected": _to_output(bad["output_text"]),
                })
    return pairs

def export_jsonl(traces: list[dict], fmt: ExportFormat) -> str:
    """Return JSONL string for the given export format."""
    if fmt == "openai_chat":
        rows = to_openai_chat(traces)
    elif fmt == "alpaca":
        rows = to_alpaca(traces)
    elif fmt == "dpo":
        rows = to_dpo_pairs(traces)
    else:
        raise ValueError(f"Unknown format: {fmt}")
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
```

### Phase 5: Integration into openclaw-research

After the package is built and tested:

```bash
# In openclaw-research/.env (add):
# LLM_TRACER_ENABLED=true  (optional — init() with disabled=False is the default)

# In openclaw-research/pyproject.toml dependencies, add:
# llm-tracer = {path = "../llm-tracer", editable = true}
# uv add --editable ../llm-tracer
```

**Add to each call site** in openclaw-research:

| File | Where to add `init()` | Stages to add |
|---|---|---|
| `daily_feed_generator.py` | Top of `main()` | `"semantic_dedup"`, `"sponsor_filter"` |
| `scripts/magazine_generator.py` | Top of `generate_magazine()` | `"editorial_briefs"` |
| `rag/embeddings.py` | Top of `index_report()` | `"rag_index"` |
| `lib/classifier.py` | Top of `classify_with_llm()` | `"item_classification"` |

One `init()` call per process entry point. Since multiple modules share the same process when the pipeline runs, `init()` needs to be called once per pipeline script (in `daily_pipeline.sh` the Python entry point is `daily_feed_generator.py`, so it goes there).

---

## System-Wide Impact

### Initialization Safety

- `init()` sanitizes the app name to `[a-z0-9_-]` before use. `"openclaw research"` → `"openclaw-research"`.
- `init()` is idempotent: second call is a no-op with a `UserWarning`. This prevents double-patching (which would cause duplicate traces) when modules call `init()` and are themselves imported by other modules.
- `_state` is process-global. If `init()` is called from a test without cleanup, subsequent tests share the patched state. Fix: call `llm_tracer.reset()` in test teardown.

### Subprocess and Multiprocessing

openclaw-research's pipeline runs as sequential Python processes (no `multiprocessing`). WAL mode + `busy_timeout=5000` is sufficient for the case where multiple pipeline runs overlap. If two processes write to the same app DB simultaneously, SQLite's WAL mode handles concurrent reads, and the 5-second timeout handles write contention gracefully.

### Provider SDK Compatibility

| Provider | Min version | Notes |
|---|---|---|
| `openai` | >= 1.0.0 | New client (`openai.OpenAI()`). Older `openai<1.0` (`ChatCompletion.create`) is not supported. |
| `anthropic` | >= 0.28.0 | `anthropic.Anthropic()` client interface. |
| `mlx-lm` | >= 0.10.0 | Module-level `mlx_lm.generate(model, tokenizer, prompt, ...)`. Earlier versions had a different function signature. |

If a provider SDK is not installed, its `patch()` function silently skips (no error).

### Streaming Responses

Streaming (`stream=True`) calls are captured with `output_text="<streaming>"` and `completion_tokens=None`. Token counts and cost are unavailable for streaming traces. These traces are still visible in the UI and annotatable, but cannot be exported in OpenAI chat format (the exporter skips them). This is an acceptable v1 limitation — streaming is not used in openclaw-research's current call sites.

### DB Schema Migration

A `metadata` table stores the current `schema_version` integer. In v1 there is no automated migration tooling, but the version is present so future versions can detect "this DB was created with v1 schema" and apply `ALTER TABLE` statements. Document in README: "To upgrade schema in v1: check `SELECT value FROM metadata WHERE key='schema_version'` and apply any needed `ALTER TABLE` manually, then update the version value."

---

## Acceptance Criteria

### Core Instrumentation

- [ ] `llm_tracer.init(app="test-app")` + `openai.OpenAI().chat.completions.create(...)` writes a row to `~/.llm-tracer/test-app.db`
- [ ] `llm_tracer.init(app="openclaw research")` sanitizes to `"openclaw-research"` and creates `~/.llm-tracer/openclaw-research.db`
- [ ] Second call to `init()` issues `UserWarning` and does not re-patch (no duplicate traces)
- [ ] `with llm_tracer.stage("my-stage"):` correctly sets `pipeline_stage` on all traces inside the block
- [ ] Nested `stage()` contexts work: innermost stage wins
- [ ] `llm_tracer.disable()` → no traces written for subsequent calls
- [ ] OpenAI async client (`AsyncOpenAI`) calls are captured
- [ ] mlx_lm calls captured when `mlx-lm` is installed; silently skipped when not installed
- [ ] Anthropic sync + async calls captured
- [ ] Streaming calls (`stream=True`) write a trace with `output_text="<streaming>"`
- [ ] Tracing exceptions never propagate to the calling application (errors are swallowed)

### SQLite Layer

- [ ] DB file created at `~/.llm-tracer/<app>.db` on first trace
- [ ] WAL mode active (verify with `PRAGMA journal_mode`)
- [ ] `metadata` table created with `schema_version=1` on DB init
- [ ] Two processes writing to the same DB simultaneously do not corrupt data
- [ ] `busy_timeout=5000` prevents immediate lock errors under contention

### Streamlit UI

- [ ] `streamlit run ui/app.py` launches without errors
- [ ] Traces page shows all traces from all `~/.llm-tracer/*.db` files
- [ ] App filter correctly isolates one app's traces
- [ ] Labeling a trace (good/bad) persists to the DB **immediately** (no deferred queue)
- [ ] Cache is cleared after annotation so updated label is visible without manual refresh
- [ ] Dataset Builder shows side-by-side chosen/rejected DPO preview for first 3 pairs
- [ ] Dataset Builder exports valid JSONL for all three formats

### Exporters

- [ ] `to_openai_chat` produces valid JSONL passable to OpenAI fine-tuning API
- [ ] `to_alpaca` produces valid Alpaca format with `instruction`, `input`, `output` keys
- [ ] `to_dpo_pairs` groups by SHA-256 hash of `input_text`; handles unpaired traces gracefully
- [ ] Streaming traces (`output_text="<streaming>"`) are skipped by all exporters

### Integration (openclaw-research)

- [ ] `llm_tracer.init(app="openclaw-research")` in `daily_feed_generator.py` with `llm_tracer.stage()` in sponsor filter and semantic dedup
- [ ] Daily pipeline run creates traces in `~/.llm-tracer/openclaw-research.db`
- [ ] UI shows openclaw-research traces with correct `pipeline_stage` labels

### Quality

- [ ] Test coverage ≥ 80% for `db.py`, `schema.py`, `exporters.py`, `core.py`, `providers/`
- [ ] All tests pass with `uv run pytest`
- [ ] `llm_tracer.init(disabled=True)` is the standard test fixture — no actual DB writes in tests

---

## Implementation Phases

### Phase 1: Scaffold + DB (Day 1)

1. `mkdir /Volumes/ext/projects/llm-tracer && cd llm-tracer`
2. `uv init --lib` → generates `pyproject.toml` with hatchling
3. Update `pyproject.toml` with correct dependencies
4. Implement `llm_tracer/db.py` + `llm_tracer/schema.py`
5. Implement `llm_tracer/core.py` (state + `_sanitize_app_name` + `_write_trace` + `_StageContext`)
6. Implement `llm_tracer/__init__.py` (public API: `init`, `stage`, `disable`, `reset`, `status`)
7. Write `tests/test_db.py` — test schema creation, WAL mode, `metadata` table, thread safety, insert + query
8. Write `tests/conftest.py` — `tmp_db` fixture using `tmp_path`

**Deliverable**: `llm_tracer.init(app="test")` + `_write_trace(...)` works, trace visible via `SELECT * FROM traces`.

### Phase 2: OpenAI + mlx Provider Patching (Day 1–2)

1. Implement `llm_tracer/providers/__init__.py` (registry: `patch_all`, `unpatch_all`)
2. Implement `llm_tracer/providers/openai_provider.py` (sync chat, async chat, sync embeddings, async embeddings)
3. Implement `llm_tracer/providers/mlx_provider.py` (with correct `args[0]`/`args[2]` extraction)
4. Implement `llm_tracer/providers/anthropic_provider.py`
5. Write `tests/test_providers.py` — mock OpenAI client, assert traces written, test idempotency, test `stage()`, test `disable()`, test app name sanitization

**Deliverable**: `llm_tracer.init(app="test"); openai.OpenAI().chat.completions.create(...)` → trace in DB.

### Phase 3: Exporters (Day 2)

1. Implement `llm_tracer/exporters.py` (with SHA-256 hash grouping for DPO)
2. Write `tests/test_exporters.py` — test all three formats, DPO pairing, hash-based dedup, streaming trace skip, malformed input handling

**Deliverable**: `export_jsonl(traces, "openai_chat")` produces valid JSONL.

### Phase 4: Streamlit UI (Day 2–3)

1. Implement `ui/db_loader.py` (with `@st.cache_data(ttl=30)` and cache-clear after writes)
2. Implement `ui/pages/traces.py`
3. Implement `ui/pages/detail.py` (immediate annotation save + cache clear)
4. Implement `ui/pages/dataset.py` (DPO side-by-side preview + format select + download)
5. Implement `ui/pages/stats.py`
6. Implement `ui/app.py` entry point

**Deliverable**: Full UI running against real traces with responsive annotation.

### Phase 5: openclaw-research Integration (Day 3)

1. `cd /Volumes/ext/projects/openclaw-research && uv add --editable ../llm-tracer`
2. Add `llm_tracer.init(app="openclaw-research")` to `daily_feed_generator.py` and `scripts/magazine_generator.py`
3. Add `stage()` wrappers at each call site (see table above)
4. Run daily pipeline, verify traces appear in UI

**Deliverable**: Real pipeline traces visible in Streamlit.

---

## SLM Fine-Tuning (Future Phase)

Once dataset is large enough:

| Task | Target model | Min examples | Framework |
|---|---|---|---|
| Sponsor classification (binary) | Qwen 2.5-3B-Instruct | ~500 | Unsloth + TRL QLoRA 4-bit |
| Item classification (multi-class) | Qwen 2.5-7B-Instruct (current model) | ~500 | Unsloth + TRL QLoRA 4-bit |
| Editorial briefs (generation) | Phi-4-mini | ~1000 | Unsloth + TRL QLoRA 4-bit |

**Research insight (Unsloth):** Unsloth wraps TRL's `SFTTrainer` and patches the model's attention kernels for 2x faster training + 40% lower VRAM. It's compatible with HuggingFace datasets (export from llm-tracer's JSONL → HF `datasets.load_dataset("json", data_files=...)`). On Apple Silicon, use MLX-LM fine-tuning instead of Unsloth (Unsloth requires CUDA). Qwen 2.5-7B is already installed in openclaw-research — same model, so a fine-tuned adapter can drop in directly.

The Dataset Builder export JSONL is the handoff point — training is out of scope for v1.

---

## Dependencies & Prerequisites

- Python 3.11+
- `uv` for package management
- `wrapt>=1.16` — monkey-patching (production-proven, used by OpenLLMetry)
- `streamlit>=1.36` — for `st.navigation` multi-page support
- `pandas>=2.2` — for trace table in UI
- `altair>=5` — for stats charts
- `openai>=1.0` — new client interface (target of patching)
- `anthropic>=0.28` — target of patching
- `mlx-lm>=0.10` — target of patching (optional)

---

## Alternative Approaches Considered

*(see brainstorm: docs/brainstorms/2026-03-12-llm-trace-logger-brainstorm.md)*

| Approach | Why Rejected |
|---|---|
| Langfuse (self-hosted) | Requires Docker + Postgres + ClickHouse — too much infrastructure for local-first use |
| Helicone proxy | Adds 50-80ms per call, requires routing all API calls through proxy |
| OpenLLMetry | Good instrumentation library but ships with OTel backend — we borrow its patching patterns, not the full stack |
| JSONL files instead of SQLite | No queryability, no filtering, no atomic updates for labels |
| Single shared DB for all apps | Harder to isolate apps, more contention — per-app DB is simpler |

---

## Sources & References

### Origin

- **Brainstorm document:** [docs/brainstorms/2026-03-12-llm-trace-logger-brainstorm.md](../brainstorms/2026-03-12-llm-trace-logger-brainstorm.md)
  - Key decisions: (1) auto-instrument always via `init()`, (2) SQLite per-app DB, (3) Streamlit 4-page UI, (4) new standalone project not inside openclaw-research

### Internal References

- `lib/log_utils.py` — `StepTimer` context manager pattern (followed for `_StageContext` design)
- `lib/classifier.py:268-305` — module-level cache pattern for deferred SDK loading
- `rag/embeddings.py:44-50` — OpenAI client instantiation pattern
- `scripts/magazine_generator.py:269-283` — GPT-4o chat completions call structure

### External References

- [OpenLLMetry GitHub](https://github.com/traceloop/openllmetry) — `wrapt.wrap_function_wrapper` pattern for SDK instrumentation
- [SQLite WAL mode docs](https://sqlite.org/wal.html) — WAL + NORMAL synchronous is safe and fast
- [CPython issue #118172](https://github.com/python/cpython/issues/118172) — multi-thread sqlite3 fetch caching bug → `cached_statements=0`
- [OpenAI fine-tuning data format](https://platform.openai.com/docs/guides/fine-tuning) — JSONL chat format spec
- [Streamlit st.navigation docs](https://docs.streamlit.io/develop/api-reference/navigation/st.navigation) — multi-page app routing (v1.36+)
- [Streamlit st.cache_data docs](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.cache_data) — TTL-based query caching
- [Unsloth GitHub](https://github.com/unslothai/unsloth) — 2x faster QLoRA fine-tuning for Qwen/Phi models
