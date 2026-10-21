"""Offline checks for the grounding eval: citation parsing, metric maths and its answer
path (the app's query graph, with stubbed retrieval and LLM)."""
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.services import prepare, query_graph, retrieve, summarize, vectorstore
from app.services.retrieve import Hit

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("eval_grounding", _SCRIPTS / "eval_grounding.py")
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)


def test_cited_and_fake_pages():
    answer = "Revenue grew (page 47). Margin fell (pages 50–51 and 60). See p. 9 and pp. 3-4."
    assert g._cited_pages(answer) == [3, 4, 9, 47, 50, 51, 60]
    ex = [{"page_start": 45, "page_end": 51}, {"page_start": None, "page_end": None}]
    assert g._fake_pages([3, 47, 51, 60], ex) == [3, 60]
    assert g._gold_hit({"gold": "Santa Clara"}, "based in santa  clara, CA")


def test_number_exact_match():
    hit = g._gold_hit
    assert hit({"gold": "Net revenue for 2022 was $23.6 billion"}, "In 2022 it was $23,601 million")
    assert not hit({"gold": "approximately 25,000 employees"}, "about 15,000 people")
    assert hit({"gold": "approximately 25,000 employees"}, "about 25 thousand people")
    assert not hit({"gold": "16% of revenue"}, "revenue rose in 2016")      # no substring hits
    assert not hit({"gold": "$23.6 billion"}, "about $24 billion")         # rounding only to gold precision
    assert not hit({"gold": "16%"}, "16 customers")                         # percent must stay percent
    assert hit({"gold": "incorporated on May 1, 1969", "key_numbers": ["1969"]}, "incorporated in 1969")
    assert g._numbers("MI300 ships in Q1 (page 47)") == []                  # model names, quarters, citations
    assert g._numbers("listed on NASDAQ (pages 1, 5, and 41).\n1. Compute\n2) Data center") == []  # lists too
    assert g._cited_pages("(pages 1, 5, and 41)") == [1, 5, 41]
    ex = [{"text": "Net revenue (In millions) 23,601, up 44%"}]
    assert g._unsourced_numbers("Revenue grew 47% to $23.6 billion, up 44% (page 9).", ex) == ["47%"]


def test_metrics_split_answerable_and_no_answer():
    ex = [{"text": "t", "page_start": 47, "page_end": 47}]
    ok = {"claim": "a", "supported": True, "cited_pages": ["page 47"], "support_pages": [47]}  # judge's string form
    bad = {"claim": "b", "supported": False, "cited_pages": [47], "support_pages": [48], "citation_correct": True}
    recs = [
        g._grade({"query": "q1", "gold": "grew 44%"}, 0, ex, "Revenue grew 44% (page 47).", [ok, bad], False),
        g._grade({"query": "q2", "gold": "x marks"}, 0, [{**ex[0], "text": "X  marks it"}], "Not in the document.",
                 [{"claim": "not stated", "kind": "meta", "supported": False}], True),  # meta: not scored
        g._grade({"query": "q3"}, 0, ex, "It was $5B (page 99).", [{"claim": "c", "supported": False}], False),
    ]
    m = g._metrics(recs)
    assert m["groundedness"] == 0.25          # (1/2 + 0/1) / 2 answers with claims
    assert m["citation_precision"] == 0.5     # 1 of 2 cited claims correct; code overrides the judge's True
    assert round(m["citation_coverage"], 3) == round(2 / 3, 3)
    assert round(m["fake_page_rate"], 3) == round(1 / 3, 3)  # page 99 never shown
    assert m["abstention_rate"] == 0.0        # q3 had no answer but answered anyway
    assert m["false_refusal_rate"] == 0.5     # q2 refused
    assert m["gold_hit_rate"] == 0.5
    assert m["number_exact_rate"] == 1.0      # q1 is the only numeric answerable query
    assert round(m["unsourced_number_rate"], 3) == round(2 / 3, 3)  # 44% and $5B absent from "t"; q2 abstained
    assert m["context_gold_rate"] == 0.5      # q2's gold was in its excerpt...
    assert m["refusal_despite_context_rate"] == 1.0  # ...and it refused anyway
    assert "## Unsupported claims (2)" in g._report("t", {"groundedness_mean": 0.25, "groundedness_std": 0}, recs, {})


