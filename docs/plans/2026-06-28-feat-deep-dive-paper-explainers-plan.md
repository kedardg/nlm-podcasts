---
title: Deep Dive Review — Per-Paper Explainers & Podcasts
type: feat
status: completed
date: 2026-06-28
origin: docs/brainstorms/2026-06-28-paper-deep-dive-explainers-brainstorm.md
---

# ✨ Deep Dive Review — Per-Paper Explainers & Podcasts

## Enhancement Summary

**Deepened on:** 2026-06-28
**Agents used:** kieran-python-reviewer, code-simplicity-reviewer, performance-oracle, architecture-strategist, best-practices-researcher.

### Key improvements folded in
1. **Selection is new code, not reuse.** `select_top_stories()` ranks by confidence + section diversity, not the `significance` band the podcast gate needs. Spec a dedicated `select_top_papers()` reading `url_map` significance + confidence, with a `SIGNIFICANCE_RANK = {"high":3,"medium":2,"low":1}` ordering (never lexical-compare the strings).
2. **Per-artifact-id tracking elevated to a blocker.** With the daily podcast + up to 3 deep-dive audios on one notebook, the existing notebook-scoped `grep -A2` poll/download (`daily_pipeline.sh:512`) returns the *wrong* MP3. Capture each `artifact_id` from `audio create`, poll `studio status --json` filtered by id, download with `--id`, and thread the id/output-prefix through the CDP + Playwright fallbacks.
3. **Source map built on demand (simplicity).** Drop the persisted Phase 0 `DATE-sources.json`; build the url→source_id map at Step 6.6 via `nlm source list <nb> --json` matched with `normalize_url_deep`. Can't drift, doesn't touch `enhance_report.py`'s `.txt` manifest. Phase 0 shrinks to the prompt rename.
4. **One explainer path first.** Ship NLM `notebook query` only, with a content-floor check (non-whitespace length **and** refusal-marker absence). GPT-4o fallback deferred to a later phase unless thin output is observed.
5. **Runtime reorder.** Fire `audio create` first, build explainers + PDF while audio generates server-side, download+compress each paper as it completes (no barrier). Per-paper 15-min deadline; ~30-min total-stage cap; auth per-create + reactive (never per-poll-tick).

### New considerations discovered
- **Lock budget, not cron cadence, is the real timing risk.** Step 5's `MAX_WAIT=10800` (180 min) + this stage can exceed the 2-hour stale-lock threshold (`daily_pipeline.sh:117`), letting a manual `/process` double-run. Cut `MAX_WAIT` to ~2400s or raise the stale-lock threshold.
- **`pdf_renderer.py` gaps** surfaced for the combined PDF: `break-before:page` per paper-section, `break-inside:avoid` is ignored inside CSS columns, TOC page-numbers need block (non-flex) layout, and WeasyPrint is called without `base_url`. (See Research Insights.)
- **`report --source-ids` spike dropped** — not used by this feature; only `audio create --source-ids` matters (confirmed to exist).

---

## Overview

Revert the daily magazine report to its prior **technical** writing style, and add a new
**Deep Dive Review** stage that turns the day's research papers into in-depth, review-grade
explainers. Every qualifying paper gets a written explainer (JournalClub arc + review depth);
the **top 3 by significance** additionally get an individual NotebookLM **podcast** each.
Delivery: one combined **Deep Dive Review PDF** + up to 3 per-paper MP3s to Telegram.

This re-homes the JournalClub narrative arc (which hurt the *whole-report* briefing) into the
one place it adds real value — deep single-paper understanding — while the daily briefing goes
back to dense, skimmable technical prose. (see brainstorm: docs/brainstorms/2026-06-28-paper-deep-dive-explainers-brainstorm.md)

## Problem Statement / Motivation

Applying the JournalClub arc to the entire daily report flattened dense technical items into long
analogy-led prose — worse for a skim-first briefing. But the arc is genuinely valuable for
*understanding hard papers*. The fix is separation of concerns: keep the briefing technical, and
give papers a dedicated deep-dive mode that acts as a review (method, results, critique) so the
reader actually understands the concepts.

