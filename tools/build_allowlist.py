#!/usr/bin/env python3
"""Generate the frozen delete allowlist. Run once; expect to delete this file.

Classification happens here and nowhere else. It deliberately does not live in
``src/lib/``: a title-matching helper on the import path is an invitation for a
future caller to use it at delete time, which is exactly the risk the frozen
allowlist exists to remove.

Two independent keys must agree before a notebook is deletable:

1. the title matches a pattern this repo's own code emits, and
2. for the weekly form, the group name decomposes into category names from
   ``data/categories.json`` (plus the literal ``Unknown`` the pipeline emits for
   the uncategorised group).

The three corrupted daily titles are matched by explicit id, not by prefix — a
``startswith("Daily Feed")`` rule would also swallow "Daily Feed Ideas".

Usage:
    .venv/bin/python3 tools/build_allowlist.py [--from-file nb_list.json]
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lib.archive_paths import NOTEBOOK_ID_RE          # noqa: E402
from lib.file_utils import now_iso, save_json         # noqa: E402
from lib.notebook_allowlist import canonical_digest   # noqa: E402

DATA = PROJECT_ROOT / "data"
CONFIG = PROJECT_ROOT / "config"
DOCS = PROJECT_ROOT / "docs"

_DAILY = re.compile(r"^Daily Feed\s*[—–-]\s*(20\d\d-\d\d-\d\d)$")
_WEEKLY = re.compile(r"^(?P<group>.+?)\s-\sWeek of\s(?P<date>20\d\d-\d\d-\d\d)$")
_PART = re.compile(r"\s*\(Part \d+/\d+\)\s*$")

# Corrupted daily titles, from a shell-escaping bug that ate the '2' of '2026'.
# Pinned by id: 'Daily Feed \t6-02-20' and 'Daily Feed \x02026-02-20'.
CORRUPT_IDS = {
    "96d2010f-dee2-433f-92ee-4c6a983be425",
    "75f6b2a2-7c2d-4967-b33a-0e6004f2dd3b",
    "0fe713a1-a767-41c4-8b7c-1ce482e4db5b",
}

# Predates this repo's first commit (2026-03-14) by 1.5-2.5 years and is emitted
# by no code here. Whatever made these, it was not this pipeline.
_RESEARCH_DIGEST = re.compile(r"^Research Digest\s-\s(20\d\d-\d\d-\d\d)$")


def category_vocabulary():
    raw = json.loads((DATA / "categories.json").read_text())
    cats = raw if isinstance(raw, list) else raw.get("categories", [])
    return {c["name"] for c in cats} | {"Unknown"}


def classify(notebook, vocab):
    """Return (origin, reason). origin None means: do not touch."""
    nid = notebook.get("id", "")
    title = notebook.get("title") or ""

    if nid in CORRUPT_IDS:
        return "corrupt", None
    if _DAILY.match(title):
        return "daily", None
    m = _WEEKLY.match(title)
    if m:
        base = _PART.sub("", m.group("group")).strip()
        if all(part.strip() in vocab for part in base.split(" + ")):
            return "weekly", None
        return None, f"weekly-shaped title with unknown group {base!r}"
    if _RESEARCH_DIGEST.match(title):
        return None, "pre-repo Research Digest (predates first commit 2026-03-14)"
    if not title.strip():
        return None, "empty title — needs human review"
    return None, "personal or unrecognised"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from-file", help="a saved `nlm notebook list --json` payload")
    ap.add_argument("--nlm", default=str(Path.home() / ".local/bin/nlm"))
    args = ap.parse_args()

    if args.from_file:
        notebooks = json.loads(Path(args.from_file).read_text())
    else:
        out = subprocess.run(
            [args.nlm, "notebook", "list", "--json"],
            capture_output=True, text=True, timeout=300,
        )
        if out.returncode != 0:
            sys.exit(f"nlm notebook list failed: {out.stderr[:300]}")
        notebooks = json.loads(out.stdout)

    vocab = category_vocabulary()
    allow, excluded = [], []
    for nb in notebooks:
        nid = nb.get("id", "")
        if not NOTEBOOK_ID_RE.match(nid):
            excluded.append({"notebook_id": nid, "title": nb.get("title"),
                             "reason": "id is not a UUID"})
            continue
        origin, reason = classify(nb, vocab)
        row = {
            "notebook_id": nid,
            "title": nb.get("title") or "",
            "source_count": nb.get("source_count") or 0,
            "updated_at": nb.get("updated_at") or "",
        }
        if origin:
            allow.append({**row, "origin": origin})
        else:
            excluded.append({**row, "reason": reason})

    digest = canonical_digest([r["notebook_id"] for r in allow])
    frozen_at = now_iso()
    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=PROJECT_ROOT,
    ).stdout.strip() or "unknown"

    save_json(DATA / "backfill-allowlist.json", {
        "frozen_at": frozen_at,
        "generator_git_sha": git_sha,
        "digest": digest,
        "count": len(allow),
        "notebooks": allow,
    })
    save_json(DATA / "backfill-excluded.json", {
        "frozen_at": frozen_at,
        "count": len(excluded),
        "notebooks": excluded,
    })
    CONFIG.mkdir(exist_ok=True)
    (CONFIG / "backfill-allowlist.sha256").write_text(digest + "\n")
    # data/ is gitignored, so the protected set is also committed under docs/
    # where it survives losing data/ entirely.
    DOCS.mkdir(exist_ok=True)
    save_json(DOCS / "backfill-excluded-ids.json", {
        "note": "Notebooks that must never be deleted. Committed copy of "
                "data/backfill-excluded.json, which lives in gitignored data/.",
        "frozen_at": frozen_at,
        "notebooks": excluded,
    })

    by_origin = {}
    for r in allow:
        by_origin[r["origin"]] = by_origin.get(r["origin"], 0) + 1
    print(f"total live     : {len(notebooks)}")
    print(f"allowlist      : {len(allow)}  {by_origin}")
    print(f"excluded       : {len(excluded)}")
    for r in excluded:
        print(f"    PROTECTED  {r['title'][:58]!r:62} {r['reason']}")
    print(f"digest         : {digest}")
    print("\nReview the protected list above before running reclaim with --execute.")


if __name__ == "__main__":
    main()
