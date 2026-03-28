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


def save_json(path, data):
    """Atomic write: tmp -> fsync -> rename. Advisory lock prevents concurrent writes."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    lock = path.with_suffix(path.suffix + ".lock")
    with open(lock, "w") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(path)
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def now_iso():
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