## Proposed Solution

A new pipeline stage (daily, **Step 6.6**, after magazine render `scripts/daily_pipeline.sh:550`,
before share-link `:561`) that:

1. **Selects** the day's research papers from already-computed data (NLM report `url_map` +
   merged feed items), ranks by significance.
2. **Writes** a per-paper explainer for each (NLM `notebook query` grounded in the ingested
   paper; content-floor check → stub on thin output). Renders all into `DATE-deepdive.pdf`.
3. **Podcasts** the top 3 via `nlm audio create <notebook> --source-ids <paper_source_id>
   --focus <single-paper-prompt> --confirm` on the **same daily notebook** (no new notebooks).
4. **Delivers** the PDF + up to 3 MP3s in Step 8.

Key reuse: the day's notebook, auth state, source-add, download/compress, PDF renderer, and
Telegram helpers all already exist. The JournalClub-arc prompt I already wrote
(`config/prompts/daily-report-prompt-v3.txt`) is repurposed as the explainer prompt.

### Decisions carried forward from brainstorm

| Decision | Choice | Source |
|----------|--------|--------|
| Main report style | Revert to technical (format-preserving, see §System-Wide Impact) | brainstorm |
| Artifact placement | Separate from the report | brainstorm |
| Source scope | Research papers + paper-discussions | brainstorm |
| Volume / cost | Top 3 → explainer + podcast; rest → explainer only | brainstorm |
| Explainer shape | JournalClub arc + review depth (7 beats) | brainstorm |
| Delivery | One combined PDF + 3 MP3s | brainstorm |
| Cadence | Daily first (weekly = future) | brainstorm |

## Technical Approach

### Architecture

**Division of labor** mirrors the existing pattern (Python builds content; shell orchestrates NLM):

- **New `src/pipeline/deep_dive_generator.py`** — pure content + selection + PDF. Inputs:
  `--date`, `--report-md`, `--notebook-id`. Builds the url→source_id map on demand
  (`nlm source list <nb> --json`). Outputs: `DATE-deepdive.md/.pdf` and a one-way JSON plan
  (`DATE-deepdive-plan.json`) naming the top ≤3 papers with `source_id`, `slug`, `title`, and a
  single-paper `--focus` prompt. Must mirror `magazine_generator.py` conventions (StepTimer,
  `load_json`/`save_json`, frozen dataclasses with `to_dict()`, `run()`/`main()` split,
  `str | None`, DYLD in the docstring) — see Research Insights.
- **New shell Step 6.6 in `scripts/daily_pipeline.sh`** — reads the JSON plan, runs the
  per-paper `nlm audio create … --source-ids … --focus … --confirm` loop (fire all, poll by
  artifact id, download via existing fallback chain, ffmpeg compress), then extends Step 8 delivery.
- **New prompt `config/prompts/deep-dive-explainer-prompt.txt`** — single-paper 7-beat review,
  derived from `daily-report-prompt-v3.txt` (the JournalClub arc) trimmed to one paper and
  extended with explicit **Experiments & Results** and **Critique & Limitations** beats.
- **New prompt `config/prompts/deep-dive-podcast-focus.txt`** — tight single-paper `--focus`.

### Paper selection (reuses existing significance)

- Significance is a `high|medium|low` string from `_infer_significance()`
  (`src/daily_feed_generator.py:1041-1057`), attached per-item and into `url_map`
  (`:986-1001`). Confidence float (classifier) is the numeric tiebreak.
- **Paper set** = `url_map` entries whose normalized key matches the arXiv pattern
  (`src/lib/url_utils.py:41`) **plus** merged items with `content_type == "research"`
  (`src/lib/classifier.py`, `RESEARCH_DOMAINS` `:58-67`).
