# Brainstorm: LLM Trace Logger & SLM Dataset Curator

**Date:** 2026-03-12
**Status:** Draft
**Author:** Kedar + Claude

---

## What We're Building

A **standalone Python tool** (`llm-tracer`) that:

1. **Captures** all LLM inputs and outputs from any Python app via automatic provider patching (`llm_tracer.init(app=...)`)
2. **Stores** traces in a local SQLite database (zero infrastructure)
3. **Surfaces** them in a Streamlit UI for browsing, filtering, and annotation
4. **Exports** curated subsets as fine-tuning datasets (JSONL in OpenAI chat format, Alpaca, or DPO pairs)

This is a **new project** at `/Volumes/ext/projects/llm-tracer` — not coupled to openclaw-research. Any project can reference it via `uv add ../llm-tracer` (editable local install) and instrument with 2 lines.

---

## Why This Approach

The openclaw-research pipeline makes LLM calls in three distinct ways:
- **OpenAI embeddings** (`text-embedding-3-small`) — RAG indexing + semantic dedup
- **OpenAI GPT-4o chat completions** — sponsor classification, editorial brief generation
- **Local mlx LLM** (`Qwen2.5-7B-Instruct-4bit`) — item classification

Today there is zero capture of what goes in and out of these calls. Every run discards:
- The exact prompt that produced a good classification
- The embedding vectors that correctly deduped a topic
- The GPT-4o response that correctly identified a sponsored item

This data is the training signal for replacing expensive API calls with a fine-tuned on-device SLM. Once ~500–1000 labeled examples accumulate, Qwen 2.5 3B or Phi-4-mini fine-tuned on this data should match GPT-4o quality for these narrow tasks at 1/20th the cost.

---

## LLM Call Taxonomy (openclaw-research)

| Call Site | Model | Task | Volume | Candidate for SLM replacement? |
|---|---|---|---|---|
| `daily_feed_generator._semantic_dedup` | text-embedding-3-small | Semantic dedup | ~50–200/day | Embeddings → keep API or use local bge-m3 |
| `daily_feed_generator` (sponsor filter) | gpt-4o | Classify sponsored/not | ~50–200/day | Yes — binary classifier SLM |
| `magazine_generator.generate_briefs_openai` | gpt-4o | Generate editorial brief | ~10–40/week | Yes — generative SLM |
| `rag/embeddings.index_report` | text-embedding-3-small | Chunk + embed report | 1/day | Keep as-is or use nomic-embed-text |
| `lib/classifier.classify_with_llm` | Qwen2.5-7B (local) | Category classification | ~50/run | Improve this via fine-tuning |

---

## Key Design Decisions

### 1. Instrumentation Style: Auto-instrument (always on)

```python
# Instrument on import — patches OpenAI/Anthropic/mlx clients automatically
import llm_tracer
llm_tracer.init(app="openclaw-research")  # required: app name for DB routing

# Calls are captured automatically with no other changes:
response = openai_client.chat.completions.create(...)

# Optionally tag a call with pipeline_stage for filtering/export:
with llm_tracer.stage("sponsor_filter"):
    response = openai_client.chat.completions.create(...)
```

**Decision: Auto-instrument always.** Patches the provider clients (OpenAI, Anthropic, mlx) on `llm_tracer.init()`. Two fields are required metadata:
- `app` — project name, used to route to the correct `.db` file (required in `init()`)
- `pipeline_stage` — the specific call site / pipeline step (optional but strongly encouraged via `stage()` context manager)

Without `pipeline_stage`, traces are still captured but are harder to filter and label.

### 2. Storage: SQLite

Schema:
```sql
traces (
  id TEXT PRIMARY KEY,          -- uuid
  ts TEXT,                      -- ISO UTC
  app TEXT,                     -- "openclaw-research", "other-app"
  pipeline_stage TEXT,          -- "sponsor_filter", "semantic_dedup", etc.
  provider TEXT,                -- "openai", "anthropic", "local-mlx"
  model TEXT,                   -- "gpt-4o", "text-embedding-3-small"
  operation TEXT,               -- "chat", "embeddings"
  prompt_tokens INT,
  completion_tokens INT,
  latency_ms INT,
  cost_usd REAL,
  input_text TEXT,              -- JSON-serialized messages or text
  output_text TEXT,             -- JSON-serialized response
  metadata TEXT,                -- JSON blob for arbitrary extras
  label TEXT,                   -- "good" / "bad" / null (human annotation)
  label_note TEXT,              -- free text annotation
  in_dataset INT DEFAULT 0,     -- 1 = included in export
  dataset_split TEXT            -- "train" / "val" / "test"
)
```

