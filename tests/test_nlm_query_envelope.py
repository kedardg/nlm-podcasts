"""The `nlm notebook query --json` envelope, and why it is parsed in one place.

nlm 0.9.x returns the answer at the top level. Earlier versions nested it under
`value`. Reading only the old path made `notebook query` look like it returned
nothing: the Deep Dive generator stubbed every paper for months while the query
itself was answering fine.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from nlm.client import extract_query_answer  # noqa: E402

# Verified against nlm 0.9.6 on 2026-10-07.
V096 = {
    "answer": "Modern autonomous systems demonstrate strong bare-metal capabilities.",
    "question": "Summarize the key findings.",
    "conversation_id": "6c2e51cf-f8e1-4dc4-8435-45b07889e6a2",
    "sources_used": [1, 2, 3],
    "citations": {"1": "..."},
    "references": ["..."],
}
LEGACY = {"value": {"answer": "An older-shaped answer."}}


def test_reads_the_0_9_x_top_level_answer():
    assert extract_query_answer(V096) == V096["answer"]


def test_still_reads_the_legacy_nested_answer():
    """Kept so a pinned/rolled-back CLI does not silently produce stubs again."""
    assert extract_query_answer(LEGACY) == "An older-shaped answer."


def test_top_level_wins_when_both_are_present():
    both = {"answer": "new", "value": {"answer": "old"}}
    assert extract_query_answer(both) == "new"


def test_absent_answer_is_empty_not_the_whole_payload():
    """rag_chat used to fall back to raw stdout, handing the caller a JSON blob
    as if it were prose."""
    assert extract_query_answer({"question": "q", "citations": {}}) == ""
    assert extract_query_answer({}) == ""
    assert extract_query_answer(None) == ""


def test_null_value_does_not_raise():
    assert extract_query_answer({"value": None}) == ""
    assert extract_query_answer({"answer": None}) == ""


def test_whitespace_only_answer_is_treated_as_empty():
    assert extract_query_answer({"answer": "   \n  "}) == ""