- **Ranking is NEW code** — a dedicated `select_top_papers(url_map, merged_items, *, max_podcast=3)`
  returning `(all_explainer_papers, podcast_papers)`. Do **not** reuse `select_top_stories()`
  (`magazine_generator.py:114-183`): it ranks by `confidence` + section diversity (harmful here),
  consumes magazine-item dicts (wrong input), and lacks the significance gate. Reuse only the
  primitives: `_infer_significance` value (read via `url_map[url]["significance"]`, **not** the
  private function across modules), `normalize_url_deep`, `RESEARCH_DOMAINS`, `ARXIV_ID_PATTERN`.
- **Significance ordering:** `SIGNIFICANCE_RANK = {"high":3,"medium":2,"low":1}` with `.get(s,0)`
  default. Never lexically compare the strings (`"high" < "low"` is `True` — the opposite of intent).
- **Podcast gate:** only papers with `significance >= medium` get a podcast. If fewer than 3
  qualify, generate fewer (never pad). A pure-`low` day → PDF only.
- **Deterministic tiebreak — one sort key:** `key = (-SIGNIFICANCE_RANK.get(s,0), -confidence, url)`
  (url normalized) → unique, rerun-stable, unit-testable.

### Per-paper podcast scoping (the critical mechanic)

`nlm audio create <nb> --source-ids <id> --focus <prompt> --confirm` scopes a podcast to one
source on the existing daily notebook. This requires a **paper-URL → source_id** map.

**Build it on demand at Step 6.6** (simpler than persisting): the notebook + sources already
exist by then, so call `nlm source list <nb> --json` once and build
`{normalize_url_deep(url): source_id}`. This can't drift from the add loop and doesn't touch the
existing `DATE-sources.txt` manifest that `enhance_report.py` consumes. Match the *probe* URL
through `normalize_url_deep` too (arXiv abs/pdf/html variance).

**Absent-paper branch (real, must not crash):** the top-15 source cap means a selected paper may
have no `source_id`. Declared behavior: **fall through to the next in-notebook paper** (no
on-the-fly source-add in v1 — simpler, idempotent by construction). `source_id` is typed
`str | None`; absence is a branch, never a `KeyError`.

### Per-paper podcast loop (Phase 2) — overlapped, per-artifact-id, isolated

`DATE-deepdive-plan.json` is a **one-way** Python→shell list: `[{source_id, focus_prompt, slug,
title}]` for the top ≤3. The shell tracks artifact ids + status in loop-local state (don't
round-trip status back through the JSON).

```
plan = read DATE-deepdive-plan.json
# 1. Start the long pole FIRST (audio cooks server-side while we build the PDF)
for paper in plan.top3:
    ensure_auth_or_rotate                          # ONCE per create (not per poll tick)
    artifact_id[paper] = nlm audio create <nb> --source-ids <source_id> --focus <prompt> --confirm
    deadline[paper]    = now + 900                 # 15-min per-paper ceiling, stamped at create
    sleep 5-10                                      # stagger: avoid a 4-job burst signature
# 2. (Meanwhile: deep_dive_generator already rendered DATE-deepdive.pdf — see reorder below)
# 3. Single poll loop, parse `studio status --json` ONCE per tick, filter by artifact_id
while pending:
    status = nlm studio status <nb> --json
    for paper in pending:
        if status[artifact_id[paper]] == completed:
            download --id artifact_id[paper]  (CDP → Playwright fallback, BUG-001)  # threaded id/prefix
            ffmpeg -ac 1 -b:a 48k -ar 22050  →  DATE-deepdive-<slug>-podcast.mp3     # per-paper, as it finishes
            mark done
        elif now > deadline[paper] or auth_failures[paper] >= 3:
            mark FAILED (step_fail "deepdive-<slug>"); continue   # never batch-fatal
    sleep 30
```

Key correctness points (from performance + architecture review):
- **Per-artifact-id polling/download is mandatory** — the notebook holds the daily podcast +
  up to 3 deep-dive audios; the existing `grep -A2`/id-less `nlm download audio` (`:512,:522`)
  would deliver the wrong MP3. Use `--json` filtered by the captured ids and `download --id`.
