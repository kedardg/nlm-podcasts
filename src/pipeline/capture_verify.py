"""The gate that stands in front of an irreversible delete.

Every check here demands positive proof that the capture is real. Absence of an
error is not evidence: a failed download leaves a partial file exactly where a
good one would be, a dropped mind map leaves a status payload that looks
complete, and an empty source list makes "every source captured" trivially true.

Pure functions over already-collected evidence, so every rejection path is
testable without touching the network.
"""

from dataclasses import dataclass, field
from pathlib import Path

from lib.archive_paths import UnsafePathError, safe_archive_path, volume_device_id

# Statuses that mean "this artifact is finished, one way or the other".
# An allowlist, not a denylist: nlm normalises an unrecognised status to
# "unknown", and an artifact whose payload shape changed may well still be
# generating.
TERMINAL_ARTIFACT_STATUSES = frozenset({"completed", "failed"})

# The only reason it is acceptable for `download all` to skip an artifact.
ACCEPTABLE_SKIP_REASONS = frozenset({"already downloaded"})

# Minimum plausible extracted length, by source type. A cookie wall or a 403
# page clears a naive "non-empty" floor; companion notes are legitimately tiny.
CHAR_FLOORS = {
    "generated_text": 20,
    "pasted_text": 20,
    "web_page": 500,
    "youtube": 100,
    "pdf": 200,
}
DEFAULT_CHAR_FLOOR = 200

_HTML_PREFIXES = (b"<!doctype html", b"<html", b"<!DOCTYPE HTML")
_TEXTUAL_KINDS = {"report", "mind_map", "data_table", "quiz", "flashcards"}
_MEDIA_KINDS = {"audio", "video"}
_PDF_KINDS = {"slide_deck"}


@dataclass
class Verdict:
    """Outcome of the gate. ``ok`` is the only value that permits deletion."""

    ok: bool = True
    reasons: list = field(default_factory=list)

    def fail(self, reason):
        self.ok = False
        self.reasons.append(reason)
        return self

    def __bool__(self):
        return self.ok


def check_authority(verdict, authority, notebook_id):
    """Condition 1 — only the authority that owns this notebook may delete it."""
    if not authority.permits(notebook_id):
        verdict.fail(
            f"{authority.kind} authority does not permit {notebook_id}"
        )
    return verdict


def check_frozen_drift(verdict, authority, notebook_id, live):
    """Condition 1b — the notebook must still look like what we froze.

    A tripwire, not a delete authority. If the user renamed it, added sources,
    or opened it since the freeze, the snapshot no longer describes it and a
    human should look before anything irreversible happens.
    """
    frozen = authority.frozen(notebook_id)
    if frozen is None:
        return verdict  # DailyAuthority: created this run, nothing to drift from
    if live.get("title", "") != frozen.title:
        verdict.fail(
            f"title drifted since freeze: {frozen.title!r} -> {live.get('title')!r}"
        )
    if (live.get("source_count") or 0) != frozen.source_count:
        verdict.fail(
            f"source_count drifted since freeze: {frozen.source_count} -> "
            f"{live.get('source_count')}"
        )
    if frozen.updated_at and (live.get("updated_at") or "") > frozen.updated_at:
        verdict.fail(
            f"notebook was touched since freeze: {frozen.updated_at} -> "
            f"{live.get('updated_at')}"
        )
    return verdict


def check_source_counts(verdict, enumerated, listed, rows, *,
                        frozen_count=None, allow_empty=False):
    """Condition 2 — three counts must agree, and none may be zero.

    A transient empty `nlm source list` would otherwise make 0 == 0 == 0 pass
    and delete a 16-source notebook having captured nothing.

    The exception is a notebook that is *provably* empty: some runs created a
    notebook and died before adding any sources. ``frozen_count`` comes from
    the allowlist snapshot — a separate call at a separate time — so agreement
    between it and today's zero is independent evidence rather than the same
    failed call observed three times. Even then it takes ``allow_empty``, so
    destroying a notebook that holds nothing is always a deliberate choice.
    """
    provably_empty = (
        not enumerated and not listed and not rows and frozen_count == 0
    )
    if provably_empty:
        if allow_empty:
            return verdict
        verdict.fail(
            "notebook holds no sources (confirmed against the frozen snapshot); "
            "pass --allow-empty to reclaim it"
        )
        return verdict
    if not enumerated or not listed or not rows:
        verdict.fail(
            f"source counts must all be non-zero (enumerated={enumerated}, "
            f"listed={listed}, rows={rows})"
        )
        return verdict
    if not (enumerated == listed == rows):
        verdict.fail(
            f"source counts disagree (enumerated={enumerated}, listed={listed}, "
            f"rows={rows})"
        )
    return verdict


def check_source_content(verdict, source_rows, *, allow_thin=False):
    """Condition 3 — every source captured real text, or says why not.

    ``allow_thin`` downgrades the per-type length floor to a recorded note.
    Some sources really are thin — a placeholder URL such as example.com
    returns ~179 characters, and that is the whole page, not a failed capture.
    Every other condition still applies.
    """
    for row in source_rows:
        status = row.get("content_status")
        if status != "ok":
            verdict.fail(
                f"source {row.get('nlm_source_id')} has content_status="
                f"{status!r}; only 'ok' permits deletion"
            )
            continue
        floor = CHAR_FLOORS.get(row.get("source_type"), DEFAULT_CHAR_FLOOR)
        if (row.get("char_count") or 0) < floor and not allow_thin:
            verdict.fail(
                f"source {row.get('nlm_source_id')} captured "
                f"{row.get('char_count')} chars, below the {floor} floor for "
                f"{row.get('source_type')}"
            )
    return verdict


