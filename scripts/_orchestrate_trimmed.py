#!/usr/bin/env python3
"""Standalone orchestrator: creates NLM notebooks from an existing
content-processor-output.json. Use when manual scope trimming is needed
and you want to skip the newsletter/triage/content-processor phases.

Reads:  data/content-processor-output.json
        data/sources.json
Writes: data/notebooks.json (appends entries with status='generating')

Mirrors the inline Python at weekly_pipeline.sh Step 5, but standalone so
we can rerun it on a hand-edited manifest without re-running triage.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NLM = os.path.expanduser("~/.local/bin/nlm")
DATE = os.environ.get("ORCH_DATE") or __import__("datetime").date.today().isoformat()

manifest_path = PROJECT_ROOT / "data" / "content-processor-output.json"
sources_path = PROJECT_ROOT / "data" / "sources.json"
notebooks_path = PROJECT_ROOT / "data" / "notebooks.json"

manifest = json.loads(manifest_path.read_text())
sources_data = json.loads(sources_path.read_text())
notebooks_registry = json.loads(notebooks_path.read_text()) if notebooks_path.exists() else {"notebooks": []}

src_map = {s["id"]: s for s in sources_data.get("sources", [])}
created = []

groups = manifest.get("groups", {})
print(f"Processing {len(groups)} groups for run weekly_{DATE}\n")

for group_name, info in groups.items():
    queued_ids = [
        sid for sid in info.get("source_ids", [])
        if src_map.get(sid, {}).get("status") in ("queued", "daily-digested", "processing")
    ]
    if not queued_ids:
        print(f"--- {group_name}: no eligible sources, skipping ---")
        continue
    info["source_ids"] = queued_ids
    info["source_count"] = len(queued_ids)

    print(f"\n--- {group_name} ({info['source_count']} sources) ---")
    slug = group_name.lower().replace(" ", "-").replace("&", "and")[:30]
    alias = f"{slug}-{DATE}"

    # Create notebook
    result = subprocess.run(
        [NLM, "notebook", "create", f"{group_name} - Week of {DATE}"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"  FAIL: notebook create: {result.stderr[:200]}")
        continue

    nb_id = None
    m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", result.stdout)
    if m:
        nb_id = m.group(0)
    if not nb_id:
        try:
            out = json.loads(result.stdout)
            nb_id = out.get("notebook_id") or out.get("id") or out.get("notebookId")
        except Exception:
            pass
    if not nb_id:
        print(f"  FAIL: could not extract notebook ID from: {result.stdout[:200]}")
        continue

    print(f"  Notebook: {nb_id}")
    subprocess.run([NLM, "alias", "set", alias, nb_id], capture_output=True, timeout=10)

    added = 0
    for sid in info.get("source_ids", []):
        src = src_map.get(sid, {})
        url = src.get("url", "")
        src_type = src.get("type", "url")
        if not url and not src.get("file_path"):
            continue

        if src_type == "youtube":
            cmd = [NLM, "source", "add", nb_id, "--youtube", url, "--wait"]
        elif src.get("file_path"):
            cmd = [NLM, "source", "add", nb_id, "--file", src["file_path"], "--wait"]
        else:
            cmd = [NLM, "source", "add", nb_id, "--url", url, "--wait"]

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            added += 1
        else:
            print(f"  WARN: source add failed for {url[:60]}: {r.stderr[:100]}")

    print(f"  Added {added}/{len(info.get('source_ids', []))} sources")

    note_path = info.get("companion_note_path", "")
    if note_path and os.path.exists(note_path):
        note_text = open(note_path).read()
        r = subprocess.run(
            [NLM, "source", "add", nb_id, "--text", note_text,
             "--title", f"{group_name} Digest Notes", "--wait"],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0:
            print("  Companion note added")

    focus_file = PROJECT_ROOT / "config" / "prompts" / "weekly-podcast-focus.txt"
    focus = focus_file.read_text().strip() if focus_file.exists() else ""
    podcast_cmd = [NLM, "audio", "create", nb_id, "--format", "deep_dive", "--length", "default", "--confirm"]
    if focus:
        podcast_cmd.extend(["--focus", focus])
    r = subprocess.run(podcast_cmd, capture_output=True, text=True, timeout=30)
    print(f"  Podcast: {'requested' if r.returncode == 0 else 'FAILED'}")

    report_script = PROJECT_ROOT / "scripts" / "nlm_create_report.sh"
    if report_script.exists():
        r = subprocess.run(["bash", str(report_script), nb_id], capture_output=True, text=True, timeout=60)
        print(f"  Report: {'requested' if r.returncode == 0 else 'FAILED'}")

    created.append({"notebook_id": nb_id, "alias": alias, "group_name": group_name, "source_count": added})
    notebooks_registry.setdefault("notebooks", []).append({
        "id": nb_id, "alias": alias,
        "category": slug, "category_name": group_name,
        "created_at": f"{DATE}T00:00:00Z",
        "source_count": added,
        "source_ids": info.get("source_ids", []),
        "has_podcast": True, "has_report": True,
        "status": "generating", "run_id": f"weekly_{DATE}",
    })

notebooks_path.write_text(json.dumps(notebooks_registry, indent=2))

print(f"\n=== Orchestrator complete: {len(created)} notebooks created ===")
for nb in created:
    print(f"  {nb['group_name']}: {nb['notebook_id']} ({nb['source_count']} sources)")
