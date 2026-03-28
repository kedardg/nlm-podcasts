# Podcastfy Evaluation — JOB 2 Research

Reference document for evaluating [Podcastfy](https://github.com/souzatharsis/podcastfy) as a local/fallback podcast generation alternative to NotebookLM.

**Status**: Research complete. Implementation deferred until JOB 1 (Playwright NLM automation) is tested.

---

## What is Podcastfy?

Podcastfy is an open-source Python package that generates multi-speaker podcast-style audio from various input types (URLs, PDFs, text, images, YouTube). It uses LLMs for transcript generation and TTS engines for audio synthesis.

- **GitHub**: https://github.com/souzatharsis/podcastfy
- **License**: Apache 2.0
- **Language**: Python 3.11+
- **Latest**: v0.4.x (as of March 2026)

## Architecture

```
Input Sources (URLs, PDFs, text, YouTube)
    |
    v
Stage 1: Content Extractor (trafilatura, PyPDF2, youtube-transcript-api)
    |
    v
Stage 2: LLM Transcript Generator (100+ models via LiteLLM)
    |  - Content Chunking with Contextual Linking (for long content)
    |  - ~7 chunks, min 600 chars each
    |  - Per-chunk transcript generation, linked thematically
    v
Stage 3: TTS Audio Synthesizer (ElevenLabs, OpenAI, Edge TTS, Google Cloud TTS)
    |  - Alternating speaker synthesis per dialogue turn
    |  - LUFS normalization for consistent volume
    v
Stage 4: Audio Assembly
    |  - Concatenate speaker segments
    |  - Optional intro/outro jingles
    v
Output: MP3 at 128-192kbps, 44.1kHz
```

### Key Components

1. **Content extraction** (Stage 1): Uses trafilatura (already in our stack) for web pages, PyPDF2 for PDFs, YouTube transcript API. Extracts clean text from any input format.
2. **Transcript generation** (Stage 2): Core logic in `podcastfy/content_generator/`. Sends extracted content to an LLM with a conversation prompt to generate a two-host dialogue. For long content, uses "Content Chunking with Contextual Linking" — breaks input into ~7 chunks (min 600 chars each), generates per-chunk dialogue, then links chunks thematically so the conversation flows naturally across segments.
3. **TTS synthesis** (Stage 3): Core logic in `podcastfy/text_to_speech.py`. Converts each dialogue turn to audio using the configured TTS provider. Alternates between two voices (configurable). Applies LUFS normalization for consistent volume across segments.
4. **Audio assembly** (Stage 4): Concatenates all speaker segments into a single file. Optionally prepends/appends intro/outro jingle audio. Exports as MP3 at 128-192kbps, 44.1kHz.

### Podcast Generation Internals

**Prompt templates**: Podcastfy uses Jinja2 templates in `podcastfy/content_generator/templates/`. The default `conversation.j2` template structures the dialogue with configurable roles, style, and structure. Custom templates can override the conversation format entirely.

**Chunking strategy**: When input text exceeds the LLM context budget, Podcastfy:
1. Splits into chunks of roughly equal size (target: ~7 chunks)
2. Enforces a minimum chunk size of 600 characters (configurable via `min_chunk_size`)
3. Each chunk gets a "context prefix" summarizing what came before (contextual linking)
4. Generates dialogue for each chunk independently, with the context prefix ensuring thematic continuity
5. Final transcript is the concatenation of all chunk dialogues

**Conversation config** (`conversation_config.yaml`): Controls every aspect of the generated dialogue:
- `roles_person1` / `roles_person2`: Speaker roles (e.g., "lead analyst" / "critical reviewer")
- `conversation_style`: List of style descriptors (e.g., ["analytical", "engaging", "technical"])
- `dialogue_structure`: Ordered sections (Opening, Main Discussion, Deep Dive, Closing)
- `creativity`: Temperature for LLM generation (0.0-1.0)
- `max_num_chunks` / `min_chunk_size`: Chunking parameters
- `text_to_speech.default_voices`: Voice assignments per speaker

### Audio Output

**Default location**: `./data/audio/podcast_{uuid}.mp3`

**Customizable via `output_filepath` parameter**:
```python
audio_file = generate_podcast(
    urls=["https://..."],
    output_file="/custom/path/podcast.mp3"  # Override default
)
```

**Format**: MP3 at 128-192kbps, 44.1kHz, LUFS-normalized for consistent volume. The bitrate depends on the TTS provider's output format — OpenAI TTS outputs at ~192kbps, Edge TTS at ~128kbps.

## Integration with OpenClaw

### How it would fit

```
daily_feed_generator.py → markdown briefing
    |
    v
Podcastfy (local)                    NotebookLM (cloud, primary)
  - Input: briefing markdown           - Input: URLs + briefing text
  - LLM: GPT-4o or Claude             - LLM: Gemini (Google internal)
  - TTS: OpenAI or ElevenLabs         - TTS: Google custom voices
  - Output: MP3                        - Output: MP4 (audio)
    |                                      |
    +---------- fallback <-----------------+
    |
    v
ffmpeg → compressed MP3 → Telegram
```

### When to use as fallback

- NLM auth fails and can't be recovered
- NLM podcast generation times out (>180 min)
- NLM is down or returns errors
- Testing/development without NLM dependency

## LLM Provider Options

### For transcript generation

| Provider | Model | Cost | Quality | Notes |
|----------|-------|------|---------|-------|
| **GitHub Copilot** | `github_copilot/gpt-4.1` | **$0** (with Copilot sub) | High | Via LiteLLM — recommended for $0 cost |
| OpenAI | GPT-4o | ~$0.01-0.03/run | High | Already in our stack (OPENAI_API_KEY) |
| Anthropic | Claude Sonnet | ~$0.01-0.03/run | High | Would need ANTHROPIC_API_KEY |
| Google | Gemini 2.0 Flash | Free tier available | Good | Closest to NLM's own model |
| Local | Ollama + Llama 3 | Free (compute only) | Medium | For Raspberry Pi / offline use |

### GitHub Copilot via LiteLLM ($0 Cost Path)

Podcastfy uses [LiteLLM](https://docs.litellm.ai/) internally for model routing, which natively supports `github_copilot/` prefixed models. This enables podcast generation at **$0 per-token cost** with an existing GitHub Copilot subscription.

**Setup**:
```bash
# 1. Install litellm with Copilot support
pip install 'litellm[proxy]'

# 2. Set Copilot token (from GitHub CLI)
export GITHUB_TOKEN=$(gh auth token)

# 3. Use in Podcastfy config
```

**Configuration** (`config/podcastfy_config.yaml`):
```yaml
# LiteLLM routes to Copilot's GPT-4.1 at 0 premium request cost
llm_model_name: "github_copilot/gpt-4.1"
```

**Usage**:
```python
from podcastfy.client import generate_podcast

audio_file = generate_podcast(
    urls=source_urls,
    llm_model_name="github_copilot/gpt-4.1",
    tts_model="edge",  # Free TTS
    conversation_config="config/podcastfy_config.yaml",
    output_file=f"data/daily-feed/{date}-podcast-podcastfy.mp3"
)
# Total cost: $0 (Copilot LLM + Edge TTS, both free)
```

**Available Copilot models via LiteLLM**:
| Model | Premium Requests | Notes |
|-------|-----------------|-------|
| `github_copilot/gpt-4.1` | 0 | Best balance of quality + cost |
| `github_copilot/gpt-4.1-mini` | 0 | Faster, slightly lower quality |
| `github_copilot/gpt-4o` | 0 | Good alternative |
| `github_copilot/claude-sonnet-4` | 1 per request | Higher quality, uses premium quota |
| `github_copilot/o3-mini` | 1 per request | Reasoning model, overkill for transcripts |

**Limitations**:
- Requires active GitHub Copilot subscription ($10/mo individual, $19/mo pro, free for students/OSS)
- Token limits may be lower than direct API access
- Rate limits apply (300 req/min for Copilot Business)
- The `github_copilot/` prefix tells LiteLLM to route through GitHub's API endpoint

### For TTS synthesis

| Provider | Voices | Cost | Quality | Latency |
|----------|--------|------|---------|---------|
| OpenAI TTS | 6 voices | $15/1M chars | Very good | Fast |
| ElevenLabs | Custom cloning | $5-22/mo | Excellent | Medium |
| Edge TTS | 300+ voices | Free | Good | Fast |
| Google Cloud TTS | 200+ voices | $4-16/1M chars | Very good | Fast |

**Recommendation**: OpenAI TTS (`tts-1-hd`) — already have the API key, good quality for spoken word, reasonable cost (~$0.05-0.10 per 20-min podcast).

## Podcastfy Configuration

### Custom conversation config

```python
# config/podcastfy_config.yaml
conversation_style:
  - analytical
  - technical
  - engaging
roles_person1: lead technical analyst
roles_person2: critical technical reviewer
dialogue_structure:
  - Opening
  - Main Discussion
  - Technical Deep Dive
  - Practical Implications
  - Closing
podcast_name: "OpenClaw Daily Tech Feed"
podcast_tagline: "AI research and engineering, synthesized daily"
output_language: "English"
creativity: 0.7
max_num_chunks: 10
min_chunk_size: 500
text_to_speech:
  model: "tts-1-hd"
  default_voices:
    question: "echo"    # Host 1
    answer: "nova"      # Host 2
```

### Integration code sketch

```python
# In daily_pipeline.sh or nlm_playwright.py fallback:
from podcastfy.client import generate_podcast

audio_file = generate_podcast(
    urls=source_urls,
    text=briefing_text,
    tts_model="openai",
    llm_model_name="gpt-4o",
    conversation_config="config/podcastfy_config.yaml",
    output_file=f"data/daily-feed/{date}-podcast-podcastfy.mp3"
)
```

## Comparison: NLM vs Podcastfy

| Aspect | NotebookLM | Podcastfy |
|--------|-----------|-----------|
| **Audio quality** | Excellent (custom Google voices, natural pauses/laughter) | Good (depends on TTS provider) |
| **Content grounding** | Deep — reads and synthesizes all sources natively | Depends on LLM context window and extraction quality |
| **Source handling** | 50 sources per notebook, processes PDFs/URLs/YouTube natively | Limited by LLM context; needs content extraction first |
| **Cost** | Free (Google account) | LLM + TTS costs (~$0.10-0.20/podcast) |
| **Reliability** | Poor — auth expires, downloads break, timeouts | Good — local execution, no auth issues |
| **Latency** | 10-30 min generation | 2-5 min generation |
| **Customization** | Limited (format, length, focus prompt) | Full control (prompt, voices, structure, length) |
| **Offline** | No | Yes (with local LLM + Edge TTS) |

## Hardware Requirements

### Minimum requirements (cloud LLM + Edge TTS)

| Resource | Requirement | Notes |
|----------|-------------|-------|
| CPU | Any modern x86/ARM | No GPU needed |
| RAM | 2 GB free | Podcastfy + Edge TTS are lightweight |
| Storage | ~500 MB | Python packages + cached audio segments |
| Network | Required | For LLM API calls + Edge TTS (HTTP-based) |
| Python | 3.11+ | Matches our `.venv` |

With a cloud LLM (Copilot via LiteLLM, OpenAI, etc.) and Edge TTS, Podcastfy's compute requirements are minimal — it's essentially an API orchestrator with audio concatenation.

### Raspberry Pi 4B (16 GB RAM) — Feasibility Analysis

**Short answer**: Yes, it can run Podcastfy with cloud LLM + Edge TTS. Not practical for local LLM inference.

**Cloud LLM + Edge TTS (recommended for Pi)**:
| Aspect | Assessment |
|--------|-----------|
| **CPU** | BCM2711 (4x Cortex-A72 @ 1.8GHz) — sufficient for API orchestration |
| **RAM** | 16 GB — far more than needed (Podcastfy uses ~200-400 MB) |
| **Edge TTS** | Pure Python, uses Microsoft's free HTTP API — works perfectly on ARM, no local compute |
| **Bottleneck** | Network latency for LLM API calls (typically 30-60s per chunk × 7 chunks ≈ 5-7 min) |
| **Audio assembly** | pydub/ffmpeg for concatenation — takes ~10-30s on Pi 4B for a 20-min podcast |
| **Total generation time** | ~5-10 min for a 20-min podcast |
| **Cost** | $0 with Copilot/LiteLLM + Edge TTS |

**Fully local (Ollama + local TTS)**:
| Aspect | Assessment |
|--------|-----------|
| **LLM** | Ollama + Llama 3.2 3B (Q4_K_M) fits in ~4 GB — leaves 12 GB free |
| **Speed** | ~2-3 tokens/sec on Pi 4B CPU (no GPU) — 7 chunks × ~1000 tokens = ~40-60 min |
| **TTS** | Piper TTS for fully offline — fast on ARM, decent quality |
| **Total** | ~50-75 min for a 20-min podcast |
| **Quality** | Noticeably worse than cloud models — less natural dialogue, weaker synthesis |

**Verdict**: Pi 4B 16GB is viable as a **zero-cost podcast server** using `github_copilot/gpt-4.1` + Edge TTS. The 16 GB RAM is overkill for this use case (good headroom for other services). Local LLM is technically possible but impractically slow and lower quality.

### Raspberry Pi 5 (8 GB) — Comparison

Pi 5 with its 2.4GHz quad-core Cortex-A76 is ~2-3x faster than Pi 4B for CPU-bound tasks. For cloud LLM + Edge TTS, both perform similarly (network-bound). For local LLM, Pi 5 achieves ~5-8 tokens/sec (still slow but more tolerable).

### Quality tradeoff (local vs cloud LLM)

Local LLM (3B params) produces noticeably worse transcripts than GPT-4o or Gemini. The dialogue is less natural, cross-source synthesis is weaker, and technical accuracy drops. Recommended only for testing, offline environments, or when cloud APIs are unavailable.

## Implementation Plan (Future)

1. `pip install podcastfy` (add to requirements.txt)
2. Create `config/podcastfy_config.yaml` with our custom conversation style
3. Add `--fallback-podcastfy` flag to `scripts/nlm_playwright.py`
4. If NLM podcast generation fails → automatically fall back to Podcastfy
5. Output file: `{prefix}-podcast-podcastfy.mp3` (distinct from NLM output)
6. Log which engine generated the podcast in `data/nlm-operations-log.json`

## Dependencies

```
podcastfy>=0.4.0
# Podcastfy's own deps (auto-installed):
# - openai (already have)
# - trafilatura (already have)
# - pydub
# - youtube-transcript-api
# - PyPDF2
```

## References

- Podcastfy repo: https://github.com/souzatharsis/podcastfy
- Podcastfy docs: https://podcastfy.readthedocs.io/
- OpenAI TTS pricing: https://openai.com/api/pricing/
- ElevenLabs: https://elevenlabs.io/
- Edge TTS (Python): https://github.com/rany2/edge-tts