- **Auth once per create + reactive on a `studio status` auth error (bounded to 3)** — do NOT copy
  the per-30s-tick `ensure_auth_or_rotate` at `:510`.
- **Per-paper failure isolation** — timeout / download / ffmpeg failure drops that paper only;
  PDF and other MP3s still ship.

### Artifacts & naming (`data/daily-feed/`)

| File | Purpose |
|------|---------|
| `DATE-deepdive.md` | Combined explainer markdown (render source) |
| `DATE-deepdive.pdf` | Combined Deep Dive Review PDF (primary deliverable) |
| `DATE-deepdive-plan.json` | One-way Python→shell plan: top ≤3 `{source_id, focus_prompt, slug, title}` |
| `DATE-deepdive-<slug>-podcast-raw.mp4` / `-podcast.mp3` | Per-paper raw + compressed audio |

(url→source_id map is built on demand at Step 6.6 from `nlm source list --json` — not persisted.)

### Implementation Phases

#### Phase 0 — Report revert (a prompt rename)
- Restore `config/prompts/daily-report-prompt-v3.technical.txt` → `daily-report-prompt-v3.txt`
  (format-preserving — verified via git history; see System-Wide Impact). Copy the JournalClub
  version to `config/prompts/deep-dive-explainer-prompt.txt` as the explainer-prompt base.
- Deliverable: technical briefing restored; magazine-parse regression test green.

#### Phase 1 — Written explainers (no audio; ships value alone)
- `deep_dive_generator.py`: `select_top_papers()`, generate explainer text via **NLM
  `notebook query` only** with a content-floor check (length + refusal markers); on thin output,
  log + emit a short stub for that paper (GPT-4o fallback deferred). Render `DATE-deepdive.pdf`
  via `render_pdf` with `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` (`src/lib/pdf_renderer.py:300`).
- Step 6.6 (text-only): run generator, deliver `DATE-deepdive.pdf` in Step 8 (`tg_send_file`).
- Deliverable: every qualifying paper gets a written review in one PDF. **No NLM-audio risk yet.**

#### Phase 2 — Per-paper podcasts (top ≤3)
- Generator emits the one-way `DATE-deepdive-plan.json`; Step 6.6 runs the overlapped podcast
  loop above. **Runtime reorder:** fire `audio create` first, render the PDF while audio cooks,
  download+compress each paper as it completes.
- Extend Step 8 to `tg_send_audio` each MP3 with a per-paper caption; partial-success contract.
- **Spike — DONE, verified 2026-06-28** (against notebook `c790e129`, test artifacts deleted):
  - `audio create <nb> --source-ids <id> --focus <p> --format deep_dive --length short --confirm`
    returns `Artifact ID: <uuid>` **immediately**, non-blocking.
  - **Concurrent generation works** — a 2nd scoped create while the 1st was `in_progress` was
    accepted with its own id; `studio status --json` showed all audios with distinct
    `{id, type, status}`. ⇒ fire-all-then-poll is real parallelism. Risk row downgraded.
  - `source list <nb> --url` → `source_id: url` lines (on-demand map). `--json` items =
    `{id, title, type, url}` (url is `None` for non-URL sources — match only URL-bearing ones).
  - `download audio <nb> --id <artifact_id> -o <path>` (per-artifact download confirmed).
  - `studio delete <nb> <artifact_id> --confirm` (positional artifact id) — for rerun cleanup.

#### Phase 3 — Hardening & future (mostly deferred)
- Cheap rerun guard: skip-if-`DATE-deepdive.pdf`-exists + `--force`.
- GPT-4o explainer fallback (only if Phase 1 shows thin NLM output in practice).
- Paper-discussion detection (trafilatura scan for embedded arXiv URLs).
- RAG indexing of explainers; `nlm-operations-log.json` provenance; weekly cross-category variant.

## Alternative Approaches Considered

- **One notebook per paper** (rejected): research confirmed `--source-ids` scopes audio on a
  single notebook, so N notebooks are unnecessary — they'd multiply auth/cleanup/cost.
