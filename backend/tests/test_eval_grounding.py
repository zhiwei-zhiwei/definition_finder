"""Offline checks for the grounding eval: citation parsing and metric maths (no LLM)."""
import importlib.util
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("eval_grounding", _SCRIPTS / "eval_grounding.py")
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)


def test_cited_and_fake_pages():
    answer = "Revenue grew (page 47). Margin fell (pages 50–51 and 60). See p. 9 and pp. 3-4."
    assert g._cited_pages(answer) == [3, 4, 9, 47, 50, 51, 60]
    ex = [{"page_start": 45, "page_end": 51}, {"page_start": None, "page_end": None}]
    assert g._fake_pages([3, 47, 51, 60], ex) == [3, 60]
    assert g._gold_hit("Net revenue for 2022 was $23.6 billion", "net revenue in 2022 was $23.6 billion")
    assert not g._gold_hit("approximately 25,000 employees", "about 15,000 people")
    assert g._gold_hit("Santa Clara", "based in santa  clara, CA")


def test_metrics_split_answerable_and_no_answer():
    ex = [{"text": "t", "page_start": 47, "page_end": 47}]
    ok = {"claim": "a", "supported": True, "cited_pages": [47], "citation_correct": True}
    bad = {"claim": "b", "supported": False, "cited_pages": [47], "citation_correct": False}
    recs = [
        g._grade({"query": "q1", "gold": "grew 44%"}, 0, ex, "Revenue grew 44% (page 47).", [ok, bad], False),
        g._grade({"query": "q2", "gold": "x marks"}, 0, [{**ex[0], "text": "X  marks it"}], "Not in the document.",
                 [{"claim": "not stated", "kind": "meta", "supported": False}], True),  # meta: not scored
        g._grade({"query": "q3"}, 0, ex, "It was $5B (page 99).", [{"claim": "c", "supported": False}], False),
    ]
    m = g._metrics(recs)
    assert m["groundedness"] == 0.25          # (1/2 + 0/1) / 2 answers with claims
    assert m["citation_precision"] == 0.5     # 1 of 2 cited claims correct
    assert round(m["citation_coverage"], 3) == round(2 / 3, 3)
    assert round(m["fake_page_rate"], 3) == round(1 / 3, 3)  # page 99 never shown
    assert m["abstention_rate"] == 0.0        # q3 had no answer but answered anyway
    assert m["false_refusal_rate"] == 0.5     # q2 refused
    assert m["gold_hit_rate"] == 0.5
    assert m["context_gold_rate"] == 0.5      # q2's gold was in its excerpt...
    assert m["refusal_despite_context_rate"] == 1.0  # ...and it refused anyway
    assert "## Unsupported claims (2)" in g._report("t", {"groundedness_mean": 0.25, "groundedness_std": 0}, recs, {})