def test_failure_taxonomy_primary_order():
    ex = [{"text": "Revenue was $23.6 billion, up 44%.", "page_start": 5, "page_end": 5}]
    lab = {"query": "q", "gold": "Revenue was $23.6 billion", "key_numbers": ["$23.6 billion"]}
    ok = {"claim": "c", "supported": True, "cited_pages": [5], "support_pages": [5]}
    good = "Revenue was $23.6 billion (page 5)."
    cases = [  # (expected primary, label, excerpts, answer, claims, abstained)
        ("unsourced_number", lab, ex, "Revenue grew 47% (page 5).", [ok], False),
        ("unsupported_claim", lab, ex, good, [ok, {"claim": "d", "supported": False}], False),
        ("answered_no_answer", {"query": "q"}, ex, good, [ok], False),
        ("retrieval_miss", lab, [{**ex[0], "text": "other"}], "Not in the document.", [], True),
        ("refused_with_context", lab, ex, "Not in the document.", [], True),
        ("wrong_number", lab, ex, "Revenue rose 44% (page 5).", [ok], False),
        ("fake_page", lab, ex, "Revenue was $23.6 billion (page 9).", [ok], False),
        ("wrong_page", lab, ex, good, [{**ok, "support_pages": [6]}], False),
        (None, lab, ex, good, [ok], False),
    ]
    recs = [g._grade(lab_, 0, ex_, ans, [dict(c) for c in cl], ab) for _, lab_, ex_, ans, cl, ab in cases]
    assert [(r["failures"] or [None])[0] for r in recs] == [c[0] for c in cases]
    m = g._metrics(recs)
    assert round(m["failure_rate"], 3) == round(8 / 9, 3) and round(m["fail_wrong_page_rate"], 3) == round(1 / 9, 3)


def test_verify_and_gate_metrics():
    ex = [{"text": "Revenue was $23.6 billion, up 44%.", "page_start": 5, "page_end": 5}]
    lab = {"query": "q", "gold": "Revenue was $23.6 billion"}
    ok = {"claim": "c", "supported": True, "cited_pages": [5], "support_pages": [5]}
    gate = {"status": "insufficient_evidence", "issues": [{"kind": "low_evidence", "detail": ""}], "needs_review": True}
    recs = [
        g._grade(lab, 0, ex, "Revenue grew 47% (page 5).", [ok], False),             # failure, flagged
        g._grade(lab, 0, ex, "Revenue was $23.6 billion (page 5).",
                 [{**ok, "support_pages": [6]}], False),                              # wrong_page: verify can't see
        g._grade(lab, 0, ex, "Revenue was $23.6 billion (page 5).", [ok], False),    # fine, not flagged
        g._grade({"query": "q2"}, 0, ex, "The excerpts do not mention it.", [], True),  # flagged refusal, no failure
        g._grade(lab, 0, [], "No relevant context was found in this document.", [], True, gate),  # gated miss
    ]
    assert [r["flagged"] for r in recs] == [True, False, False, True, True]
    m = g._metrics(recs)
    assert round(m["verify_recall"], 3) == round(2 / 3, 3)     # caught 2 of the 3 failures
    assert round(m["verify_precision"], 3) == round(2 / 3, 3)  # the refusal was a correct abstention
    assert m["gate_rate"] == 0.2 and m["gate_false_refusal_rate"] == 0.25
    assert "| wrong_page | 1 | 1 | 0 |" in g._report("t", {}, recs, {})


_CASES = json.loads((Path(__file__).parent / "fixtures" / "grounding_regressions.json").read_text())


@pytest.mark.parametrize("case", _CASES, ids=[f"{c['run'][:8]}-{c['rep']}-{c['label']['query'][:40]}" for c in _CASES])
def test_frozen_failures_keep_their_verdict(case):
    """Real answers from past runs (scripts/freeze_regressions.py): the scorer must still give
    each the failures a human checked. A flip means a scorer change, intended or not."""
    rec = g._grade(case["label"], case["rep"], case["excerpts"], case["answer"], case["claims"], case["abstained"])
    assert rec["failures"] == case["failures"], case["note"]


@pytest.fixture
def eval_graph(monkeypatch):
    """The graph as the eval script sees it: in-memory checkpoints. The test app's lifespan
    swapped in a SQLite one bound to the TestClient's event loop; asyncio.run here would
    drive that connection from another loop and break it for later tests."""
    monkeypatch.setattr(query_graph, "graph", query_graph._BUILDER.compile(
        checkpointer=InMemorySaver(serde=query_graph._SERDE)))


