"""POST /query streams every stage from the query graph, in the order the frontend expects;
flagged answers pause for review until POST /query/resume."""
import json
import sqlite3
import uuid
from types import SimpleNamespace

import pytest

from app.config import settings
from app.models.db import SessionLocal
from app.models.schema import Document
from app.services import prepare, retrieve, summarize, vectorstore
from app.services.retrieve import Hit
from app.utils.sse import sse_event

QUERY_EVENTS = ["stage:embedding_query", "stage:searching", "stage:consolidating", "stage:fetching_context",
                "snippets", "stage:summarizing", "token", "token", "stage:verifying", "verification", "review", "done"]
GROUNDED = ["The term is", " set out on page 3."]  # passes verify: no review pause


def _events(body: str) -> list[str]:
    """SSE body -> ["stage:<name>" | "<event>", ...], skipping pings."""
    out = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n") if line.startswith(("event: ", "data: ")))
        if "event" in fields:
            ev = fields["event"]
            out.append(f"stage:{json.loads(fields['data'])['stage']}" if ev == "stage" else ev)
    return out


def _data(body: str, event: str) -> dict:
    """Payload of the last `event` in an SSE body."""
    blocks = [b for b in body.replace("\r\n", "\n").split("\n\n") if f"event: {event}\n" in b]
    return json.loads(blocks[-1].split("data: ", 1)[1])


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
    # Unprepared docs run the prepare node first; prepared ones skip it. The stub answer's
    # "5 years" is in no excerpt, so the run ends paused for review.
    assert _events(r.text) == ([] if prepared else ["stage:extracting"]) + QUERY_EVENTS


def _checkpoints() -> int:
    with sqlite3.connect(settings.CHECKPOINT_DB) as c:
        return c.execute("SELECT count(*) FROM checkpoints").fetchone()[0]


def test_finished_runs_leave_no_checkpoints(client, doc_id, stubs):
    stubs.setattr(prepare, "has_chunks", lambda _id: True)
    stubs.setattr(summarize, "summarize_stream", lambda *a: iter(GROUNDED))
    before = _checkpoints()
    r = client.post("/query", json={"doc_id": doc_id, "query": "how long is the term?"})
    assert "review" not in _events(r.text)
    assert _checkpoints() == before


def test_flagged_answer_pauses_until_accepted(client, doc_id, stubs):
    stubs.setattr(prepare, "has_chunks", lambda _id: True)
    before = _checkpoints()
    r = client.post("/query", json={"doc_id": doc_id, "query": "how long is the term?"})
    pause = _data(r.text, "review")
    assert pause["can_retry"] and pause["top_k"] == 5
    assert _checkpoints() == before + 1  # the paused run survives in SQLite

    r = client.post("/query/resume", json={"query_id": pause["query_id"], "action": "accept"})
    assert _events(r.text) == ["done"]
    assert _checkpoints() == before
    # A finished run can't be resumed again (nor can an id that never existed).
    r = client.post("/query/resume", json={"query_id": pause["query_id"], "action": "accept"})
    assert _events(r.text) == ["error"] and "nothing to review" in r.text


def test_retry_reruns_with_more_passages_and_updates_the_cache(client, stubs, make_user, bearer):
    stubs.setattr(prepare, "has_chunks", lambda _id: True)
    _, jwt = make_user("retry@example.com", "retryuser")
    uid = client.get("/auth/me", headers=bearer(jwt)).json()["user_id"]
    did = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(Document(id=did, filename="x.pdf", mime="application/pdf", size_bytes=1, status="ready", user_id=uid))
        db.commit()
    top_ks = []

    def search(db, doc_id, qvec, query, top_k):
        top_ks.append(top_k)
        return [Hit("c1", "p1", "the term", 1.0, 3, 3, [], None)]

    stubs.setattr(retrieve, "hybrid_search", search)
    r = client.post("/query", json={"doc_id": did, "query": "how long is the term?"}, headers=bearer(jwt))
    qid = _data(r.text, "review")["query_id"]
    # Another user (or an anonymous caller) can't resume it.
    _, other = make_user("other@example.com", "otheruser")
    for headers in (bearer(other), {}):
        r = client.post("/query/resume", json={"query_id": qid, "action": "retry"}, headers=headers)
        assert "nothing to review" in r.text

    stubs.setattr(summarize, "summarize_stream", lambda *a: iter(GROUNDED))
    r = client.post("/query/resume", json={"query_id": qid, "action": "retry"}, headers=bearer(jwt))
    assert _events(r.text) == QUERY_EVENTS[:6] + ["token", "token", "stage:verifying", "verification", "done"]
    assert top_ks == [5, 10]
    # The retry overwrote the paused run's cached row instead of adding one.
    [row] = client.get(f"/queries?doc_id={did}", headers=bearer(jwt)).json()
    assert row["id"] == qid
    assert client.get(f"/queries/{qid}", headers=bearer(jwt)).json()["summary_text"] == "".join(GROUNDED)


def test_no_retry_at_max_top_k(client, doc_id, stubs):
    stubs.setattr(prepare, "has_chunks", lambda _id: True)
    r = client.post("/query", json={"doc_id": doc_id, "query": "how long is the term?", "top_k": 50})
    pause = _data(r.text, "review")
    assert not pause["can_retry"]
    r = client.post("/query/resume", json={"query_id": pause["query_id"], "action": "retry"})
    assert _events(r.text) == ["done"]  # treated as accept


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
    # Gated answers aren't held for review: more passages can't raise the doc's best cosine.
    assert _events(r.text) == QUERY_EVENTS[:6] + ["token", "verification", "done"]
    assert "insufficient_evidence" in r.text and "No relevant context" in r.text


def test_signed_in_query_caches_its_verification(client, stubs, make_user, bearer):
    stubs.setattr(prepare, "has_chunks", lambda _id: True)
    _, jwt = make_user("graph@example.com", "graphuser")
    did = uuid.uuid4().hex
    with SessionLocal() as db:
        uid = client.get("/auth/me", headers=bearer(jwt)).json()["user_id"]
        db.add(Document(id=did, filename="x.pdf", mime="application/pdf", size_bytes=1, status="ready", user_id=uid))
        db.commit()
    client.post("/query", json={"doc_id": did, "query": "how long is the term?"}, headers=bearer(jwt))
    [row] = client.get(f"/queries?doc_id={did}", headers=bearer(jwt)).json()
    cached = client.get(f"/queries/{row['id']}", headers=bearer(jwt)).json()
    # The stub answer says "5 years"; its only excerpt ("the term") doesn't.
    assert cached["verification"]["status"] == "warning"
    assert [i["kind"] for i in cached["verification"]["issues"]] == ["unsourced_number"]
