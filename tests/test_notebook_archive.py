"""Tests for the notebook capture-and-delete archive.

Deleting a NotebookLM notebook is irreversible, so these lean hard on the
rejection paths: the value of the gate is entirely in what it refuses.
"""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lib import archive_db  # noqa: E402
from lib.archive_paths import (  # noqa: E402
    UnsafePathError, assert_notebook_id, notebook_dir, safe_archive_path,
    staging_dir,
)
from lib.notebook_allowlist import (  # noqa: E402
    MAX_DELETES_PER_RUN, AllowlistError, DailyAuthority, FrozenNotebook,
    ReclaimAuthority, canonical_digest, cap_for_run, load_allowlist,
)
from pipeline import capture_verify as cv  # noqa: E402

NB = "bf88fbcb-5023-4074-97fc-c64fa3f62d2e"
NB2 = "3b86e94e-62ea-4895-af63-9a4281547381"


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_allowlist", PROJECT_ROOT / "tools" / "build_allowlist.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Classification ────────────────────────────────────────────────────────────

# Real titles from the live account that must never be classified as deletable.
# Five of these are pure AI/ML topics, indistinguishable by subject from
# pipeline output — which is why classification is by emitted-title shape and
# never by topic.
PERSONAL_TITLES = [
    "Accounting FP&A",
    "IdentityRAG: Unifying Customer Context for High-Precision AI",
    "RAG and LLM Interview Study Guide",
    "Statistical Modeling and Causal Inference Library Directory",
    "CUDA from Zero — Kernels, Memory, and Neural Nets on the GPU",
    "Agentic and Automated Document Data Extraction",
    "vLLM vs TensorRT-LLM on RTX 5080 — Models & Advancements",
    "Is AI Reasoning Right for the Wrong Reasons?",
    "Ultimate Beginner's Guide to Financial Planning and Analysis",
    "Precision Tracking: A Technical Guide to Pro Ball and Athlete Metrics",
    "The Front Office Investment Banking Process Lifecycle",
    "Clinical NLP Advances: Retrieval, Evaluation, and Production Standards",
    "Insurance Claim Denials and the Appeals Process",
    "Design and Analysis of Post-Tensioned Concrete Bridges",
    "Healthcare Waste and the WISeR Prior Authorization Model",
    "ICD-10 Claim Validation System: Architecture and Coding Rules",
    "Machine Learning and Policy for Upcoding in Healthcare Payment",
    "Medicare Inpatient Reimbursement, Coding, and Auditing",
    "Transformers and Text Diffusion Language Models",
    "Transformer Attention Parallelism and Efficiency",
    "TAT-QA: Numerical Reasoning over Hybrid Finance Data",
    "Fine-Tuning and Safety of GPT-OSS Models",
    "Energy-Based Transformers: Scalable Learners and Thinkers",
]

VOCAB = {
    "Machine Learning", "NLP & Language Models", "Computer Vision",
    "Robotics & Embodied AI", "AI Safety & Policy", "Open Source & Tools",
    "Tech Industry", "Developer Experience", "General", "Unknown",
}


@pytest.mark.parametrize("title", PERSONAL_TITLES)
def test_personal_notebooks_are_never_deletable(title):
    origin, reason = _load_builder().classify({"id": NB, "title": title}, VOCAB)
    assert origin is None, f"{title!r} would have been deleted"
    assert reason


@pytest.mark.parametrize("title", [
    "Daily Feed — 2026-08-16",
    "Machine Learning - Week of 2026-08-15",
    "Machine Learning (Part 12/28) - Week of 2026-08-15",
    "Machine Learning + NLP & Language Models - Week of 2026-03-30",
    "Unknown - Week of 2026-02-23",
])
def test_pipeline_titles_are_deletable(title):
    origin, _ = _load_builder().classify({"id": NB, "title": title}, VOCAB)
    assert origin in ("daily", "weekly")


