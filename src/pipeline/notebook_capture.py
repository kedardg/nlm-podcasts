"""Capture a notebook's artifacts and sources to disk, then prove it worked.

Order matters. Capture writes into a per-run staging directory and is promoted
by a single rename only after the database transaction commits, so a crashed
re-capture can never destroy a previously good capture — the archive is the
only copy those artifacts have.

Nothing here deletes anything. Deletion lives behind :func:`delete_if_verified`,
which runs the gate first and refuses on any doubt.
"""

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from lib import archive_db
from lib.archive_paths import (
    assert_notebook_id, notebook_dir, safe_archive_path, staging_dir,
    volume_device_id,
)
from lib.file_utils import now_iso
from nlm import client
from pipeline import capture_verify as cv

# Extensions by artifact kind, so stored filenames derive from the artifact id
# rather than its title. Titles reach ffmpeg, curl and the PDF renderer
# downstream, and this account's titles contain tabs, control bytes and slashes.
_EXT = {
    "report": ".md", "audio": ".m4a", "video": ".mp4", "mind_map": ".json",
    "slide_deck": ".pdf", "infographic": ".png", "data_table": ".csv",
    "quiz": ".json", "flashcards": ".json",
}

DELETED_IDS_LOG = "deleted-ids.log"


@dataclass
class CaptureResult:
    notebook_id: str
    ok: bool = True
    error: str = None
    studio_ok: bool = False
    studio_artifacts: list = field(default_factory=list)
    download_json: dict = None
    artifact_rows: list = field(default_factory=list)
    source_rows: list = field(default_factory=list)
    source_count_enumerated: int = 0
    source_count_listed: int = 0
    completeness: str = "nothing"

    def fail(self, message):
        self.ok = False
        self.error = message
        return self


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _artifact_id(item):
    aid = item.get("artifact_id")
    if isinstance(aid, str) and aid:
        return aid
    # nlm omits artifact_id for some artifacts. A surrogate keeps the UNIQUE
    # constraint meaningful — UNIQUE admits unlimited NULL duplicates.
    return archive_db.surrogate_artifact_id(
        item.get("artifact_type"), item.get("title"), item.get("created_at")
    )


def capture(data_dir, notebook_id, live_meta, run_id, *, types=None):
    """Pull everything off a notebook into staging. Never deletes."""
    assert_notebook_id(notebook_id)
    result = CaptureResult(notebook_id=notebook_id)
    result.source_count_enumerated = live_meta.get("source_count") or 0

    staging = staging_dir(data_dir, notebook_id, run_id)
    if staging.exists():
        shutil.rmtree(staging)          # only ever discards this run's partials
    (staging / "artifacts").mkdir(parents=True)
    (staging / "sources").mkdir(parents=True)

    # 1. What does the notebook think it has? Persisted as the audit trail for
    #    what we believed at capture time.
    ok, artifacts = client.studio_status(notebook_id)
    result.studio_ok = ok
    result.studio_artifacts = artifacts or []
    (staging / "studio-status.json").write_text(
        json.dumps({"call_ok": ok, "artifacts": result.studio_artifacts}, indent=2)
    )
    if not ok:
        return result.fail("studio status call failed; artifact state unknown")

    # 2. Artifacts.
    if result.studio_artifacts:
        try:
            payload = client.download_all(
                notebook_id, staging / "artifacts", types=types
            )
        except client.NlmError as exc:
            return result.fail(str(exc))
        result.download_json = payload
        (staging / "download.json").write_text(json.dumps(payload, indent=2))

        for item in payload.get("items", []):
            aid = _artifact_id(item)
            kind = item.get("artifact_type") or "unknown"
            row = {
                "nlm_artifact_id": aid, "kind": kind, "title": item.get("title"),
                "file_path": None, "size_bytes": None, "checksum": None,
                "st_ino": None, "status": "failed" if not item.get("success") else "downloaded",
                "discarded_reason": None,
            }
            src = item.get("path")
            if item.get("success") and src and Path(src).exists():
                # Rename off the title-derived name nlm chose.
                dest = staging / "artifacts" / f"{aid}{_EXT.get(kind, '.bin')}"
                os.replace(src, dest)
                st = dest.stat()
                row.update(
                    file_path=f"capture/artifacts/{dest.name}",
                    size_bytes=st.st_size, st_ino=st.st_ino,
                    checksum=_sha256(dest),
                )
            result.artifact_rows.append(row)

    # 3. Source text — the part that survives the notebook and feeds retrieval.
    try:
        sources = client.source_list(notebook_id)
    except client.NlmError as exc:
        return result.fail(str(exc))
    result.source_count_listed = len(sources)

    for src in sources:
        sid = src.get("id")
        row = {
            "nlm_source_id": sid, "title": src.get("title"), "url": src.get("url"),
            "source_type": src.get("type"), "content_path": None,
            "char_count": 0, "content_status": "error",
        }
        text = client.source_content(sid) if sid else None
        if text:
            out = staging / "sources" / f"{sid}.txt"
            out.write_text(text)
            row.update(content_path=f"capture/sources/{out.name}",
                       char_count=len(text), content_status="ok")
        elif text == "":
            row["content_status"] = "empty"
        result.source_rows.append(row)

    have_artifacts = any(r["status"] == "downloaded" for r in result.artifact_rows)
    have_sources = any(r["content_status"] == "ok" for r in result.source_rows)
    result.completeness = (
        "full" if have_artifacts and have_sources
        else "sources_only" if have_sources
        else "nothing"
    )

    manifest = {
        "notebook_id": notebook_id, "run_id": run_id, "captured_at": now_iso(),
        "title": live_meta.get("title"), "completeness": result.completeness,
        "artifacts": result.artifact_rows, "sources": result.source_rows,
    }
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return result