- **GPT-4o-only explainers** (rejected as primary): not grounded in the full paper; used as
  fallback only.
- **Deep-dive inside the main report** (rejected in brainstorm): re-couples briefing and depth.
- **All papers get podcasts** (rejected): unbounded cost on heavy days.

## System-Wide Impact

### Interaction graph
Step 6.6 runs *after* magazine (`:550`) so the NLM report `url_map` (significance, per-paper
arXiv `**Source:**` URLs) already exists, the daily notebook + sources exist, and auth is fresh.
It reuses: `ensure_auth_or_rotate()` (`:102`), the audio download fallback chain (`:522-537`),
ffmpeg compress (`:543`), `render_pdf`, and `telegram_send.sh` (`tg_send_file`/`tg_send_audio`).

### ⚠️ Report-revert vs magazine parser (latent conflict — RESOLVED)
`daily_feed_generator.py --magazine` calls `parse_nlm_report()`, which requires the **v3
markdown format** (`## AI Research`, `### articles`, `**Source:**`, `**Analysis:**`). The revert
must **preserve that format** — it only changes *tone*. The `daily-report-prompt-v3.technical.txt`
backup is the original v3-format/technical-tone prompt, so restoring it keeps the magazine intact.
**Acceptance test asserts the magazine still parses after revert.** Do NOT revert to
`templates/report-prompt.txt` (different format → would break the magazine).

### Error propagation
Per-paper isolation is mandatory: a podcast timeout, download failure, or thin explainer must not
abort the batch or the PDF. The PDF is the primary deliverable; on PDF render failure, still
deliver MP3s + a markdown fallback and raise a `step_fail` alert.

### State lifecycle risks
Reruns can create duplicate NLM audio artifacts and re-deliver. Phase 3 adds skip-if-exists and a
per-date delivery/artifact manifest; until then, document that reruns re-generate and re-deliver.

### API surface parity
`studio status <notebook>` is not artifact-scoped; with the daily podcast + up to 3 deep-dive
audios on one notebook, polling must track **artifact ids** captured from each `audio create`,
not "the notebook's audio status."

### Integration test scenarios
1. 0 papers → stage skipped, no empty PDF, pipeline still `ok`.
2. 2 papers → 2 explainers in PDF, 2 podcasts (not padded to 3).
3. Top-3 paper absent from notebook sources (no `source_id`) → fall through to the next
   in-notebook paper (no on-the-fly add in v1) — never crash.
4. Auth expires after podcast #1 → re-auth recovers, #2/#3 still complete.
5. PDF render env var missing → render fails loudly; MP3s + markdown fallback still delivered.

## Acceptance Criteria

### Functional
- [ ] Main daily report renders in technical tone; after revert, `parse_nlm_report()` yields a **non-empty `url_map` and all four section names resolve** (not merely "doesn't throw" — the parser fails silently to empty on malformed input).
- [ ] `DATE-deepdive.pdf` contains one explainer per qualifying paper, each following the 7-beat template.
- [ ] Top ≤3 papers (significance ≥ medium) each produce a compressed MP3 scoped to that paper.
- [ ] PDF + MP3s delivered to Telegram with per-paper captions.
- [ ] url→source_id map (built on demand from `nlm source list --json`) resolves every selected paper via `normalize_url_deep`; absent papers fall through without crashing.

### Boundary / determinism (from SpecFlow)
- [ ] 0 papers → stage cleanly skipped, no empty PDF, pipeline `ok`, log/Telegram note emitted.
- [ ] 1–2 papers → exactly N explainers and `min(N,3)` podcasts; never padded.
- [ ] Selection is deterministic across reruns (tiebreak specified to a unique key).
- [ ] A `low`-only day produces a PDF and **no** podcasts.