@pytest.mark.parametrize("title", [
    "My Reading - Week of 2026-03-01",      # weekly shape, unknown group
    "Budget - Week of 2026-01-05",
    "Daily Feed Ideas",                     # would match a naive prefix rule
    "Daily Feedback Notes",
    "Research Digest - 2023-10-03",         # predates this repo
    "",                                     # empty title
])
def test_lookalike_titles_are_protected(title):
    origin, reason = _load_builder().classify({"id": NB, "title": title}, VOCAB)
    assert origin is None, f"{title!r} would have been deleted"
    assert reason


def test_corrupted_daily_titles_matched_by_id_not_prefix():
    mod = _load_builder()
    corrupt_id = sorted(mod.CORRUPT_IDS)[0]
    origin, _ = mod.classify({"id": corrupt_id, "title": "Daily Feed \t6-02-20"}, VOCAB)
    assert origin == "corrupt"
    # The same corrupted-looking title on a different id stays protected.
    origin, _ = mod.classify({"id": NB, "title": "Daily Feed \t6-02-20"}, VOCAB)
    assert origin is None


# ── Path safety ───────────────────────────────────────────────────────────────

def test_notebook_id_must_be_a_uuid():
    assert assert_notebook_id(NB) == NB
    for bad in ["daily-2026-09-29", "general-(part-1/6)-2026-03-30", "", "../x"]:
        with pytest.raises(UnsafePathError):
            assert_notebook_id(bad)


@pytest.mark.parametrize("candidate", [
    "/etc/passwd", "../../../etc/passwd", "a/../../b", "x\x00y", "x\ny",
])
def test_paths_cannot_escape_the_notebook_directory(tmp_path, candidate):
    notebook_dir(tmp_path, NB).mkdir(parents=True)
    with pytest.raises(UnsafePathError):
        safe_archive_path(tmp_path, NB, candidate)


def test_symlink_component_is_rejected(tmp_path):
    nd = notebook_dir(tmp_path, NB)
    (nd / "artifacts").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("x")
    (nd / "artifacts" / "link").symlink_to(outside)
    with pytest.raises(UnsafePathError):
        safe_archive_path(tmp_path, NB, "artifacts/link/secret.md")


def test_duplicate_titles_get_disjoint_directories(tmp_path):
    """Four titles are duplicated across nine live notebooks; keying the archive
    on notebook_id is what stops one capture from verifying against another's
    files."""
    assert notebook_dir(tmp_path, NB) != notebook_dir(tmp_path, NB2)


def test_staging_is_inside_the_notebook_directory(tmp_path):
    s = staging_dir(tmp_path, NB, "reclaim_2026-09-29T00:00:00Z")
    assert s.parent == notebook_dir(tmp_path, NB)


# ── Schema ────────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    conn = archive_db.open_db(tmp_path / "research.db")
    yield conn
    conn.close()


def test_pragmas_and_version(db):
    assert db.execute("PRAGMA user_version").fetchone()[0] == archive_db.SCHEMA_VERSION
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def _insert_notebook(db, notebook_id=NB, state="pending", origin="daily"):
    with archive_db.write_txn(db):
        db.execute(
            "INSERT INTO notebooks (notebook_id,title,origin,state) VALUES (?,?,?,?)",
            (notebook_id, "T", origin, state),
        )


def test_unknown_state_is_rejected(db):
    _insert_notebook(db)
    with pytest.raises(sqlite3.IntegrityError):
        with archive_db.write_txn(db):
            db.execute("UPDATE notebooks SET state='delted' WHERE notebook_id=?", (NB,))


def test_deleted_requires_a_timestamp(db):
    _insert_notebook(db)
    with pytest.raises(sqlite3.IntegrityError):
        with archive_db.write_txn(db):
            db.execute("UPDATE notebooks SET state='deleted' WHERE notebook_id=?", (NB,))


def test_protected_state_is_unreachable_by_the_delete_query(db):
    _insert_notebook(db, state="protected", origin="protected")
    rows = db.execute(
        "SELECT notebook_id FROM notebooks WHERE state IN (?,?)",
        archive_db.DELETABLE_STATES,
    ).fetchall()
    assert rows == []


