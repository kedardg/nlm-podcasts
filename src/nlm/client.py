"""Typed wrappers over the `nlm` CLI.

Transport only: no database, no vector store, no policy. Keeping this layer
free of those lets the verification gate be exercised entirely offline.

Every call is an argv list — never a shell string — because notebook titles in
this account contain literal tabs, ``\\x02``, ``/``, quotes and em dashes.
"""

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

NLM_BIN = os.environ.get("NLM_BIN") or str(Path.home() / ".local/bin/nlm")
FFPROBE = "/opt/homebrew/bin/ffprobe"


class NlmError(RuntimeError):
    pass


@dataclass
class CommandResult:
    ok: bool
    stdout: str
    stderr: str

    def json(self):
        try:
            return json.loads(self.stdout)
        except json.JSONDecodeError as exc:
            raise NlmError(f"non-JSON response: {self.stdout[:200]}") from exc


def _run(args, timeout=600) -> CommandResult:
    try:
        proc = subprocess.run(
            [NLM_BIN, *args], capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return CommandResult(False, "", f"timed out after {timeout}s")
    return CommandResult(proc.returncode == 0, proc.stdout, proc.stderr)


def notebook_list(timeout=300):
    res = _run(["notebook", "list", "--json"], timeout=timeout)
    if not res.ok:
        raise NlmError(f"notebook list failed: {res.stderr[:300]}")
    return res.json()


def studio_status(notebook_id, timeout=180):
    """Return ``(call_succeeded, artifacts)``.

    The caller must treat a failed call as "artifact state unknown" rather than
    "no artifacts" — the difference decides whether a notebook may be deleted.
    """
    res = _run(["studio", "status", notebook_id, "--json"], timeout=timeout)
    if not res.ok:
        return False, None
    try:
        payload = res.json()
    except NlmError:
        return False, None
    if isinstance(payload, dict):
        payload = payload.get("artifacts", [])
    return True, payload


def source_list(notebook_id, timeout=180):
    res = _run(["source", "list", notebook_id, "--json"], timeout=timeout)
    if not res.ok:
        raise NlmError(f"source list failed: {res.stderr[:300]}")
    payload = res.json()
    if isinstance(payload, dict):
        payload = payload.get("sources", [])
    return payload


def source_content(source_id, timeout=180):
    """Extracted text for one source, or None if nlm could not produce it."""
    res = _run(["source", "content", source_id, "--json"], timeout=timeout)
    if not res.ok:
        return None
    try:
        return (res.json() or {}).get("content")
    except NlmError:
        return None


def download_all(notebook_id, output_dir, types=None, timeout=3600):
    """Download every completed artifact into ``output_dir``.

    ``--skip-existing`` is deliberately NOT passed: nlm names its subdirectory
    after the notebook *title*, and four titles are duplicated across nine
    notebooks, so a shared output root plus skip-existing would let one
    notebook's capture silently "succeed" against another's files. Callers pass
    a per-notebook-id directory, and every artifact is fetched fresh.
    """
    args = ["download", "all", notebook_id, "-d", str(output_dir),
            "--no-progress", "--json"]
    if types:
        args += ["--types", ",".join(types)]
    res = _run(args, timeout=timeout)
    if not res.ok:
        raise NlmError(f"download all failed: {res.stderr[:300]}")
    return res.json()


def delete_notebook(notebook_id, timeout=120) -> CommandResult:
    """Irreversible. Callers must have passed the verification gate."""
    return _run(["delete", "notebook", notebook_id, "--confirm"], timeout=timeout)


def delete_alias(alias, timeout=60) -> CommandResult:
    """Aliases outlive their notebook, and `delete notebook` accepts an alias."""
    return _run(["delete", "alias", alias], timeout=timeout)


def ffprobe_duration(path):
    """Seconds of playable media, or None. Used to reject silent/empty audio."""
    ffprobe = FFPROBE if Path(FFPROBE).exists() else "ffprobe"
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None
