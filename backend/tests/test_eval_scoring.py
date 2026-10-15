"""Offline checks for the eval harness: scoring maths and label-file shape."""
import importlib.util
import json
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("eval_retrieval", _SCRIPTS / "eval_retrieval.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)


def test_first_match_rank_is_one_based_and_whitespace_case_insensitive():
    snippets = ["nothing here", "The  Total\nMonthly Rent of $4070.00 applies", "later"]
    assert ev._first_match_rank(snippets, ["total monthly rent of $4070.00"]) == 2
    assert ev._first_match_rank(snippets, ["absent", "later"]) == 3  # any gold counts
    assert ev._first_match_rank(snippets, ["absent"]) is None


def test_passk_counts_hits_within_k_and_never_misses():
    p = ev._passk([1, 3, None, 8])
    assert p[1] == 0.25 and p[3] == 0.5 and p[10] == 0.75 and p[20] == 0.75


def test_label_files_are_well_formed():
    for f in (_SCRIPTS / "eval_labels").glob("*.json"):
        for d in json.loads(f.read_text()):
            assert d["query"].strip(), f
            if d.get("tag") == "no-answer":
                assert "gold" not in d, (f, d["query"])
            else:
                golds = [d["gold"]] if isinstance(d["gold"], str) else d["gold"]
                assert golds and all(g.strip() for g in golds), (f, d["query"])