def test_artifact_without_an_id_cannot_duplicate(db):
    """UNIQUE(a, b) admits unlimited duplicates when b IS NULL, and nlm omits
    artifact_id for some artifacts — hence NOT NULL plus a surrogate."""
    _insert_notebook(db)
    sur = archive_db.surrogate_artifact_id("report", "T", "2026-01-01")
    assert sur == archive_db.surrogate_artifact_id("report", "T", "2026-01-01")
    with archive_db.write_txn(db):
        db.execute(
            "INSERT INTO artifacts (notebook_id,nlm_artifact_id,kind,status)"
            " VALUES (?,?,?,?)", (NB, sur, "report", "downloaded"))
    with pytest.raises(sqlite3.IntegrityError):
        with archive_db.write_txn(db):
            db.execute(
                "INSERT INTO artifacts (notebook_id,nlm_artifact_id,kind,status)"
                " VALUES (?,?,?,?)", (NB, sur, "report", "downloaded"))
    with pytest.raises(sqlite3.IntegrityError):
        with archive_db.write_txn(db):
            db.execute(
                "INSERT INTO artifacts (notebook_id,nlm_artifact_id,kind,status)"
                " VALUES (?,?,?,?)", (NB, None, "report", "downloaded"))


def test_entering_capturing_clears_a_stale_verdict(db):
    _insert_notebook(db)
    with archive_db.write_txn(db):
        archive_db.set_state(db, NB, "captured", verified_at="2026-09-01T00:00:00Z",
                             manifest_sha="abc")
    with archive_db.write_txn(db):
        archive_db.set_state(db, NB, "capturing")
    row = db.execute("SELECT verified_at, manifest_sha FROM notebooks WHERE notebook_id=?",
                     (NB,)).fetchone()
    assert row["verified_at"] is None and row["manifest_sha"] is None


def test_transitions_are_recorded(db):
    _insert_notebook(db)
    with archive_db.write_txn(db):
        archive_db.set_state(db, NB, "capturing", run_id="r1")
        archive_db.set_state(db, NB, "captured", run_id="r1")
    events = db.execute(
        "SELECT to_state FROM notebook_events WHERE notebook_id=? ORDER BY id", (NB,)
    ).fetchall()
    assert [e["to_state"] for e in events] == ["capturing", "captured"]


# ── Authority ─────────────────────────────────────────────────────────────────

def test_daily_authority_only_permits_what_this_run_created():
    auth = DailyAuthority("daily_2026-09-29")
    assert not auth.permits(NB)
    auth.claim(NB)
    assert auth.permits(NB)
    assert not auth.permits(NB2)


def test_reclaim_authority_does_not_permit_a_new_notebook():
    """A frozen file cannot authorise tomorrow's notebook — which is why the
    daily path needs its own authority rather than appending to the allowlist."""
    auth = ReclaimAuthority(
        {NB: FrozenNotebook(NB, "T", 5, "2026-08-16T00:00:00Z", "daily")}, set(), "d")
    assert auth.permits(NB)
    assert not auth.permits(NB2)


def test_limit_may_lower_the_cap_but_never_raise_it():
    assert cap_for_run(None) == MAX_DELETES_PER_RUN
    assert cap_for_run(5) == 5
    assert cap_for_run(10_000) == MAX_DELETES_PER_RUN


def _write_allowlist(tmp_path, ids, excluded=(), digest=None):
    allow = tmp_path / "backfill-allowlist.json"
    excl = tmp_path / "backfill-excluded.json"
    dig = tmp_path / "backfill-allowlist.sha256"
    allow.write_text(json.dumps({"notebooks": [
        {"notebook_id": i, "title": "T", "source_count": 1,
         "updated_at": "2026-08-16T00:00:00Z", "origin": "daily"} for i in ids]}))
    excl.write_text(json.dumps({"notebooks": [
        {"notebook_id": i, "reason": "personal"} for i in excluded]}))
    dig.write_text((digest or canonical_digest(ids)) + "\n")
    return allow, excl, dig


def test_allowlist_loads_when_the_digest_matches(tmp_path):
    paths = _write_allowlist(tmp_path, [NB, NB2])
    auth = load_allowlist(*paths)
    assert len(auth) == 2


