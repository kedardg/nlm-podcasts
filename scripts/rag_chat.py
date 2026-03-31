#!/usr/bin/env python3
"""
RAG Chat Pipeline — queries ChromaDB for relevant notebooks, authenticates
with NotebookLM, queries each notebook, and outputs formatted results.

Usage:
    python3 scripts/rag_chat.py "what papers discuss attention mechanisms?"
    python3 scripts/rag_chat.py "ml: transformer architectures"
    python3 scripts/rag_chat.py "SLAM algorithms" --category robotics

Exit codes:
    0 = success (results printed)
    1 = no results found (fallback message printed)
    2 = error (error message printed)

Output: Plain text ready to relay to the user. The LLM does NOT need to
parse JSON or make decisions — just send this output as the reply.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python3"
QUERY_SCRIPT = PROJECT_ROOT / "rag" / "query.py"
AUTH_SCRIPT = PROJECT_ROOT / "scripts" / "nlm_ensure_auth.sh"
NLM_BIN = Path.home() / ".local" / "bin" / "nlm"
MAX_NOTEBOOKS = 3


def parse_category_question(text: str) -> tuple[str | None, str]:
    """Parse 'category: question' format. Returns (category, question)."""
    if ":" in text:
        parts = text.split(":", 1)
        candidate = parts[0].strip().lower()
        # Only treat as category if it's a short slug-like word
        if len(candidate) <= 20 and " " not in candidate:
            return candidate, parts[1].strip()
    return None, text


def chromadb_search(question: str, category: str | None = None) -> dict:
    """Run ChromaDB notebook routing query."""
    cmd = [
        str(VENV_PYTHON), str(QUERY_SCRIPT),
        question, "--mode", "notebook",
    ]
    if category:
        cmd.extend(["--category", category])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return {"notebooks": [], "fallback_used": True, "error": result.stderr.strip()}
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return {"notebooks": [], "fallback_used": True, "error": str(e)}


def ensure_nlm_auth() -> bool:
    """Check/refresh NLM authentication. Returns True if auth is valid."""
    try:
        result = subprocess.run(
            ["bash", str(AUTH_SCRIPT)],
            capture_output=True, text=True, timeout=120,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def query_notebook(notebook_id: str, question: str) -> str | None:
    """Query a single NotebookLM notebook. Returns the answer text or None."""
    try:
        result = subprocess.run(
            [str(NLM_BIN), "notebook", "query", notebook_id, question],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            # nlm CLI returns JSON: {"value": {"answer": "..."}}
            try:
                data = json.loads(result.stdout.strip())
                answer = data.get("value", {}).get("answer", "")
                return answer if answer else result.stdout.strip()
            except (json.JSONDecodeError, AttributeError):
                return result.stdout.strip()
        return None
    except subprocess.TimeoutExpired:
        return None


def format_output(question: str, notebook_answers: list[dict], category: str | None) -> str:
    """Format notebook answers into a user-facing reply."""
    if not notebook_answers:
        return (
            "I don't have any indexed notebooks on this topic yet.\n"
            "Run /process or wait for the next cron to create notebooks, "
            "then try again."
        )

    lines = ["Based on your research notebooks:\n"]

    for entry in notebook_answers:
        alias = entry.get("alias", entry.get("notebook_id", "unknown"))
        answer = entry.get("answer", "")
        if answer:
            lines.append(f"**From {alias}:**")
            lines.append(answer)
            lines.append("")

    # Add source info
    sources = []
    for entry in notebook_answers:
        for url in entry.get("sources_matched", []):
            if url not in sources:
                sources.append(url)

    if sources:
        lines.append("Sources:")
        for url in sources[:10]:
            lines.append(f"- {url}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="RAG Chat Pipeline")
    parser.add_argument("question", help="The question (may include 'category: question' format)")
    parser.add_argument("--category", help="Override category filter")
    args = parser.parse_args()

    # Step 1: Parse question for category prefix
    category, question = parse_category_question(args.question)
    if args.category:
        category = args.category

    # Step 2: ChromaDB search
    search_result = chromadb_search(question, category)
    notebooks = search_result.get("notebooks", [])
    fallback = search_result.get("fallback_used", False)

    if not notebooks:
        print(
            "I don't have any indexed notebooks on this topic yet.\n"
            "Run /process or wait for the next cron to create notebooks, "
            "then try again."
        )
        sys.exit(1)

    # Step 3: NLM auth
    if not ensure_nlm_auth():
        # Auth failed — output what we know from ChromaDB at least
        print(
            f"Found {len(notebooks)} relevant notebook(s) but NotebookLM auth expired.\n"
            "Run: ~/.local/bin/nlm login\n"
            "Then ask your question again."
        )
        sys.exit(1)

    # Step 4: Query top notebooks
    notebook_answers = []
    for nb in notebooks[:MAX_NOTEBOOKS]:
        nb_id = nb.get("notebook_id", "")
        alias = nb.get("notebook_alias", nb_id)
        if not nb_id:
            continue

        answer = query_notebook(nb_id, question)
        if answer:
            notebook_answers.append({
                "notebook_id": nb_id,
                "alias": alias,
                "answer": answer,
                "sources_matched": nb.get("sources_matched", []),
            })

    # Step 5: Format and output
    output = format_output(question, notebook_answers, category)
    print(output)

    if not notebook_answers:
        sys.exit(1)


if __name__ == "__main__":
    main()
