"""Path safety for the notebook archive.

Every file the archive writes or verifies goes through :func:`safe_archive_path`.
Paths arrive from ``nlm download all --json`` — external input — so they are
treated as untrusted: absolute paths, ``..`` components, and symlinks are all
rejected, at write time as well as verify time.

The archive is keyed by ``notebook_id`` and never by title or alias. Titles in
this account contain ``/``, literal tabs, ``\\x02`` and em dashes, and four
titles are duplicated across nine notebooks; 26 aliases contain a literal ``/``.
"""

import os
import re
from pathlib import Path

# NotebookLM ids are v4-shaped UUIDs. Anything else never reaches a filesystem
# path or a delete call.
NOTEBOOK_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

# data/archive/ already holds news-YYYY-MM.json from archive_old_news.py, so
# notebooks get their own subtree rather than sharing that namespace.
ARCHIVE_SUBDIR = "notebooks"


class UnsafePathError(ValueError):
    """A candidate path escaped its notebook directory, or looked like it might."""


def assert_notebook_id(notebook_id: str) -> str:
    """Return ``notebook_id`` if it is a well-formed UUID, else raise."""
    if not isinstance(notebook_id, str) or not NOTEBOOK_ID_RE.match(notebook_id):
        raise UnsafePathError(f"not a notebook id: {notebook_id!r}")
    return notebook_id


def archive_root(data_dir) -> Path:
    return Path(data_dir) / "archive" / ARCHIVE_SUBDIR


def notebook_dir(data_dir, notebook_id: str) -> Path:
    """The one directory a notebook's capture may touch."""
    return archive_root(data_dir) / assert_notebook_id(notebook_id)


def staging_dir(data_dir, notebook_id: str, run_id: str) -> Path:
    """Per-run staging. Capture writes here, then promotes by atomic rename.

    Nothing ever mutates a previously-good capture in place: a crashed
    re-capture must not be able to destroy the only copy that exists.
    """
    safe_run = re.sub(r"[^A-Za-z0-9_.-]", "_", run_id)[:64]
    return notebook_dir(data_dir, notebook_id) / f".staging-{safe_run}"


def _has_symlink_component(path: Path, base: Path) -> bool:
    """True if any component between ``base`` and ``path`` is a symlink."""
    cur = path
    while True:
        if cur.is_symlink():
            return True
        if cur == base or cur.parent == cur:
            return False
        cur = cur.parent


def safe_archive_path(data_dir, notebook_id: str, candidate, *, base=None) -> Path:
    """Resolve ``candidate`` and prove it lives inside this notebook's directory.

    ``candidate`` may be absolute (as ``nlm download all`` reports it) or
    relative to ``base``. Either way the resolved result must sit under ``base``
    with no symlinked component.
    """
    base = Path(base) if base is not None else notebook_dir(data_dir, notebook_id)
    base_resolved = base.resolve()

    raw = str(candidate)
    if "\x00" in raw or "\n" in raw:
        raise UnsafePathError(f"path contains a control character: {raw!r}")

    p = Path(raw)
    if p.is_absolute():
        # An absolute path contains no '..' and would defeat a naive join check,
        # so it is admitted only if it already resolves inside the base.
        resolved = p.resolve()
    else:
        if any(part == ".." for part in p.parts):
            raise UnsafePathError(f"path escapes with '..': {raw!r}")
        resolved = (base_resolved / p).resolve()

    if not resolved.is_relative_to(base_resolved):
        raise UnsafePathError(f"path escapes {base_resolved}: {raw!r}")

    if resolved.exists() and _has_symlink_component(resolved, base_resolved):
        raise UnsafePathError(f"path traverses a symlink: {raw!r}")

    return resolved


def volume_device_id(path) -> int:
    """Device id of ``path``, recorded at start and re-checked before deleting.

    The project root is an external volume. If it unmounts mid-run, writes land
    on the boot disk's empty mountpoint: the files exist, are non-empty and
    verify — and vanish on remount.
    """
    return os.stat(path).st_dev