def test_tampered_allowlist_is_refused(tmp_path):
    """A count check would still pass here — the digest is what catches a
    regeneration that swapped ids while keeping the total."""
    allow, excl, dig = _write_allowlist(tmp_path, [NB, NB2])
    doc = json.loads(allow.read_text())
    doc["notebooks"][0]["notebook_id"] = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    allow.write_text(json.dumps(doc))
    with pytest.raises(AllowlistError, match="digest mismatch"):
        load_allowlist(allow, excl, dig)


def test_an_id_cannot_be_both_allowed_and_excluded(tmp_path):
    allow, excl, dig = _write_allowlist(tmp_path, [NB, NB2], excluded=[NB])
    with pytest.raises(AllowlistError, match="both allowed and excluded"):
        load_allowlist(allow, excl, dig)


def test_notebooks_appearing_since_the_freeze_halt_the_run(tmp_path):
    allow, excl, dig = _write_allowlist(tmp_path, [NB])
    with pytest.raises(AllowlistError, match="since the freeze"):
        load_allowlist(allow, excl, dig, live_ids=[NB, NB2])


def test_non_uuid_in_allowlist_is_refused(tmp_path):
    allow, excl, dig = _write_allowlist(tmp_path, [NB])
    doc = json.loads(allow.read_text())
    doc["notebooks"].append({"notebook_id": "daily-2026-09-29", "title": "x"})
    allow.write_text(json.dumps(doc))
    with pytest.raises(AllowlistError):
        load_allowlist(allow, excl, dig)


# ── The gate ──────────────────────────────────────────────────────────────────

def test_vacuous_zero_source_count_does_not_pass():
    assert not cv.check_source_counts(cv.Verdict(), 0, 0, 0)


def test_disagreeing_source_counts_do_not_pass():
    assert not cv.check_source_counts(cv.Verdict(), 16, 16, 12)
    assert cv.check_source_counts(cv.Verdict(), 16, 16, 16)


def test_failed_download_item_blocks_deletion():
    """A failed streaming download leaves a partial file exactly where a good
    one would be, so existence and magic bytes both still pass."""
    payload = {"items": [{"artifact_type": "audio", "success": False,
                          "error": "connection reset", "path": "/x.m4a"}],
               "total_artifacts": 1}
    assert not cv.check_download_result(cv.Verdict(), payload, 1)


def test_unsupported_skip_blocks_deletion():
    payload = {"items": [], "skipped": [{"reason": "unsupported artifact type"}]}
    assert not cv.check_download_result(cv.Verdict(), payload, None)
    payload = {"items": [], "skipped": [{"reason": "already downloaded"}]}
    assert cv.check_download_result(cv.Verdict(), payload, None)


def test_artifact_count_drift_blocks_deletion():
    payload = {"items": [], "total_artifacts": 2}
    assert not cv.check_download_result(cv.Verdict(), payload, 3)


def test_non_terminal_artifact_blocks_deletion():
    """A deep-dive podcast that hit its generation deadline is still being made
    server-side; deleting now destroys it."""
    assert not cv.check_artifact_states(
        cv.Verdict(), [{"id": "a", "status": "in_progress"}], True)


def test_unknown_artifact_status_blocks_deletion():
    """nlm normalises an unrecognised status to 'unknown', so the terminal set
    must be an allowlist rather than a denylist."""
    assert not cv.check_artifact_states(
        cv.Verdict(), [{"id": "a", "status": "unknown"}], True)
    assert cv.check_artifact_states(
        cv.Verdict(), [{"id": "a", "status": "completed"}], True)


def test_failed_status_call_blocks_deletion():
    assert not cv.check_artifact_states(cv.Verdict(), None, False)


def _artifact_row(path, **kw):
    row = {"kind": "report", "file_path": path, "status": "downloaded",
           "size_bytes": None}
    row.update(kw)
    return row


def test_zero_byte_artifact_blocks_deletion(tmp_path):
    nd = notebook_dir(tmp_path, NB)
    (nd / "artifacts").mkdir(parents=True)
    (nd / "artifacts" / "r.md").write_text("")
    assert not cv.check_artifact_files(
        cv.Verdict(), tmp_path, NB, [_artifact_row("artifacts/r.md")])