def check_download_result(verdict, download_json, studio_artifact_count):
    """Downloads must have succeeded, and nothing may have been quietly skipped."""
    if download_json is None:
        verdict.fail("no `download all` result recorded")
        return verdict

    for item in download_json.get("items", []):
        if item.get("success") is not True:
            # A failed streaming download still leaves a partial file at
            # items[].path, which passes existence, size and magic-byte checks.
            verdict.fail(
                f"artifact {item.get('artifact_type')} reported success="
                f"{item.get('success')!r}: {item.get('error')}"
            )
    for skip in download_json.get("skipped", []):
        reason = (skip.get("reason") or "").lower()
        if reason not in ACCEPTABLE_SKIP_REASONS:
            verdict.fail(f"artifact skipped by nlm: {reason!r}")

    reported = download_json.get("total_artifacts")
    if reported is not None and studio_artifact_count is not None:
        if reported != studio_artifact_count:
            # `download all` re-polls status independently; a mismatch means the
            # set moved under us, or our snapshot was already incomplete.
            verdict.fail(
                f"artifact count moved between snapshot and download "
                f"({studio_artifact_count} -> {reported})"
            )
    return verdict


def check_artifact_states(verdict, studio_status, status_call_ok):
    """Condition 5 — nothing may still be generating."""
    if not status_call_ok:
        verdict.fail("`studio status` did not succeed; artifact state is unknown")
        return verdict
    if studio_status is None:
        verdict.fail("no `studio status` payload recorded")
        return verdict
    for art in studio_status:
        status = (art.get("status") or "").lower()
        if status not in TERMINAL_ARTIFACT_STATUSES:
            verdict.fail(
                f"artifact {art.get('artifact_id') or art.get('id')} is in "
                f"non-terminal state {status!r}"
            )
    return verdict


def _magic_ok(path: Path, kind: str) -> tuple:
    try:
        head = path.open("rb").read(512)
    except OSError as exc:
        return False, f"unreadable: {exc}"
    if not head:
        return False, "file is empty"
    lowered = head.lstrip()[:16].lower()
    if kind in _TEXTUAL_KINDS and any(
        lowered.startswith(p.lower()) for p in _HTML_PREFIXES
    ):
        return False, "looks like an HTML error page, not the artifact"
    if kind in _PDF_KINDS and not head.startswith(b"%PDF-"):
        return False, "does not begin with %PDF-"
    return True, None


def check_artifact_files(
    verdict, data_dir, notebook_id, artifact_rows, *,
    expected_device=None, media_probe=None, seen_inodes=None,
):
    """Condition 4/6/7 — the files are present, plausible, and in bounds."""
    seen_inodes = seen_inodes if seen_inodes is not None else {}
    for row in artifact_rows:
        kind = row.get("kind")
        rel = row.get("file_path")
        if row.get("status") == "missing":
            verdict.fail(f"artifact {kind} was never captured")
            continue
        if not rel:
            verdict.fail(f"artifact {kind} has no recorded path")
            continue
        try:
            path = safe_archive_path(data_dir, notebook_id, rel)
        except UnsafePathError as exc:
            verdict.fail(f"artifact {kind}: {exc}")
            continue
        if not path.exists():
            verdict.fail(f"artifact {kind} missing on disk: {rel}")
            continue

        st = path.stat()
        if st.st_size == 0:
            verdict.fail(f"artifact {kind} is zero bytes")
            continue
        if row.get("size_bytes") is not None and st.st_size != row["size_bytes"]:
            verdict.fail(
                f"artifact {kind} is {st.st_size} bytes, recorded as "
                f"{row['size_bytes']}"
            )
        if expected_device is not None and st.st_dev != expected_device:
            # The volume moved under us; files written earlier may be gone.
            verdict.fail(f"artifact {kind} sits on a different device than at start")
        if st.st_ino in seen_inodes and seen_inodes[st.st_ino] != notebook_id:
            # A hardlink into another notebook's capture — sha256 cannot tell
            # this from a genuine copy, which is the duplicate-title hazard.
            verdict.fail(f"artifact {kind} is hardlinked to {seen_inodes[st.st_ino]}")
        seen_inodes[st.st_ino] = notebook_id

        ok, why = _magic_ok(path, kind)
        if not ok:
            verdict.fail(f"artifact {kind}: {why}")
            continue
        if kind in _MEDIA_KINDS and media_probe is not None:
            duration = media_probe(path)
            if not duration or duration <= 0:
                verdict.fail(f"artifact {kind} has no playable duration")
    return verdict


def check_volume(verdict, data_dir, expected_device):
    """Condition 7 — the archive is still on the volume we started on."""
    if expected_device is None:
        return verdict
    try:
        current = volume_device_id(data_dir)
    except OSError as exc:
        return verdict.fail(f"cannot stat the archive volume: {exc}")
    if current != expected_device:
        verdict.fail(
            f"archive volume changed (device {expected_device} -> {current}); "
            f"writes may have landed on an empty mountpoint"
        )
    return verdict
