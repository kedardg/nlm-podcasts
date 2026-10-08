"""SQLite archive for captured NotebookLM notebooks.

The archive is the durable record of every notebook the pipeline created: its
sources, its generated artifacts, where each landed on disk, and whether it was
ultimately deleted upstream. Once a notebook's artifacts are here, the notebook
itself is disposable — which is the whole point, because the account is capped
at 500 and nothing in this repo ever deleted one.

Constraints are real constraints, not comments. Every column that carries a
state gets a ``CHECK``, because a typo that writes an unrecognised state is the
bug class that deletes the wrong thing.
"""

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

# Lifecycle. 'protected' is terminal and unreachable by any delete query: the
# 34 notebooks that are not ours (24 personal + 9 predating this repo + 1 empty)
# must not merely be absent from the allowlist, they must also be in a state the
# delete path cannot select.
NOTEBOOK_STATES = (
    "pending",
    "capturing",
    "captured",
    "delete_pending",
    "deleted",
    "deleted_unconfirmed",
    "needs_human",
    "protected",
)
DELETABLE_STATES = ("captured", "delete_pending")
NOTEBOOK_ORIGINS = ("daily", "weekly", "corrupt", "protected")
ARTIFACT_STATUSES = (
    "downloaded", "verified", "discarded_large", "failed", "missing",
)
SOURCE_STATUSES = ("ok", "empty", "error", "skipped")
RUN_KINDS = ("daily", "reclaim", "manual")
RUN_STATUSES = ("running", "ok", "fail")
COMPLETENESS = ("full", "sources_only", "nothing")


def _enum(col, values):
    joined = ", ".join(f"'{v}'" for v in values)
    return f"CHECK ({col} IN ({joined}))"


def _enum_nullable(col, values):
    joined = ", ".join(f"'{v}'" for v in values)
    return f"CHECK ({col} IS NULL OR {col} IN ({joined}))"


MIGRATION_001 = f"""
CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    kind         TEXT NOT NULL {_enum('kind', RUN_KINDS)},
    run_date     TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    completed_at TEXT,
    status       TEXT NOT NULL {_enum('status', RUN_STATUSES)}
);

CREATE TABLE IF NOT EXISTS notebooks (
    notebook_id             TEXT PRIMARY KEY,
    run_id                  TEXT REFERENCES runs(run_id) ON DELETE SET NULL,
    title                   TEXT NOT NULL,
    alias                   TEXT,
    origin                  TEXT NOT NULL {_enum('origin', NOTEBOOK_ORIGINS)},
    category                TEXT,
    source_count_enumerated INTEGER,
    source_count_listed     INTEGER,
    source_count_rows       INTEGER,
    state                   TEXT NOT NULL {_enum('state', NOTEBOOK_STATES)},
    capture_error           TEXT,
    capture_completeness    TEXT {_enum_nullable('capture_completeness', COMPLETENESS)},
    studio_status_json      TEXT,
    manifest_sha            TEXT,
    captured_at             TEXT,
    verified_at             TEXT,
    deleted_at              TEXT,
    -- A state implies its timestamp. Without this, a row can claim 'deleted'
    -- while carrying no record of when, which makes the audit trail a lie.
    CHECK (state <> 'deleted' OR deleted_at IS NOT NULL),
    CHECK (state <> 'captured' OR captured_at IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS sources (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    notebook_id    TEXT NOT NULL REFERENCES notebooks(notebook_id) ON DELETE CASCADE,
    nlm_source_id  TEXT NOT NULL,
    title          TEXT,
    url            TEXT,
    source_type    TEXT,
    content_path   TEXT,
    char_count     INTEGER NOT NULL DEFAULT 0,
    content_status TEXT NOT NULL {_enum('content_status', SOURCE_STATUSES)},
    indexed_at     TEXT,
    UNIQUE (notebook_id, nlm_source_id)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    notebook_id      TEXT NOT NULL REFERENCES notebooks(notebook_id) ON DELETE CASCADE,
    -- NOT NULL is load-bearing: nlm omits artifact_id for some artifacts, and
    -- UNIQUE(a, b) admits unlimited duplicate rows when b IS NULL. Callers pass
    -- surrogate_artifact_id() when nlm gives us nothing.
    nlm_artifact_id  TEXT NOT NULL,
    kind             TEXT NOT NULL,
    title            TEXT,
    file_path        TEXT,          -- relative to data/archive/notebooks/<id>/
    size_bytes       INTEGER,
    checksum         TEXT,
    st_ino           INTEGER,
    status           TEXT NOT NULL {_enum('status', ARTIFACT_STATUSES)},
    discarded_reason TEXT,
    downloaded_at    TEXT,
    delivered_at     TEXT,
    UNIQUE (notebook_id, nlm_artifact_id)
);

-- Append-only transition history. notebooks holds one row per notebook, so
-- retry history would otherwise be overwritten — unacceptable for a 465-item
-- irreversible operation.
CREATE TABLE IF NOT EXISTS notebook_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    notebook_id TEXT NOT NULL,
    at          TEXT NOT NULL,
    from_state  TEXT,
    to_state    TEXT NOT NULL,
    run_id      TEXT,
    detail      TEXT
);

CREATE INDEX IF NOT EXISTS idx_notebooks_state ON notebooks(state);
CREATE INDEX IF NOT EXISTS idx_notebooks_run   ON notebooks(run_id);
CREATE INDEX IF NOT EXISTS idx_sources_nb      ON sources(notebook_id);
CREATE INDEX IF NOT EXISTS idx_sources_indexed ON sources(indexed_at);
CREATE INDEX IF NOT EXISTS idx_artifacts_nb    ON artifacts(notebook_id);
CREATE INDEX IF NOT EXISTS idx_events_nb       ON notebook_events(notebook_id);
"""