def test_html_error_page_saved_as_markdown_blocks_deletion(tmp_path):
    nd = notebook_dir(tmp_path, NB)
    (nd / "artifacts").mkdir(parents=True)
    (nd / "artifacts" / "r.md").write_text("<!DOCTYPE html>\n<html><body>429</body>")
    assert not cv.check_artifact_files(
        cv.Verdict(), tmp_path, NB, [_artifact_row("artifacts/r.md")])


def test_truncated_artifact_blocks_deletion(tmp_path):
    """Size is compared against what was recorded at download time."""
    nd = notebook_dir(tmp_path, NB)
    (nd / "artifacts").mkdir(parents=True)
    (nd / "artifacts" / "r.md").write_text("# real report body")
    assert not cv.check_artifact_files(
        cv.Verdict(), tmp_path, NB,
        [_artifact_row("artifacts/r.md", size_bytes=99999)])


def test_missing_artifact_blocks_deletion(tmp_path):
    notebook_dir(tmp_path, NB).mkdir(parents=True)
    assert not cv.check_artifact_files(
        cv.Verdict(), tmp_path, NB, [_artifact_row(None, status="missing")])


def test_silent_audio_blocks_deletion(tmp_path):
    nd = notebook_dir(tmp_path, NB)
    (nd / "artifacts").mkdir(parents=True)
    (nd / "artifacts" / "a.m4a").write_bytes(b"\x00\x00\x00\x20ftypM4A ")
    assert not cv.check_artifact_files(
        cv.Verdict(), tmp_path, NB, [_artifact_row("artifacts/a.m4a", kind="audio")],
        media_probe=lambda p: 0.0)
    assert cv.check_artifact_files(
        cv.Verdict(), tmp_path, NB, [_artifact_row("artifacts/a.m4a", kind="audio")],
        media_probe=lambda p: 2582.1)


def test_hardlinked_artifact_blocks_deletion(tmp_path):
    """sha256 cannot distinguish a hardlink into another notebook's capture from
    a genuine copy — which is exactly the duplicate-title hazard."""
    a = notebook_dir(tmp_path, NB) / "artifacts"
    b = notebook_dir(tmp_path, NB2) / "artifacts"
    a.mkdir(parents=True); b.mkdir(parents=True)
    (a / "r.md").write_text("# body")
    (b / "r.md").hardlink_to(a / "r.md")
    seen = {}
    assert cv.check_artifact_files(
        cv.Verdict(), tmp_path, NB, [_artifact_row("artifacts/r.md")], seen_inodes=seen)
    assert not cv.check_artifact_files(
        cv.Verdict(), tmp_path, NB2, [_artifact_row("artifacts/r.md")], seen_inodes=seen)


def test_volume_change_blocks_deletion(tmp_path):
    """If the external volume unmounts mid-run, writes land on the boot disk's
    empty mountpoint and every other check still passes."""
    assert not cv.check_volume(cv.Verdict(), tmp_path, expected_device=-1)
    from lib.archive_paths import volume_device_id
    assert cv.check_volume(cv.Verdict(), tmp_path, volume_device_id(tmp_path))


def test_source_with_non_ok_status_blocks_deletion():
    assert not cv.check_source_content(
        cv.Verdict(), [{"nlm_source_id": "s1", "content_status": "error",
                        "source_type": "web_page", "char_count": 0}])


def test_cookie_wall_sized_capture_blocks_deletion():
    """A 403 or cookie wall clears a naive non-empty check."""
    assert not cv.check_source_content(
        cv.Verdict(), [{"nlm_source_id": "s1", "content_status": "ok",
                        "source_type": "web_page", "char_count": 120}])
    assert cv.check_source_content(
        cv.Verdict(), [{"nlm_source_id": "s1", "content_status": "ok",
                        "source_type": "web_page", "char_count": 9000}])


def test_companion_notes_are_allowed_to_be_short():
    assert cv.check_source_content(
        cv.Verdict(), [{"nlm_source_id": "s1", "content_status": "ok",
                        "source_type": "generated_text", "char_count": 57}])