### Resilience
- [ ] One podcast failing (timeout / download / ffmpeg) does not block the others or the PDF; failure reported per-artifact.
- [ ] `ensure_auth_or_rotate` runs at the top of each podcast iteration (>10-min loops survive cookie rotation).
- [ ] Polling/downloading uses per-artifact ids (disambiguated from the main daily podcast).
- [ ] Explainer below the content floor (length AND refusal-marker check) logs + emits a short stub for that paper; no all-blank template is delivered. (GPT-4o fallback = Phase 3.)
- [ ] Combined-PDF stage runs with `DYLD_FALLBACK_LIBRARY_PATH` set.

### Quality gates
- [ ] Tests for: selection (0/1-2/3/>3/ties), revert-keeps-magazine, url→source_id matching via `normalize_url_deep`, partial-failure delivery.
- [ ] CLAUDE.md updated (Pipeline Scripts, Data Files, Custom Prompts).

## Success Metrics
- A reader who consumes a deep-dive understands the paper's problem, method, results, and limitations without opening the paper.
- Heavy-paper days complete within the daily pipeline's runtime budget without colliding with the next scheduled run.

## Dependencies & Risks

| Risk | Mitigation |
|------|-----------|
| Top-3 paper not in notebook sources (15-cap) | Phase 0 url→source_id map; if absent, add-on-the-fly (idempotent) or fall through to next in-notebook paper |
| `nlm notebook query` returns thin text | Content-floor check (length + refusal markers) → stub for that paper now; GPT-4o fallback later if recurrent |
| Audio download bug (BUG-001) | Existing CDP→Playwright fallback chain, per-paper |
| Cookie rotation mid-loop | Per-iteration `ensure_auth_or_rotate` |
| Multiple audios on one notebook → ambiguous polling | Track artifact ids from each `audio create` |
| Added runtime (3 × gen + polls + re-auth) | Fire all `audio create` first; overlap PDF build; per-paper 15-min deadline + ~30-min stage cap |
| MP3 >16MB | ffmpeg ladder; fall back to notebook share link |
| Lock-budget overrun → manual `/process` double-runs | Cut Step 5 `MAX_WAIT` 10800→~2400s, or raise stale-lock threshold above `MAX_WAIT + stage cap` |
| ~~`audio create --source-ids` blocks while another audio generates~~ | **RESOLVED (spike 2026-06-28): parallelizes — concurrent scoped creates accepted, distinct artifact ids, `studio status --json` disambiguates.** |

## Documentation Plan
- CLAUDE.md: new `deep_dive_generator.py` (Pipeline Scripts), `DATE-deepdive.*` (Data Files), `deep-dive-explainer-prompt.txt` + `deep-dive-podcast-focus.txt` (Custom Prompts), new daily Step 6.6.
- `docs/logs/2026-06-28-deep-dive-design.md` design note.

## Sources & References

### Origin
- **Brainstorm:** [docs/brainstorms/2026-06-28-paper-deep-dive-explainers-brainstorm.md](../brainstorms/2026-06-28-paper-deep-dive-explainers-brainstorm.md) — carried forward: revert report to technical; separate per-paper deep-dive; top-3 get podcasts; JournalClub-arc + review-depth explainer; combined PDF + 3 MP3s.

### Internal references
- `scripts/daily_pipeline.sh` — Steps 2-8 (notebook/source/report/podcast/poll/download/deliver); path vars `:61-69`; auth `:102`; magazine `:550`; insert point `:557`.
- `src/daily_feed_generator.py:1041-1057` (`_infer_significance`), `:986-1001` (`url_map`), `:1303-1305` (tiering).
- `src/pipeline/magazine_generator.py:114-183` (`select_top_stories`), `:186` (`build_magazine_items`).
- `src/lib/url_utils.py:41` (arXiv regex), `:59` (`normalize_url_deep`).
- `src/lib/classifier.py:37-67` (`ContentType`, `RESEARCH_DOMAINS`).
- `src/lib/pdf_renderer.py:300` (`render_pdf`), `:27-29` (DYLD auto-inject).
- `scripts/telegram_send.sh:42/66/94` (message/file/audio); `scripts/nlm_create_report.sh`; `scripts/nlm_download_audio.py`; `scripts/nlm_ensure_auth.sh`.
- `config/prompts/daily-report-prompt-v3.txt` (JournalClub arc → explainer source) and `*.technical.txt` backups.
- `docs/BUGS.md` BUG-001 (audio download); `docs/logs/2026-03-03-playwright-nlm-automation.md` (auth persistence).

