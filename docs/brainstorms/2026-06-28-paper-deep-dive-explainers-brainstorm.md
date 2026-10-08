# Brainstorm: Per-Paper Deep-Dive Explainers ("Deep Dive Review")

**Date:** 2026-06-28
**Status:** Captured — ready for `/ce:plan`

## Problem

The JournalClub-style narrative arc, applied to the *whole* daily report, made the
main briefing worse — flattening dense technical items into long analogy-led prose.
But the arc is genuinely valuable for **understanding hard papers deeply**. The fix:
stop forcing it on the whole report; give it its own home as a dedicated, in-depth
**paper review / explainer** mode that helps the reader actually understand the concepts.

## What We're Building

1. **Revert the main report to the old technical style.** Restore the dense,
   jargon-exact report/podcast prompts (the `*.technical.txt` backups still on disk).
   The daily magazine goes back to what worked.

2. **Add a new "Deep Dive Review" stage** at the end of the pipeline that, from the
   day's papers, produces in-depth per-paper explainers and podcasts.

### Decisions (resolved)

| Decision | Choice |
|----------|--------|
| Main report style | Revert to old technical prompts; JournalClub-arc prompts get **repurposed** for deep-dives |
| Artifact placement | **Separate** from the main report |
| Source scope | **Research papers + paper-discussions** (arXiv/OpenReview/HF papers + links clearly discussing a specific paper) |
| Volume / cost control | **Top 3 by significance** → full explainer **+ individual podcast each**; remaining qualifying papers → **written explainer only** (no audio) |
| Explainer structure | **JournalClub arc + review depth** (7-part template below) |
| Delivery | **One combined "Deep Dive Review" PDF** (all explainers) + the **3 per-paper podcast MP3s** delivered after it |
| Cadence | Daily (chosen over weekly-batch) |

### The per-paper explainer template (review/in-depth mode)

```
PAPER: <title> (authors, venue)
1. The hook (everyday framing)
2. The problem & why it matters
3. How it's done today + the flaw
4. The method / architecture (deep)
5. Experiments & results (numbers)
6. Critique & limitations
7. Why it matters / what to watch
```

This is the JournalClub arc extended with explicit **results** and **critique/limitations**
beats so it reads as a review, not just an explainer.

## Why This Approach

- **Separates concerns:** the daily briefing stays dense and skimmable; depth lives in a
  clearly-labelled opt-in artifact. The earlier failure was conflating the two.
- **Reuses work:** the five JournalClub-arc prompts already written are repurposed here
  rather than discarded — and the `.technical.txt` backups make the revert a file copy.
- **Bounded cost:** per-paper podcasts are the expensive part (~6–10 min NLM gen each);
  the Top-3 cap keeps a heavy paper day from ballooning the pipeline, while every paper
  still gets a written review.

**Alternatives considered:** appending deep-dives inside the main report (rejected — re-couples
the two concerns); all-papers-get-podcasts (rejected — unbounded cost); written-only/no audio
(rejected — user wants the per-paper episodes).

## Open Questions (defer to `/ce:plan` — these are HOW)

1. **Significance signal** for Top-3 selection — reuse the magazine's existing significance
   inference, or a dedicated ranking?
2. **Per-paper podcast mechanism** — one NLM notebook per paper (clean per-paper audio) vs.
   a single notebook with source-focused audio. Per-paper notebooks are likely required for
   truly per-paper episodes; cost/auth implications to weigh.
3. **Paper-discussion detection** — how to decide a non-paper link "is about a specific paper"
   (classifier/heuristic extension).
4. **Artifact storage & naming** — e.g. `data/daily-feed/DATE-deepdive.pdf`,
   `DATE-deepdive-paperN-podcast.mp3`.
5. **Pipeline placement** — daily only, or also surface in the weekly pipeline over the
   week's accumulated papers?

## Success Criteria

- Main daily report is back to the technical style (no narrative-arc regression).
- A "Deep Dive Review" PDF is produced with one in-depth review per qualifying paper.
- The top 3 papers each have a dedicated podcast episode, delivered with the PDF.
- Reading/listening to a deep-dive leaves the user genuinely understanding the paper's
  concept, method, results, and limitations.