def test_frozen_drift_blocks_deletion():
    frozen = FrozenNotebook(NB, "Daily Feed — 2026-08-16", 5,
                            "2026-08-16T12:00:00Z", "daily")
    auth = ReclaimAuthority({NB: frozen}, set(), "d")
    live_same = {"title": frozen.title, "source_count": 5,
                 "updated_at": frozen.updated_at}
    assert cv.check_frozen_drift(cv.Verdict(), auth, NB, live_same)

    for field, value in [("title", "Renamed"), ("source_count", 6),
                         ("updated_at", "2026-09-29T00:00:00Z")]:
        live = dict(live_same, **{field: value})
        assert not cv.check_frozen_drift(cv.Verdict(), auth, NB, live), field


# ── Capture promotion ─────────────────────────────────────────────────────────

def test_promote_preserves_a_previous_capture(tmp_path):
    """A re-capture must never destroy the capture already on disk — the
    archive is the only copy those artifacts have."""
    from pipeline import notebook_capture as nc

    run_a, run_b = "reclaim_A", "reclaim_B"
    s1 = staging_dir(tmp_path, NB, run_a)
    (s1 / "artifacts").mkdir(parents=True)
    (s1 / "artifacts" / "first.md").write_text("original capture")
    nc.promote(tmp_path, NB, run_a)

    final = notebook_dir(tmp_path, NB) / "capture"
    assert (final / "artifacts" / "first.md").read_text() == "original capture"

    s2 = staging_dir(tmp_path, NB, run_b)
    (s2 / "artifacts").mkdir(parents=True)
    (s2 / "artifacts" / "second.md").write_text("new capture")
    nc.promote(tmp_path, NB, run_b)

    assert (final / "artifacts" / "second.md").read_text() == "new capture"
    superseded = list(notebook_dir(tmp_path, NB).glob(".superseded-*"))
    assert superseded, "the earlier capture was destroyed"
    assert (superseded[0] / "artifacts" / "first.md").read_text() == "original capture"


def test_capture_discards_only_its_own_staging(tmp_path):
    """Restarting a notebook clears this run's partials, not a good capture."""
    from pipeline import notebook_capture as nc

    final = notebook_dir(tmp_path, NB) / "capture"
    (final / "artifacts").mkdir(parents=True)
    (final / "artifacts" / "good.md").write_text("good")

    s = staging_dir(tmp_path, NB, "reclaim_C")
    (s / "artifacts").mkdir(parents=True)
    (s / "artifacts" / "partial.md").write_text("half a file")

    import shutil
    shutil.rmtree(s)
    assert (final / "artifacts" / "good.md").exists()


def test_thin_source_can_be_accepted_explicitly():
    """A placeholder URL such as example.com returns ~179 characters, and that
    is the whole page. Fail closed by default; let an operator opt in."""
    rows = [{"nlm_source_id": "s1", "content_status": "ok",
             "source_type": "web_page", "char_count": 179}]
    assert not cv.check_source_content(cv.Verdict(), rows)
    assert cv.check_source_content(cv.Verdict(), rows, allow_thin=True)


def test_allow_thin_does_not_excuse_a_failed_capture():
    rows = [{"nlm_source_id": "s1", "content_status": "error",
             "source_type": "web_page", "char_count": 0}]
    assert not cv.check_source_content(cv.Verdict(), rows, allow_thin=True)


def test_provably_empty_notebook_needs_an_explicit_flag():
    """Some runs created a notebook and died before adding sources. Agreement
    between today's zero and the frozen snapshot's zero is independent
    evidence — but reclaiming it is still a deliberate choice."""
    assert not cv.check_source_counts(cv.Verdict(), 0, 0, 0, frozen_count=0)
    assert cv.check_source_counts(cv.Verdict(), 0, 0, 0, frozen_count=0,
                                  allow_empty=True)


def test_transient_empty_list_is_refused_even_with_allow_empty():
    """The frozen snapshot says 16 sources; today's call says none. That is a
    failed call, not an empty notebook."""
    assert not cv.check_source_counts(cv.Verdict(), 0, 0, 0, frozen_count=16,
                                      allow_empty=True)