### AI-era notes
- Plan grounded by repo-research-analyst, learnings-researcher, spec-flow-analyzer, and (deepen pass) kieran-python-reviewer, code-simplicity-reviewer, performance-oracle, architecture-strategist, best-practices-researcher. The `audio create --source-ids` single-notebook scoping is the key simplification; spike it before Phase 2.

---

## Research Insights (deepen pass)

### Explainer prompting — grounding & anti-hallucination (best-practices)
- **Quote-first, then write only from extracted quotes** (Anthropic) — single highest-value anti-hallucination move; have NLM/LLM pull verbatim quotes, then write the review from them.
- **Ground on full paper text, section-parsed — not abstract/metadata.** Abstract-only fails exactly on method/results/limitations, which the review needs. NLM ingesting the paper as a source is the right substrate; query it per-section.
- **Decompose into named dimensions** (method soundness, results, limitations/critique) rather than one "review this paper" call — forcing named beats stops the model skipping critique. Maps directly onto the 7-beat template.
- **Permit abstention** ("I don't have enough information") to suppress confident fabrication.
- **Content floor = length AND refusal-marker absence** — a refusal ("cannot answer based on the sources") can be 400 chars and pass a length check. `_meets_content_floor(text, min_chars)` must be pure, parameterized, and check both. Sources: platform.claude.com reduce-hallucinations; arxiv 2408.06292, 2309.11495.

### Resilient orchestration (best-practices)
- **Single-flight the cookie refresh** — N in-flight jobs hitting an expired cookie fire N duplicate refreshes and trip Google reuse detection; share one awaited refresh. Proactive refresh at ~70-80% of the ~10-min lifetime + reactive-on-401 (refresh once, retry once, then mark "re-auth required").
- **Fire-all-then-poll with full-jitter capped backoff**, per-job p95 timeout (not worst-case), heartbeat stuck-detection, and checkpoint/resume off `nlm-operations-log.json`. Dead-letter the known download-redirect-bug failures rather than looping. Sources: AWS backoff-and-jitter; Stripe idempotency; MS async request-reply.

### PDF generation (markdown→WeasyPrint) — concrete gotchas for `pdf_renderer.py`
- **`break-before: page` on each paper `<section>`** — clean page breaks + faster pagination (one paper = one section).
- **`break-inside: avoid` is silently ignored inside CSS `columns`** and on table rows — keep cards in grid/block, not `columns`.
- **TOC page numbers via `target-counter(attr(href,url), page)` render `0` under flex/grid** — the TOC `<nav>/<li>/<a>` must be block layout.
- **Clickable outline via `bookmark-level`/`bookmark-label`** (section=1, paper=2).
- **Pass `base_url=str(PROJECT_ROOT)`** to WeasyPrint — current `_WeasyHTML(string=...)` passes none, so relative image/font/css paths fail (pre-existing latent bug).
- **Skip empty `<section>` wrappers** for thin/missing papers rather than emitting an empty bordered box; `break-after: avoid` on headers to prevent orphans. Source: WeasyPrint issues #1491/#2032/#1547/#1076.

### Python conventions (kieran-python-reviewer)
- New module must be structurally indistinguishable from `magazine_generator.py`: path bootstrap, `StepTimer`, `load_json`/`save_json` (never raw `json.dump`), frozen `@dataclass` + `to_dict()` for the paper candidate / plan entry, `run()`+thin `main()`, full `str | None` annotations.
- **No Strategy/ABC hierarchy** for the (eventual) 2-way LLM fallback — one orchestrator + small isolated helpers returning `""` on failure (the `magazine_generator.py:289-306` pattern). Helpers never `sys.exit()` or raise across a paper boundary (breaks per-paper isolation).