def surrogate_artifact_id(kind, title, created_at) -> str:
    """Deterministic stand-in for an artifact nlm reports without an id."""
    raw = f"{kind}|{title or ''}|{created_at or ''}"
    return "sur_" + hashlib.sha256(raw.encode()).hexdigest()[:28]


def connect(db_path) -> sqlite3.Connection:
    """Open the archive with the pragmas the schema's guarantees depend on.

    ``foreign_keys`` is per-connection and defaults OFF, so the declared
    relationships do nothing unless set here. ``isolation_level=None`` hands
    transaction control to us so writes can use BEGIN IMMEDIATE.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=FULL")
    # fsync() on macOS does not flush the drive write cache; F_FULLFSYNC does.
    conn.execute("PRAGMA fullfsync=1")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("foreign_keys pragma did not take effect")
    return conn


def migrate(conn) -> int:
    """Apply forward-only migrations. Returns the resulting user_version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        # executescript() issues its own COMMIT, so it cannot run inside an
        # explicit transaction. Every statement is IF NOT EXISTS, so a partial
        # apply is safe to repeat.
        conn.executescript(MIGRATION_001)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        version = SCHEMA_VERSION
    return version


def open_db(db_path):
    conn = connect(db_path)
    migrate(conn)
    return conn


@contextmanager
def write_txn(conn):
    """A write transaction.

    BEGIN IMMEDIATE takes the write lock up front. A deferred transaction that
    reads then writes can fail with SQLITE_BUSY at COMMIT, where busy_timeout
    does not help.

    Never wrap a download, a subprocess call or any other I/O in this: holding
    the write lock across an 83 MB download starves the other pipeline far past
    busy_timeout.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def record_event(conn, notebook_id, from_state, to_state, run_id=None, detail=None, now=None):
    from lib.file_utils import now_iso
    conn.execute(
        "INSERT INTO notebook_events (notebook_id, at, from_state, to_state, run_id, detail)"
        " VALUES (?,?,?,?,?,?)",
        (notebook_id, now or now_iso(), from_state, to_state, run_id, detail),
    )


def set_state(conn, notebook_id, to_state, *, run_id=None, detail=None, **columns):
    """Move a notebook to ``to_state`` and log the transition.

    Entering 'capturing' clears verified_at and manifest_sha: a row verified on
    an earlier run must never carry that verdict into a fresh attempt.
    """
    from lib.file_utils import now_iso

    if to_state not in NOTEBOOK_STATES:
        raise ValueError(f"unknown state: {to_state!r}")
    row = conn.execute(
        "SELECT state FROM notebooks WHERE notebook_id=?", (notebook_id,)
    ).fetchone()
    from_state = row["state"] if row else None

    if to_state == "capturing":
        columns.setdefault("verified_at", None)
        columns.setdefault("manifest_sha", None)
    if to_state == "captured":
        columns.setdefault("captured_at", now_iso())
    if to_state == "deleted":
        columns.setdefault("deleted_at", now_iso())

    sets = ", ".join(f"{k}=?" for k in columns)
    params = list(columns.values()) + [to_state, notebook_id]
    conn.execute(
        f"UPDATE notebooks SET {sets + ', ' if sets else ''}state=? WHERE notebook_id=?",
        params,
    )
    record_event(conn, notebook_id, from_state, to_state, run_id, detail)
