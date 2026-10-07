# Notebook lifecycle: capture, verify, delete

**Date:** 2026-09-29
**Plan:** [docs/plans/2026-09-29-feat-notebook-lifecycle-local-archive-plan.md](../plans/2026-09-29-feat-notebook-lifecycle-local-archive-plan.md)
**Brainstorm:** [docs/brainstorms/2026-09-29-notebook-lifecycle-and-local-archive-brainstorm.md](../brainstorms/2026-09-29-notebook-lifecycle-and-local-archive-brainstorm.md)

## What was wrong

The NotebookLM account held **499 of 500** notebooks, so `nlm notebook create`
failed and both pipelines had been dead since roughly 2026-08-16. No code in
this repo had ever deleted a notebook — no TTL, no cap monitoring, nothing.

Weekly was the amplifier. `data_triage.py` splits a category into ~15-source
sub-notebooks, so `Machine Learning - Week of 2026-08-15` alone became **28
notebooks across 441 sources**. July produced 163 notebooks, August 230.

The leak was made worse by failure: the last daily-feed artifacts on disk were
2026-06-28, yet notebooks were still being created through 2026-08-16. Every
failed run left a notebook behind, holding a slot, holding artifacts nobody
ever downloaded.

## Design decisions

**NotebookLM is a rendering service, not storage.** It has no versioning, no
backup, no query API beyond chat, and a hard object cap. Once artifacts are on
disk the notebook has no remaining job. Everything else follows from that.

**Keyed by `notebook_id`, never by title or alias.** Four titles are duplicated
across nine notebooks; three contain a literal tab or `\x02`; 26 aliases
contain a `/`. `nlm download all` names its output directory after the *title*,
so a shared output root plus `--skip-existing` would let one notebook's capture
silently "succeed" against another's files, then pass verification, then be
deleted. `--skip-existing` is therefore never passed, and every capture gets
its own id-named directory.

**Two delete authorities, neither falling back to the other.** Reclaim deletes
only ids in a frozen, digest-verified allowlist. Daily deletes only a notebook
the running process created. A frozen file cannot authorise tomorrow's
notebook, and a run-scoped authority must not reach into the backlog. Titles
are never re-matched at delete time — a personal notebook shaped like
`Anything - Week of 2027-01-04` must never become deletable.

**The gate demands positive proof, not absence of error.** Three failure modes
drove this:
- A failed streaming download leaves a *partial file at the same path*, which
  passes existence, size and magic-byte checks. So `items[].success` must be
  `True` for every item.
- A transient empty `nlm source list` makes "every source captured" trivially
  true. So source counts must agree *and* be non-zero — unless the frozen
  snapshot independently agrees the notebook never had sources.
- `nlm` normalises an unrecognised artifact status to `"unknown"`, so the
  terminal set is an allowlist (`completed`, `failed`), not a denylist.

**Staging plus a single rename.** Capture writes to `.staging-<run_id>/` and is
promoted only after the transaction commits; an existing capture is set aside,
not overwritten. The archive is the only copy those artifacts have, so a
crashed re-capture must not be able to destroy a good one.

## What the documentation had wrong

- **BUG-001 was fixed upstream.** `nlm download all` fetched an 83 MB `.m4a`
  in one call (`ffprobe`: 2582 s @ 257 kbps). The CDP and Playwright audio
  workarounds are obsolete on 0.9.6. CLAUDE.md said `nlm` 0.3.15; it is 0.9.6.
- **`/chat` was already broken.** `rag_chat.py:27` pointed at `rag/query.py`,
  which has not existed since the repo layout moved. Every query returned
  "I don't have any indexed notebooks." This mattered for sequencing: the plan
  had put a local-RAG rewrite *before* the reclaim to protect `/chat`, and
  `/chat` had nothing to protect. Only 56 of the 465 reclaimable notebooks had
  ChromaDB routing rows at all.
- **`backup.sh` exited 1 silently.** A quoting bug at `:107` collapsed three
  globs into one shell word; `2>/dev/null` hid the failure and `set -e` aborted
  the script after printing plausible output.
- **The pipeline lock was unsound.** It wrote no PID, guessed staleness from
  mtime, and installed an unconditional `trap 'rmdir "$LOCKDIR"' EXIT` — so a
  process whose lock had been reclaimed would delete the *new* holder's lock.

## What running it taught

**The concurrency hazard was not theoretical.** Two reclaim processes
overlapped in the window before reclaim took the lock. `notebook_events` showed
exactly what happened: one captured, verified and deleted each notebook; the
other lost the race, and its `download all` failed *because the notebook was
already gone*, then overwrote the state to `needs_human` with that misleading
error. Four notebooks looked failed while their captures sat intact on disk
(136, 95, 41 and 41 MB). Without the append-only event log this would have been
undiagnosable. `archive_reconcile.py --repair` now fixes exactly this, on three
independent pieces of evidence.

**Fail-closed needs escape hatches, not a lower bar.** Two real cases surfaced
that the gate refused correctly but permanently: a source whose URL is
`https://example.com/article1` and whose entire page is 179 characters, and
notebooks created before a run died without adding any sources. Both became
explicit flags (`--allow-thin-sources`, `--allow-empty`) rather than a weaker
floor — and `--allow-empty` still requires the frozen snapshot to agree the
notebook never had sources, so a transient empty list is refused.

**`needs_human` must be terminal.** It was being retried every batch,
re-downloading every artifact only to refuse again. The set grows, so each
batch got slower than the last.

## Deferred

Local RAG over the captured text (`/chat` currently answers from nothing),
serving `/generate` from the archive instead of live notebooks, prompt-injection
controls for synthesis over scraped page text, and folding weekly's data steps
into daily. Tracked as `todos/040`, `todos/045`.
