"""POST /query streams every stage from the query graph, in the order the frontend expects."""
import json
import uuid
from types import SimpleNamespace

import pytest

from app.models.db import SessionLocal
from app.models.schema import Document
from app.services import prepare, retrieve, summarize, vectorstore
from app.services.retrieve import Hit
from app.utils.sse import sse_event

QUERY_EVENTS = ["stage:embedding_query", "stage:searching", "stage:consolidating", "stage:fetching_context",
                "snippets", "stage:summarizing", "token", "token", "stage:verifying", "verification", "done"]


def _events(body: str) -> list[str]:
    """SSE body -> ["stage:<name>" | "<event>", ...], skipping pings."""
    out = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n") if line.startswith(("event: ", "data: ")))
        if "event" in fields:
            ev = fields["event"]
            out.append(f"stage:{json.loads(fields['data'])['stage']}" if ev == "stage" else ev)
    return out


@pytest.fixture
def doc_id():
    did = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(Document(id=did, filename="x.pdf", mime="application/pdf", size_bytes=1, status="ready"))
        db.commit()
    return did


@pytest.fixture
def stubs(monkeypatch):
    """No Docling, no OpenAI: one hit in one parent, a two-token answer."""
    async def fake_prepare(_id):
        yield sse_event("stage", {"stage": "extracting"})

    monkeypatch.setattr(prepare, "prepare_document_stream", fake_prepare)
    monkeypatch.setattr(retrieve, "embed_query", lambda _q: [0.0])
    monkeypatch.setattr(vectorstore, "search", lambda *a: [("c1", 0.6)])  # evidence gate's best cosine
    monkeypatch.setattr(retrieve, "hybrid_search", lambda *a: [Hit("c1", "p1", "the term", 1.0, 3, 3, [], None)])
    monkeypatch.setattr(retrieve, "fetch_parent_texts", lambda db, ids: [
        SimpleNamespace(text="the term", marked_text="[page 3] the term", page_start=3, page_end=3)])
    monkeypatch.setattr(summarize, "summarize_stream", lambda *a: iter(["It is", " 5 years."]))
    return monkeypatch


@pytest.mark.parametrize("prepared", [True, False])
def test_query_streams_graph_events_in_order(client, doc_id, stubs, prepared):
    stubs.setattr(prepare, "has_chunks", lambda _id: prepared)
    r = client.post("/query", json={"doc_id": doc_id, "query": "how long is the term?"})
    # Unprepared docs run the prepare node first; prepared ones skip it.
    assert _events(r.text) == ([] if prepared else ["stage:extracting"]) + QUERY_EVENTS


def test_node_failure_ends_stream_with_error(client, doc_id, stubs):
    stubs.setattr(prepare, "has_chunks", lambda _id: True)

    def boom(*a):
        raise RuntimeError("search down")

    stubs.setattr(retrieve, "hybrid_search", boom)
    r = client.post("/query", json={"doc_id": doc_id, "query": "q"})
    assert _events(r.text) == ["stage:embedding_query", "stage:searching", "error"]
    assert "search down" in r.text


@pytest.mark.parametrize("case", ["low_similarity", "no_passages"])
def test_insufficient_evidence_skips_the_llm(client, doc_id, stubs, case):
    stubs.setattr(prepare, "has_chunks", lambda _id: True)
    if case == "low_similarity":
        stubs.setattr(vectorstore, "search", lambda *a: [("c1", 0.05)])
    else:
        stubs.setattr(retrieve, "fetch_parent_texts", lambda db, ids: [])

    def no_llm(*a):
        raise AssertionError("summarizer called")

    stubs.setattr(summarize, "summarize_stream", no_llm)
    r = client.post("/query", json={"doc_id": doc_id, "query": "what is AMD's revenue?"})
    assert _events(r.text) == QUERY_EVENTS[:6] + ["token", "verification", "done"]
    assert "insufficient_evidence" in r.text and "No relevant context" in r.text
