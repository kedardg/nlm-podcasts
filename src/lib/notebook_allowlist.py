"""Who is allowed to delete a notebook, and on what evidence.

Deleting a NotebookLM notebook is irreversible and there is no undo, so
authority to delete is deliberately narrow and comes in exactly two flavours:

``ReclaimAuthority``
    For the one-off backlog pass. A notebook is deletable only if its id is in
    a frozen, digest-verified allowlist that a human approved once. Titles are
    never re-matched at delete time — a personal notebook created next year
    called "Notes - Week of 2027-01-04" must not become deletable by accident.

``DailyAuthority``
    For the steady state. A notebook is deletable only if this very process
    created it: the row carries the current ``run_id``, ``origin='daily'``, and
    reached ``captured`` in-process. A frozen file cannot authorise tomorrow's
    notebook, and a run-scoped authority must never reach into the backlog.

Neither falls back to the other.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from lib.archive_paths import NOTEBOOK_ID_RE

# A mistaken `--execute` must not be able to empty the account. --limit may
# lower this; nothing raises it.
MAX_DELETES_PER_RUN = 25


class AllowlistError(RuntimeError):
    """The allowlist is missing, malformed, or no longer describes reality."""


def canonical_digest(ids) -> str:
    """Digest of the id set, order-independent and whitespace-independent."""
    payload = json.dumps(sorted(ids), separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class FrozenNotebook:
    """What we believed about a notebook when the allowlist was frozen."""

    notebook_id: str
    title: str
    source_count: int
    updated_at: str
    origin: str


class ReclaimAuthority:
    """Delete authority for the backlog pass."""

    kind = "reclaim"

    def __init__(self, entries, excluded_ids, digest):
        self._entries = entries          # id -> FrozenNotebook
        self._excluded = set(excluded_ids)
        self.digest = digest

    def __len__(self):
        return len(self._entries)

    @property
    def ids(self):
        return set(self._entries)

    def frozen(self, notebook_id):
        return self._entries.get(notebook_id)

    def permits(self, notebook_id) -> bool:
        """Membership only. Drift is checked separately by the gate."""
        return notebook_id in self._entries and notebook_id not in self._excluded


class DailyAuthority:
    """Delete authority for a notebook this run created."""

    kind = "daily"

    def __init__(self, run_id):
        self.run_id = run_id
        self._created = set()

    def claim(self, notebook_id):
        """Record that this process created ``notebook_id``."""
        if not NOTEBOOK_ID_RE.match(notebook_id or ""):
            raise AllowlistError(f"not a notebook id: {notebook_id!r}")
        self._created.add(notebook_id)

    def permits(self, notebook_id) -> bool:
        return notebook_id in self._created

    def frozen(self, notebook_id):
        return None  # no frozen snapshot exists for a notebook created just now


def load_allowlist(allowlist_path, excluded_path, digest_path, *, live_ids=None):
    """Load and validate the frozen allowlist.

    Raises :class:`AllowlistError` unless every invariant holds. The point is
    that a regeneration bug which swaps ten personal notebooks for ten pipeline
    notebooks keeps the count at 465 — so the count is never the check.
    """
    allowlist_path, excluded_path = Path(allowlist_path), Path(excluded_path)
    digest_path = Path(digest_path)

    for p in (allowlist_path, excluded_path, digest_path):
        if not p.exists():
            raise AllowlistError(f"missing: {p}")

    doc = json.loads(allowlist_path.read_text())
    excluded_doc = json.loads(excluded_path.read_text())
    entries_raw = doc.get("notebooks", [])
    excluded_raw = excluded_doc.get("notebooks", [])

    entries, excluded_ids = {}, set()
    for row in entries_raw:
        nid = row.get("notebook_id", "")
        if not NOTEBOOK_ID_RE.match(nid):
            raise AllowlistError(f"allowlist holds a non-UUID id: {nid!r}")
        if nid in entries:
            raise AllowlistError(f"allowlist holds a duplicate id: {nid}")
        entries[nid] = FrozenNotebook(
            notebook_id=nid,
            title=row.get("title", ""),
            source_count=row.get("source_count") or 0,
            updated_at=row.get("updated_at", ""),
            origin=row.get("origin", ""),
        )
    for row in excluded_raw:
        nid = row.get("notebook_id", "")
        if nid:
            excluded_ids.add(nid)

    # The digest is committed outside data/, which is gitignored. It is what
    # makes "frozen" mean something.
    expected = digest_path.read_text().split()[0].strip()
    actual = canonical_digest(entries)
    if expected != actual:
        raise AllowlistError(
            f"allowlist digest mismatch: file={actual} committed={expected}"
        )

    overlap = set(entries) & excluded_ids
    if overlap:
        raise AllowlistError(f"{len(overlap)} id(s) both allowed and excluded")

    if live_ids is not None:
        live = set(live_ids)
        known = set(entries) | excluded_ids
        unclassified = live - known
        if unclassified:
            raise AllowlistError(
                f"{len(unclassified)} live notebook(s) appeared since the freeze "
                f"and are classified by neither file; re-freeze before deleting"
            )

    return ReclaimAuthority(entries, excluded_ids, actual)


def cap_for_run(requested_limit=None) -> int:
    """Deletes permitted this run. ``--limit`` may only lower the hard cap."""
    if requested_limit is None:
        return MAX_DELETES_PER_RUN
    if requested_limit < 0:
        raise AllowlistError("--limit must be non-negative")
    return min(requested_limit, MAX_DELETES_PER_RUN)