def test_needs_human_is_not_retried_automatically(tmp_path):
    """The gate refused; re-running re-downloads every artifact only to refuse
    again, and the set grows so each batch gets slower."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "reclaim", PROJECT_ROOT / "src" / "pipeline" / "reclaim_notebooks.py")
    reclaim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reclaim)

    conn = archive_db.open_db(tmp_path / "research.db")
    with archive_db.write_txn(conn):
        for nid, state in ((NB, "needs_human"), (NB2, "deleted")):
            conn.execute(
                "INSERT INTO notebooks (notebook_id,title,origin,state,deleted_at,"
                "captured_at) VALUES (?,?,?,?,?,?)",
                (nid, "T", "daily", state,
                 "2026-09-29T00:00:00Z" if state == "deleted" else None,
                 "2026-09-29T00:00:00Z"))

    assert NB in reclaim.skip_ids(conn)
    assert NB not in reclaim.skip_ids(conn, retry_needs_human=True)
    assert NB2 in reclaim.skip_ids(conn, retry_needs_human=True)


# ── Source expiry ─────────────────────────────────────────────────────────────

def test_expiry_moves_rows_rather_than_mutating_them(tmp_path, monkeypatch):
    """data/ is gitignored and unbacked, so an in-place status flip would be the
    only record and a bad run would be unrecoverable."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "expire", PROJECT_ROOT / "src" / "pipeline" / "expire_sources.py")
    expire = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(expire)

    from lib.file_utils import load_json, save_json
    data = tmp_path
    save_json(data / "sources.json", {"sources": [
        {"id": "old", "status": "queued", "added_at": "2020-01-01T00:00:00Z"},
        {"id": "fresh", "status": "queued", "added_at": "2099-01-01T00:00:00Z"},
        {"id": "linked", "status": "processing", "added_at": "2020-01-01T00:00:00Z",
         "notebook_id": NB},
        {"id": "done", "status": "completed", "added_at": "2020-01-01T00:00:00Z"},
    ]})
    monkeypatch.setattr(sys, "argv", ["expire", "--data-dir", str(data), "--days", "2"])
    expire.main()

    live = {s["id"] for s in load_json(data / "sources.json")["sources"]}
    assert live == {"fresh", "linked", "done"}, "wrong rows expired"

    archived = load_json(
        data / "archive" / f"sources-expired-{__import__('datetime').datetime.now().strftime('%Y-%m')}.json",
        default={"sources": []})["sources"]
    assert [s["id"] for s in archived] == ["old"]
    assert archived[0]["status"] == "expired"
    assert archived[0]["expired_reason"]


# ── Resume semantics ──────────────────────────────────────────────────────────

def _reclaim_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "reclaim", PROJECT_ROOT / "src" / "pipeline" / "reclaim_notebooks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("state,expect_reprocessed", [
    ("pending", True),          # never started
    ("capturing", True),        # crashed mid-capture; capture is idempotent and
                                # promote sets any prior capture aside
    ("captured", True),         # verified but not deleted; re-verify and delete
    ("delete_pending", True),   # delete may or may not have landed; retry is safe
    ("deleted", False),         # terminal
    ("protected", False),       # terminal, and never selectable
    ("needs_human", False),     # the gate refused; retrying just refuses again
])
def test_resume_decision_per_state(tmp_path, state, expect_reprocessed):
    """Killing the process at any point must resume to a correct decision.

    Resume is derived entirely from committed state plus the live account, so
    there is no in-memory progress to lose.
    """
    reclaim = _reclaim_module()
    conn = archive_db.open_db(tmp_path / "research.db")
    with archive_db.write_txn(conn):
        conn.execute(
            "INSERT INTO notebooks (notebook_id,title,origin,state,captured_at,deleted_at)"
            " VALUES (?,?,?,?,?,?)",
            (NB, "Daily Feed — 2026-08-16", "daily", state,
             "2026-09-29T00:00:00Z",
             "2026-09-29T00:00:00Z" if state == "deleted" else None),
        )
    skipped = reclaim.skip_ids(conn)
    assert (NB not in skipped) == expect_reprocessed


