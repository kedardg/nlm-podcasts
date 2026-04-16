"""Shared file I/O utilities for OpenClaw Research Automation.

Provides atomic JSON save/load with advisory locking to prevent
data corruption from concurrent writes (e.g., overlapping cron runs).
"""

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def load_json(path, default=None):
    """Load JSON file, return default (empty dict) if missing or corrupt."""
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default if default is not None else {}
    except json.JSONDecodeError:
        return default if default is not None else {}


def save_json(path, data, mode=None):
    """Atomic write: tmp -> fsync -> rename. Advisory lock prevents concurrent writes.

    If ``mode`` is given (e.g. ``0o600``), the target file is created with
    exactly those permissions — avoiding the umask race of a post-write chmod.
    """
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    lock = path.with_suffix(path.suffix + ".lock")
    with open(lock, "w") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            if mode is not None:
                # Create tmp with restrictive perms from the start.
                if tmp.exists():
                    tmp.unlink()
                fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
            else:
                with open(tmp, "w") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
            tmp.replace(path)
            if mode is not None:
                # rename preserves tmp's mode, but enforce again in case of
                # pre-existing target with looser perms on some filesystems.
                os.chmod(path, mode)
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def load_news_queue(path):
    """Load data/news-queue.json with schema migration.

    Runtime code (newsletter_parser, blog_monitor) appends to
    ``news_queue["articles"]``. Older bootstraps or hand-edited files may use
    ``{"items": [...]}`` or omit the key; this helper guarantees callers get
    a dict with an ``articles`` list so subsequent appends are safe.
    """
    q = load_json(path, default={"articles": []})
    if not isinstance(q, dict):
        q = {"articles": []}
    if "articles" not in q:
        legacy = q.pop("items", None)
        q["articles"] = legacy if isinstance(legacy, list) else []
    return q


def now_iso():
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