def promote(data_dir, notebook_id, run_id):
    """Move this run's staging into place with a single rename.

    An existing capture is set aside rather than overwritten — if this run
    turns out to be worse, the earlier one is still on disk.
    """
    staging = staging_dir(data_dir, notebook_id, run_id)
    final = notebook_dir(data_dir, notebook_id) / "capture"
    if final.exists():
        os.replace(final, final.with_name(f".superseded-{now_iso().replace(':', '')}"))
    os.replace(staging, final)
    return final


def persist(conn, notebook_id, result, run_id, live_meta):
    """Write the capture to the archive. One transaction, no I/O inside it."""
    with archive_db.write_txn(conn):
        conn.execute(
            "INSERT INTO notebooks (notebook_id, run_id, title, origin, state,"
            " source_count_enumerated, source_count_listed, source_count_rows,"
            " capture_completeness, studio_status_json)"
            " VALUES (?,?,?,?,'pending',?,?,?,?,?)"
            " ON CONFLICT(notebook_id) DO UPDATE SET"
            "  run_id=excluded.run_id,"
            "  source_count_enumerated=excluded.source_count_enumerated,"
            "  source_count_listed=excluded.source_count_listed,"
            "  source_count_rows=excluded.source_count_rows,"
            "  capture_completeness=excluded.capture_completeness,"
            "  studio_status_json=excluded.studio_status_json",
            (notebook_id, run_id, live_meta.get("title") or "",
             live_meta.get("origin") or "daily",
             result.source_count_enumerated, result.source_count_listed,
             len(result.source_rows), result.completeness,
             json.dumps(result.studio_artifacts)),
        )
        for row in result.artifact_rows:
            conn.execute(
                "INSERT INTO artifacts (notebook_id, nlm_artifact_id, kind, title,"
                " file_path, size_bytes, checksum, st_ino, status, downloaded_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(notebook_id, nlm_artifact_id) DO UPDATE SET"
                "  file_path=excluded.file_path, size_bytes=excluded.size_bytes,"
                "  checksum=excluded.checksum, st_ino=excluded.st_ino,"
                "  status=excluded.status, downloaded_at=excluded.downloaded_at",
                (notebook_id, row["nlm_artifact_id"], row["kind"], row["title"],
                 row["file_path"], row["size_bytes"], row["checksum"],
                 row["st_ino"], row["status"], now_iso()),
            )
        for row in result.source_rows:
            conn.execute(
                "INSERT INTO sources (notebook_id, nlm_source_id, title, url,"
                " source_type, content_path, char_count, content_status)"
                " VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(notebook_id, nlm_source_id) DO UPDATE SET"
                "  content_path=excluded.content_path,"
                "  char_count=excluded.char_count,"
                "  content_status=excluded.content_status",
                (notebook_id, row["nlm_source_id"], row["title"], row["url"],
                 row["source_type"], row["content_path"], row["char_count"],
                 row["content_status"]),
            )
        archive_db.set_state(conn, notebook_id, "captured", run_id=run_id)