def test_answers_come_from_the_query_graph(monkeypatch, eval_graph):
    monkeypatch.setattr(prepare, "has_chunks", lambda _id: True)
    monkeypatch.setattr(retrieve, "embed_query", lambda _q: [0.0])
    monkeypatch.setattr(vectorstore, "search", lambda *a: [("c1", 0.6)])
    monkeypatch.setattr(retrieve, "hybrid_search", lambda *a: [Hit("c1", "p1", "Rent is $5.", 1.0, 3, None, [], None)])
    monkeypatch.setattr(retrieve, "enrich_hits_from_db", lambda db, hits: hits)
    monkeypatch.setattr(retrieve, "fetch_parent_texts", lambda db, ids: [
        SimpleNamespace(text="Rent is $5.", marked_text="[page 3] Rent is $5.", page_start=3, page_end=None)])
    monkeypatch.setattr(summarize, "summarize_stream", lambda *a: iter(["Rent is $5 (page 3)."]))
    state = asyncio.run(g._run_graph("doc", "What is the rent?", 5))
    assert state["answer"] == "Rent is $5 (page 3)." and state["verification"]["status"] == "ok"
    assert g._excerpts(state)[0]["page_end"] == 3  # single-page parent: NULL end filled in

    def no_llm(*a):
        raise AssertionError("summarizer called")

    monkeypatch.setattr(summarize, "summarize_stream", no_llm)
    # Re-judging stored answers stops before the summarizer...
    state = asyncio.run(g._run_graph("doc", "What is the rent?", 5, answer=False))
    assert "answer" not in state and state["excerpts"]
    # ...and the evidence gate answers off-topic queries without it, as in the app.
    monkeypatch.setattr(vectorstore, "search", lambda *a: [("c1", 0.01)])
    assert asyncio.run(g._run_graph("doc", "AMD revenue?", 5))["answer"] == query_graph.NO_CONTEXT


def test_auto_retry_resumes_paused_runs(monkeypatch, eval_graph):
    top_ks = []

    def search(db, doc_id, qvec, query, top_k):
        top_ks.append(top_k)
        return [Hit("c1", "p1", "Rent is $5.", 1.0, 3, None, [], None)]

    answers = iter(["Rent is $7 (page 3).", "Rent is $5 (page 3)."])  # flagged ($7 is in no excerpt), then fine
    monkeypatch.setattr(prepare, "has_chunks", lambda _id: True)
    monkeypatch.setattr(retrieve, "embed_query", lambda _q: [0.0])
    monkeypatch.setattr(vectorstore, "search", lambda *a: [("c1", 0.6)])
    monkeypatch.setattr(retrieve, "hybrid_search", search)
    monkeypatch.setattr(retrieve, "enrich_hits_from_db", lambda db, hits: hits)
    monkeypatch.setattr(retrieve, "fetch_parent_texts", lambda db, ids: [
        SimpleNamespace(text="Rent is $5.", marked_text="[page 3] Rent is $5.", page_start=3, page_end=None)])
    monkeypatch.setattr(summarize, "summarize_stream", lambda *a: iter([next(answers)]))
    state = asyncio.run(g._run_graph("doc", "What is the rent?", 5, retry=True))
    assert state["verification"]["status"] == "warning"
    assert state["retried"]["answer"] == "Rent is $5 (page 3)." and state["retried"]["verification"]["status"] == "ok"
    assert top_ks == [5, 10]

    ex = [{"text": "Rent is $5.", "page_start": 3, "page_end": 3}]
    lab = {"query": "q", "gold": "Rent is $5"}
    ok = {"claim": "c", "supported": True, "cited_pages": [3], "support_pages": [3]}
    fixed = g._grade(lab, 0, ex, "Rent is $7 (page 3).", [ok], False)
    fixed["retry"] = g._grade(lab, 0, ex, "Rent is $5 (page 3).", [ok], False)
    broke = g._grade({"query": "q2"}, 0, ex, "The excerpts do not mention it.", [], True)  # correct refusal...
    broke["retry"] = g._grade({"query": "q2"}, 0, ex, "It is $5 (page 3).", [ok], False)  # ...answered on retry
    m = g._metrics([fixed, broke, g._grade(lab, 0, ex, "Rent is $5 (page 3).", [ok], False)])
    assert round(m["retry_rate"], 3) == round(2 / 3, 3)
    assert m["retry_fixed_rate"] == 1.0 and m["retry_broke_rate"] == 1.0