One `.db` file per app, stored at `~/.llm-tracer/<app-name>.db`. The Streamlit UI can open any `.db` file.

### 3. Streamlit UI

Inspired by promptfoo's side-by-side comparison + impeccable's intent-as-label design:

**Pages:**
- **Traces** — filterable table (app / pipeline_stage / model / date / label / in_dataset)
- **Trace Detail** — full input/output diff, token counts, latency, cost; annotation controls (label + note)
- **Dataset Builder** — select traces → preview as JSONL → export (OpenAI chat format / Alpaca / DPO)
- **Stats** — cost by task, model, date; quality label distribution; estimated SLM readiness (% traces annotated)

**UX pattern from OpenViking:** Show the full prompt construction chain, not just the final text. For chat calls: rendered conversation view. For embeddings: show the chunked input + similarity score returned.

### 4. Dataset Export Formats

```jsonl
// OpenAI fine-tuning (chat format)
{"messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}

// Alpaca (instruction-following)
{"instruction": "...", "input": "...", "output": "..."}

// DPO (Direct Preference Optimization)
{"prompt": "...", "chosen": "...", "rejected": "..."}
```

DPO pairs are generated when the same input has two traces with `label="good"` and `label="bad"` respectively.

### 5. SLM Fine-Tuning (future phase — after data collection)

Once dataset is large enough (~500 examples for classification, ~1000 for generation):
- **Framework**: Unsloth or TRL/Axolotl (QLoRA 4-bit)
- **Target models**: Qwen 2.5 3B-Instruct (classification), Phi-4-mini (editorial briefs)
- The Dataset Builder export is the handoff point — training is out of scope for v1

---

## Project Structure

```
/Volumes/ext/projects/llm-tracer/
├── llm_tracer/
│   ├── __init__.py          # init(), stage() context manager, provider patching
│   ├── db.py                # SQLite CRUD
│   ├── schema.py            # Trace dataclass, cost estimation
│   ├── exporters.py         # JSONL exporters (chat/alpaca/dpo)
│   └── providers/
│       ├── openai.py        # OpenAI-specific capture (messages, embeddings)
│       ├── anthropic.py     # Anthropic messages capture
│       └── mlx.py           # Local mlx_lm capture
├── ui/
│   └── app.py               # Streamlit app
├── pyproject.toml
└── README.md
```

---

## Framework Decision

**Custom SQLite + Streamlit.** Evaluated: Langfuse (full platform, overkill), Phoenix/Arize (OTel-native, great for RAG but heavyweight), Helicone (proxy-based, adds latency), Promptfoo (eval only, not a logger), OpenLLMetry (good auto-instrument library — borrow its monkey-patching approach for provider adapters).

Optional future: export in Langfuse JSONL format for compatibility with that ecosystem.

---

## Resolved Questions

- **New project or inside openclaw-research?** → New standalone project at `/Volumes/ext/projects/llm-tracer`
- **Storage format?** → SQLite (with Streamlit UI for curation)
- **Priority calls?** → All calls, categorized by app/task/model
- **End goal?** → All of: fine-tune SLM, improve prompts, reduce cost
- **Package distribution?** → Structured as a proper package (pyproject.toml) but not published — other projects reference via `uv add ../llm-tracer` (editable install)
- **Auto-instrument?** → Always on via `llm_tracer.init(app=...)`. `app` is required. `pipeline_stage` is optional but strongly encouraged via `with llm_tracer.stage("name"):` — without it traces are uncategorized
- **Multi-app UI?** → Aggregate all `~/.llm-tracer/*.db` files in one Streamlit view with an app filter
- **Annotation?** → Streamlit UI only