def test_deleted_notebook_is_not_reprocessed_even_if_still_listed(tmp_path):
    """Idempotency: a second full pass must be a no-op, not a second delete."""
    reclaim = _reclaim_module()
    conn = archive_db.open_db(tmp_path / "research.db")
    with archive_db.write_txn(conn):
        conn.execute(
            "INSERT INTO notebooks (notebook_id,title,origin,state,captured_at,deleted_at)"
            " VALUES (?,?,'daily','deleted',?,?)",
            (NB, "T", "2026-09-29T00:00:00Z", "2026-09-29T00:00:00Z"))
    auth = ReclaimAuthority(
        {NB: FrozenNotebook(NB, "T", 5, "2026-08-16T00:00:00Z", "daily")}, set(), "d")
    order = reclaim.build_order(
        auth, {NB: {"id": NB, "updated_at": "2026-08-16T00:00:00Z"}},
        reclaim.skip_ids(conn))
    assert order == []


# ── Artifact generation wait ──────────────────────────────────────────────────

def _daily_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "daily_cd", PROJECT_ROOT / "src" / "pipeline" / "daily_capture_delete.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_empty_status_is_not_treated_as_finished(monkeypatch):
    """Reproduces the real failure: seconds after `report create` the artifact
    is not registered yet, so the status is empty. The old shell poll read that
    as done, logged 'completed in 0s', and the report download then failed."""
    daily = _daily_module()
    sequence = [
        (True, []),                                            # not registered yet
        (True, [{"type": "report", "status": "in_progress"}]),
        (True, [{"type": "report", "status": "completed"},
                {"type": "audio", "status": "in_progress"}]),
        (True, [{"type": "report", "status": "completed"},
                {"type": "audio", "status": "completed"}]),
    ]
    calls = {"n": 0}

    def fake_status(_notebook_id):
        result = sequence[min(calls["n"], len(sequence) - 1)]
        calls["n"] += 1
        return result

    monkeypatch.setattr(daily.client, "studio_status", fake_status)
    assert daily.wait_for_artifacts(NB, ["report", "audio"], timeout=30, interval=0)
    assert calls["n"] == 4, "returned before both artifacts were confirmed"


def test_failed_generation_is_reported_not_waited_out(monkeypatch):
    daily = _daily_module()
    monkeypatch.setattr(daily.client, "studio_status", lambda _n: (
        True, [{"type": "report", "status": "completed"},
               {"type": "audio", "status": "failed"}]))
    assert not daily.wait_for_artifacts(NB, ["report", "audio"], timeout=30, interval=0)


def test_unavailable_status_keeps_waiting_rather_than_assuming_done(monkeypatch):
    daily = _daily_module()
    monkeypatch.setattr(daily.client, "studio_status", lambda _n: (False, None))
    assert not daily.wait_for_artifacts(NB, ["report"], timeout=1, interval=0)


def test_an_empty_source_does_not_block_deletion():
    """'empty' means nlm answered and the source has no extractable text — an
    answer, not a failure. Blocking on it stranded 'Daily Feed — 2026-10-07'
    (13 sources, artifacts captured, completeness 'full') on one empty source,
    which is how the account starts growing again."""
    rows = [
        {"nlm_source_id": "s1", "content_status": "ok",
         "source_type": "web_page", "char_count": 9000},
        {"nlm_source_id": "s2", "content_status": "empty",
         "source_type": "web_page", "char_count": 0},
    ]
    assert cv.check_source_content(cv.Verdict(), rows)


def test_a_failed_source_capture_still_blocks_deletion():
    """'error' means the call failed — we do not know what was in that source."""
    rows = [
        {"nlm_source_id": "s1", "content_status": "ok",
         "source_type": "web_page", "char_count": 9000},
        {"nlm_source_id": "s2", "content_status": "error",
         "source_type": "web_page", "char_count": 0},
    ]
    assert not cv.check_source_content(cv.Verdict(), rows)


def test_all_sources_empty_blocks_deletion():
    """Every source empty is indistinguishable from a wholesale capture failure,
    so it is refused however each row is labelled."""
    rows = [
        {"nlm_source_id": "s1", "content_status": "empty",
         "source_type": "web_page", "char_count": 0},
        {"nlm_source_id": "s2", "content_status": "empty",
         "source_type": "web_page", "char_count": 0},
    ]
    assert not cv.check_source_content(cv.Verdict(), rows)
