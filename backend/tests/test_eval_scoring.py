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


def test_mrr_and_recall_at_k():
    assert abs(ev._mrr([1, 3, None]) - (1 + 1 / 3) / 3) < 1e-9  # miss scores 0
    assert ev._gold_ranks(["a x", "b", "c a"], ["a", "c", "zz"]) == [1, 3, None]
    # one query with 2 gold passages (ranks 2 and miss) + one single-gold query at rank 1
    r = ev._recallk([[2, None], [1]])
    assert r[1] == 0.5 and r[3] == 0.75  # (0 + 1)/2, (0.5 + 1)/2


def test_comparison_report_counts_rank_moves():
    def mk(q, rank):
        return {"label": {"query": q, "tag": "t"}, "rank": rank, "gold_ranks": [rank],
                "embed_ms": 1.0, "search_ms": 1.0, "rerank_ms": 0.0, "total_ms": 2.0}
    det = {"base": [mk("up", None), mk("down", 1), mk("same", 2)],
           "ctx": [mk("up", 4), mk("down", 3), mk("same", 2)]}
    summ = {n: ev._metrics(det[n]) for n in det}
    md = ev._comparison_report("doc", ["base", "ctx"], summ, {"base": 1.0, "ctx": 2.0}, det)
    assert "ctx better: 1, worse: 1, unchanged: 1" in md
    assert "| up | miss | 4 |" in md and "| same |" not in md
