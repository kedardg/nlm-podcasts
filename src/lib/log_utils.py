"""Structured JSON logging for OpenClaw pipeline scripts.

Emits one JSON line per event to stderr. Human-readable logs
continue via print() to stdout; structured logs go to stderr
for parsing by monitoring tools.
"""

import json
import sys
import time
from datetime import datetime, timezone


def log_event(event, step=None, status="info", duration_ms=None, error=None, **extra):
    """Emit one JSON log line to stderr."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
    }
    if step:
        entry["step"] = step
    if status != "info":
        entry["status"] = status
    if duration_ms is not None:
        entry["duration_ms"] = round(duration_ms)
    if error:
        entry["error"] = str(error)
    entry.update(extra)
    print(json.dumps(entry, separators=(",", ":")), file=sys.stderr)


class StepTimer:
    """Context manager that logs step duration."""

    def __init__(self, step_name):
        self.step = step_name
        self.start = None

    def __enter__(self):
        self.start = time.monotonic()
        log_event("step_start", step=self.step)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        ms = (time.monotonic() - self.start) * 1000
        if exc_type:
            log_event("step_end", step=self.step, status="fail",
                      duration_ms=ms, error=str(exc_val))
        else:
            log_event("step_end", step=self.step, status="ok", duration_ms=ms)
        return False