def verify(conn, data_dir, notebook_id, authority, live_meta, result,
           expected_device, seen_inodes=None, allow_thin_sources=False):
    """Run the full gate. Only an ``ok`` verdict may be followed by a delete."""
    v = cv.Verdict()
    cv.check_authority(v, authority, notebook_id)
    cv.check_frozen_drift(v, authority, notebook_id, live_meta)
    cv.check_volume(v, data_dir, expected_device)
    cv.check_artifact_states(v, result.studio_artifacts, result.studio_ok)
    if result.studio_artifacts:
        cv.check_download_result(v, result.download_json, len(result.studio_artifacts))
    cv.check_source_counts(
        v, result.source_count_enumerated, result.source_count_listed,
        len(result.source_rows),
    )
    cv.check_source_content(v, result.source_rows, allow_thin=allow_thin_sources)
    cv.check_artifact_files(
        v, data_dir, notebook_id, result.artifact_rows,
        expected_device=expected_device, media_probe=client.ffprobe_duration,
        seen_inodes=seen_inodes,
    )

    row = conn.execute(
        "SELECT state FROM notebooks WHERE notebook_id=?", (notebook_id,)
    ).fetchone()
    if row is None:
        v.fail("no archive row committed for this notebook")
    elif row["state"] not in archive_db.DELETABLE_STATES:
        v.fail(f"archive row is in state {row['state']!r}, not deletable")
    return v


def _log_deletion(data_dir, record):
    """Append a self-contained record, fsynced, *before* the delete call.

    An id alone would say that something was destroyed but not what. This is
    the only trace that survives if the database write fails afterwards, so it
    carries enough to reconstruct the loss.
    """
    for target in (Path(data_dir) / DELETED_IDS_LOG,
                   Path.home() / "openclaw-research-backups" / DELETED_IDS_LOG):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as fh:
                fh.write(json.dumps(record, separators=(",", ":")) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError:
            # The mirror is best-effort; the primary is on the same volume as
            # the archive it describes.
            continue


def delete_if_verified(conn, data_dir, notebook_id, authority, live_meta, result,
                       expected_device, run_id, *, seen_inodes=None, dry_run=True,
                       allow_thin_sources=False):
    """The only path to an irreversible delete. Returns ``(deleted, verdict)``."""
    verdict = verify(conn, data_dir, notebook_id, authority, live_meta, result,
                     expected_device, seen_inodes=seen_inodes,
                     allow_thin_sources=allow_thin_sources)
    if not verdict.ok:
        with archive_db.write_txn(conn):
            archive_db.set_state(
                conn, notebook_id, "needs_human", run_id=run_id,
                capture_error="; ".join(verdict.reasons)[:2000],
            )
        return False, verdict
    if dry_run:
        return False, verdict

    record = {
        "ts": now_iso(), "notebook_id": notebook_id,
        "title": live_meta.get("title"), "alias": live_meta.get("alias"),
        "source_count": result.source_count_listed,
        "source_urls_sha256": hashlib.sha256(
            json.dumps(sorted(r.get("url") or "" for r in result.source_rows)).encode()
        ).hexdigest(),
        "artifact_ids": [r["nlm_artifact_id"] for r in result.artifact_rows],
        "artifact_shas": [r["checksum"] for r in result.artifact_rows],
        "capture_completeness": result.completeness,
        "allowlist_digest": getattr(authority, "digest", None),
        "run_id": run_id,
    }
    _log_deletion(data_dir, record)

    with archive_db.write_txn(conn):
        archive_db.set_state(conn, notebook_id, "delete_pending", run_id=run_id)

    res = client.delete_notebook(notebook_id)
    if not res.ok:
        with archive_db.write_txn(conn):
            archive_db.set_state(conn, notebook_id, "needs_human", run_id=run_id,
                                 capture_error=f"delete failed: {res.stderr[:400]}")
        return False, verdict.fail(f"delete failed: {res.stderr[:200]}")

    alias = live_meta.get("alias")
    if alias:
        client.delete_alias(alias)   # aliases outlive their notebook

    with archive_db.write_txn(conn):
        archive_db.set_state(conn, notebook_id, "deleted", run_id=run_id)
    return True, verdict


def current_device(data_dir):
    return volume_device_id(data_dir)
