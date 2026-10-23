"""POST /query streams every stage from the query graph, in the order the frontend expects;
flagged answers pause for review until POST /query/resume; transient failures are retried,
and a run that still fails can be recovered from its checkpoint. Each run is traced: a
query.run span with one span per node attempt under it."""
import json
import sqlite3
import uuid
from datetime import timedelta
from types import SimpleNamespace

import httpx
import openai
import pytest
from opentelemetry.trace import StatusCode

from app.config import settings
from app.models.db import SessionLocal
from app.models.schema import Document
from app.services import prepare, query_graph, retrieve, summarize, vectorstore
from app.services.query_graph import _transient
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


@pytest.fixture
def flaky(monkeypatch):
    """flaky(module, name, exc, fails): the function raises `exc` on its first `fails` calls,
    then calls through. Returns its call log, so a test can tell whether a node re-ran."""
    def make(module, name, exc, fails=1):
        real, calls = getattr(module, name), []

        def wrapper(*a, **kw):
            calls.append(a)
            if len(calls) <= fails:
                raise exc
            return real(*a, **kw)

        monkeypatch.setattr(module, name, wrapper)
        return calls
    return make


_REQ = httpx.Request("POST", "https://api.openai.com/v1")


def _api_error(cls, code):
    return cls("x", response=httpx.Response(code, request=_REQ), body=None)


@pytest.mark.parametrize("prepared", [True, False])
def test_query_streams_graph_events_in_order(client, doc_id, stubs, prepared):
    stubs.setattr(prepare, "has_chunks", lambda _id: prepared)
    r = client.post("/query", json={"doc_id": doc_id, "query": "how long is the term?"})
    # Unprepared docs run the prepare node first; prepared ones skip it. The stub answer's
    # "5 years" is in no excerpt, so the run ends paused for review.
    assert _events(r.text) == ([] if prepared else ["stage:extracting"]) + QUERY_EVENTS


def _checkpoints() -> int:
    """Runs (threads) with checkpoints, read from the file itself: what survives a restart."""
    with sqlite3.connect(settings.CHECKPOINT_DB) as c:
        return c.execute("SELECT count(DISTINCT thread_id) FROM checkpoints").fetchone()[0]


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


def test_rejected_answer_is_kept_and_labelled(client, stubs, make_user, bearer):
    stubs.setattr(prepare, "has_chunks", lambda _id: True)
    _, jwt = make_user("reject@example.com", "rejectuser")
    uid = client.get("/auth/me", headers=bearer(jwt)).json()["user_id"]
    did = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(Document(id=did, filename="x.pdf", mime="application/pdf", size_bytes=1, status="ready", user_id=uid))
        db.commit()
    before = _checkpoints()
    r = client.post("/query", json={"doc_id": did, "query": "how long is the term?"}, headers=bearer(jwt))
    qid = _data(r.text, "review")["query_id"]
    r = client.post("/query/resume", json={"query_id": qid, "action": "reject"}, headers=bearer(jwt))
    assert _events(r.text) == ["done"]
    assert _checkpoints() == before
    cached = client.get(f"/queries/{qid}", headers=bearer(jwt)).json()
    assert cached["review_status"] == "rejected" and cached["review"] is None
    assert cached["summary_text"] == "It is 5 years."  # kept, not deleted


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
    # A later visit can resume it from the cache: same payload as the SSE event.
    assert client.get(f"/queries/{qid}", headers=bearer(jwt)).json()["review"] == _data(r.text, "review")
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
    assert row["id"] == qid and row["review_status"] is None  # the retried answer passed verify
    cached = client.get(f"/queries/{qid}", headers=bearer(jwt)).json()
    assert cached["summary_text"] == "".join(GROUNDED) and cached["review"] is None


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
    # Gated answers show no snippets and aren't held for review: more passages can't raise
    # the doc's best cosine.
    assert _events(r.text) == QUERY_EVENTS[:4] + ["stage:summarizing", "token", "verification", "done"]
    assert "insufficient_evidence" in r.text and "No relevant context" in r.text


def test_gated_query_caches_no_snippets(client, stubs, make_user, bearer):
    stubs.setattr(prepare, "has_chunks", lambda _id: True)
    stubs.setattr(vectorstore, "search", lambda *a: [("c1", 0.05)])
    _, jwt = make_user("gated@example.com", "gateduser")
    uid = client.get("/auth/me", headers=bearer(jwt)).json()["user_id"]
    did = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(Document(id=did, filename="x.pdf", mime="application/pdf", size_bytes=1, status="ready", user_id=uid))
        db.commit()
    client.post("/query", json={"doc_id": did, "query": "what is AMD's revenue?"}, headers=bearer(jwt))
    [row] = client.get(f"/queries?doc_id={did}", headers=bearer(jwt)).json()
    assert client.get(f"/queries/{row['id']}", headers=bearer(jwt)).json()["snippets"] == []


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
    assert row["review_status"] == "pending"
    client.post("/query/resume", json={"query_id": row["id"], "action": "accept"}, headers=bearer(jwt))
    [row] = client.get(f"/queries?doc_id={did}", headers=bearer(jwt)).json()
    assert row["review_status"] == "accepted"
    cached = client.get(f"/queries/{row['id']}", headers=bearer(jwt)).json()
    assert cached["review"] is None and cached["review_status"] == "accepted"


def test_only_transient_errors_are_retried():
    assert all(map(_transient, [openai.APIConnectionError(request=_REQ), openai.APITimeoutError(_REQ),
                                _api_error(openai.RateLimitError, 429), _api_error(openai.InternalServerError, 500),
                                httpx.RemoteProtocolError("peer closed connection")]))
    # A bad key or request fails the same way again; so does a bug of ours.
    assert not any(map(_transient, [_api_error(openai.AuthenticationError, 401),
                                    _api_error(openai.BadRequestError, 400), RuntimeError("bug"), KeyError("x")]))


def test_transient_failure_is_retried_inside_the_run(client, doc_id, stubs, flaky):
    stubs.setattr(prepare, "has_chunks", lambda _id: True)
    stubs.setattr(summarize, "summarize_stream", lambda *a: iter(GROUNDED))
    calls = flaky(retrieve, "hybrid_search", openai.APIConnectionError(request=_REQ))
    r = client.post("/query", json={"doc_id": doc_id, "query": "how long is the term?"})
    assert len(calls) == 2
    # The retried node streams its stages again, then the run carries on as usual.
    assert _events(r.text) == ["stage:embedding_query", "stage:searching"] + QUERY_EVENTS[:6] + [
        "token", "token", "stage:verifying", "verification", "done"]


def test_stream_dropped_mid_answer_is_retried(client, doc_id, stubs):
    stubs.setattr(prepare, "has_chunks", lambda _id: True)
    attempts = []

    def stream(*a):
        attempts.append(1)
        yield GROUNDED[0]
        if len(attempts) == 1:
            raise httpx.RemoteProtocolError("peer closed connection")
        yield GROUNDED[1]

    stubs.setattr(summarize, "summarize_stream", stream)
    r = client.post("/query", json={"doc_id": doc_id, "query": "how long is the term?"})
    # The half answer's token was already sent; the retry restarts at stage:summarizing,
    # which is where the frontend clears the answer.
    assert _events(r.text) == QUERY_EVENTS[:6] + ["token", "snippets", "stage:summarizing", "token", "token",
                                                  "stage:verifying", "verification", "done"]
    assert _data(r.text, "verification")["status"] == "ok"


def test_permanent_failure_is_not_retried(client, doc_id, stubs, flaky):
    stubs.setattr(prepare, "has_chunks", lambda _id: True)
    calls = flaky(retrieve, "hybrid_search", _api_error(openai.AuthenticationError, 401))
    r = client.post("/query", json={"doc_id": doc_id, "query": "q"})
    assert len(calls) == 1
    assert _events(r.text) == ["stage:embedding_query", "stage:searching", "error"]


def test_failed_run_recovers_from_its_checkpoint(client, doc_id, stubs, flaky):
    stubs.setattr(prepare, "has_chunks", lambda _id: True)
    stubs.setattr(summarize, "summarize_stream", lambda *a: iter(GROUNDED))
    searches = flaky(retrieve, "hybrid_search", None, fails=0)  # just counts
    answers = flaky(summarize, "summarize_stream", openai.APIConnectionError(request=_REQ), fails=2)
    before = _checkpoints()
    r = client.post("/query", json={"doc_id": doc_id, "query": "how long is the term?"})
    # Both attempts failed: the run stops at summarize and stays on disk, named in the error.
    assert _events(r.text) == QUERY_EVENTS[:6] + QUERY_EVENTS[4:6] + ["error"]
    qid = _data(r.text, "error")["query_id"]
    assert _checkpoints() == before + 1

    r = client.post("/query/resume", json={"query_id": qid, "action": "recover"})
    # It continues at the failed node; retrieve and consolidate don't run again.
    assert _events(r.text) == QUERY_EVENTS[4:6] + ["token", "token", "stage:verifying", "verification", "done"]
    assert len(searches) == 1 and len(answers) == 3
    assert _checkpoints() == before
    r = client.post("/query/resume", json={"query_id": qid, "action": "recover"})
    assert "nothing to recover" in r.text


def test_recover_and_review_only_reach_their_own_runs(client, stubs, make_user, bearer, flaky):
    stubs.setattr(prepare, "has_chunks", lambda _id: True)
    _, jwt = make_user("recover@example.com", "recoveruser")
    uid = client.get("/auth/me", headers=bearer(jwt)).json()["user_id"]
    did = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(Document(id=did, filename="x.pdf", mime="application/pdf", size_bytes=1, status="ready", user_id=uid))
        db.commit()
    flaky(retrieve, "hybrid_search", openai.APIConnectionError(request=_REQ), fails=2)
    r = client.post("/query", json={"doc_id": did, "query": "q"}, headers=bearer(jwt))
    failed = _data(r.text, "error")["query_id"]
    r = client.post("/query", json={"doc_id": did, "query": "q"}, headers=bearer(jwt))
    paused = _data(r.text, "review")["query_id"]
    _, other = make_user("recover2@example.com", "recoveruser2")
    for headers in (bearer(other), {}):
        assert "nothing to recover" in client.post(
            "/query/resume", json={"query_id": failed, "action": "recover"}, headers=headers).text
    # A failed run isn't up for review, and a paused one isn't up for recovery.
    assert "nothing to review" in client.post(
        "/query/resume", json={"query_id": failed, "action": "accept"}, headers=bearer(jwt)).text
    assert "nothing to recover" in client.post(
        "/query/resume", json={"query_id": paused, "action": "recover"}, headers=bearer(jwt)).text
    client.post("/query/resume", json={"query_id": paused, "action": "accept"}, headers=bearer(jwt))


def test_sweep_drops_stale_failed_runs_but_not_paused_ones(client, doc_id, stubs, flaky):
    stubs.setattr(prepare, "has_chunks", lambda _id: True)
    r = client.post("/query", json={"doc_id": doc_id, "query": "q"})
    paused = _data(r.text, "review")["query_id"]
    flaky(retrieve, "hybrid_search", openai.APIConnectionError(request=_REQ), fails=2)
    r = client.post("/query", json={"doc_id": doc_id, "query": "q"})
    failed = _data(r.text, "error")["query_id"]
    before = _checkpoints()
    # The sweep runs at startup; call it on the app's event loop, where its connection lives.
    client.portal.call(query_graph.sweep, timedelta(hours=1))
    assert _checkpoints() == before  # too recent to drop
    client.portal.call(query_graph.sweep, timedelta(0))
    assert "nothing to recover" in client.post("/query/resume", json={"query_id": failed, "action": "recover"}).text
    assert _events(client.post("/query/resume", json={"query_id": paused, "action": "accept"}).text) == ["done"]


def test_progress_is_on_disk_before_the_run_ends(client, doc_id, stubs):
    """What survives a kill -9 mid-run: with durability="exit" nothing is written until the
    run returns, so a killed process would leave nothing to recover."""
    stubs.setattr(prepare, "has_chunks", lambda _id: True)
    before, during = _checkpoints(), []

    def stream(*a):
        during.append(_checkpoints())
        return iter(GROUNDED)

    stubs.setattr(summarize, "summarize_stream", stream)
    client.post("/query", json={"doc_id": doc_id, "query": "how long is the term?"})
    assert during == [before + 1]


def _run_tree(spans):
    """The request's query.run span and its node spans in start order, all children of it."""
    finished = sorted(spans.get_finished_spans(), key=lambda s: s.start_time)
    (run,) = [s for s in finished if s.name == "query.run"]
    nodes = [s for s in finished if s.name.startswith("node.")]
    assert all(n.parent.span_id == run.context.span_id for n in nodes)
    return run, nodes


def test_each_node_is_a_span_under_the_run(client, doc_id, stubs, spans):
    stubs.setattr(prepare, "has_chunks", lambda _id: True)
    stubs.setattr(summarize, "summarize_stream", lambda *a: iter(GROUNDED))
    client.post("/query", json={"doc_id": doc_id, "query": "how long is the term?"})
    run, nodes = _run_tree(spans)
    assert [n.name for n in nodes] == ["node.retrieve", "node.consolidate", "node.summarize", "node.verify",
                                       "node.finalize"]
    (http,) = [s for s in spans.get_finished_spans() if s.name == "POST /query"]
    assert run.parent.span_id == http.context.span_id
    assert run.attributes["outcome"] == "ok" and run.attributes["query.action"] == "query"
    assert run.attributes["doc.id"] == doc_id
    assert {n.attributes["outcome"] for n in nodes} == {"ok"}
    retrieved, _, summarized, verified, _ = nodes
    assert retrieved.attributes["retrieval.top_cosine"] == 0.6 and retrieved.attributes["retrieval.hits"] == 1
    assert [e.name for e in summarized.events] == ["summarizing", "first_token"]
    assert verified.attributes["verification.status"] == "ok"


def test_a_retried_node_shows_both_attempts(client, doc_id, stubs, flaky, spans):
    stubs.setattr(prepare, "has_chunks", lambda _id: True)
    stubs.setattr(summarize, "summarize_stream", lambda *a: iter(GROUNDED))
    flaky(retrieve, "hybrid_search", openai.APIConnectionError(request=_REQ))
    client.post("/query", json={"doc_id": doc_id, "query": "how long is the term?"})
    run, nodes = _run_tree(spans)
    failed, retried = [n for n in nodes if n.name == "node.retrieve"]
    assert failed.status.status_code == StatusCode.ERROR and failed.attributes["outcome"] == "error"
    assert [e.name for e in failed.events] == ["embedding_query", "searching", "exception"]
    assert retried.attributes["outcome"] == "ok" and run.attributes["outcome"] == "ok"


def test_a_review_pause_is_not_an_error(client, doc_id, stubs, spans):
    stubs.setattr(prepare, "has_chunks", lambda _id: True)
    r = client.post("/query", json={"doc_id": doc_id, "query": "how long is the term?"})
    run, nodes = _run_tree(spans)
    # interrupt() is an exception inside LangGraph; the span must not read it as a failure.
    assert nodes[-1].name == "node.review" and nodes[-1].attributes["outcome"] == "paused"
    assert nodes[-1].status.status_code == StatusCode.UNSET and not nodes[-1].events
    assert run.attributes["outcome"] == "paused" and run.status.status_code == StatusCode.UNSET

    spans.clear()
    client.post("/query/resume", json={"query_id": _data(r.text, "review")["query_id"], "action": "accept"})
    resumed, nodes = _run_tree(spans)  # a new request, so a new trace, tied to the first by query.id
    assert resumed.context.trace_id != run.context.trace_id
    assert resumed.attributes["query.id"] == run.attributes["query.id"]
    assert resumed.attributes["query.action"] == "accept" and resumed.attributes["outcome"] == "ok"
    assert [n.name for n in nodes] == ["node.review", "node.finalize"]


def test_a_recovered_run_traces_only_the_nodes_it_reruns(client, doc_id, stubs, flaky, spans):
    stubs.setattr(prepare, "has_chunks", lambda _id: True)
    stubs.setattr(summarize, "summarize_stream", lambda *a: iter(GROUNDED))
    flaky(summarize, "summarize_stream", openai.APIConnectionError(request=_REQ), fails=2)
    r = client.post("/query", json={"doc_id": doc_id, "query": "how long is the term?"})
    run, nodes = _run_tree(spans)
    assert run.status.status_code == StatusCode.ERROR and run.attributes["outcome"] == "error"
    assert [n.name for n in nodes] == ["node.retrieve", "node.consolidate", "node.summarize", "node.summarize"]

    spans.clear()
    client.post("/query/resume", json={"query_id": _data(r.text, "error")["query_id"], "action": "recover"})
    recovered, nodes = _run_tree(spans)
    assert recovered.attributes["query.action"] == "recover" and recovered.attributes["outcome"] == "ok"
    assert [n.name for n in nodes] == ["node.summarize", "node.verify", "node.finalize"]


def test_prepare_stages_are_events_on_its_span(client, doc_id, stubs, spans):
    stubs.setattr(prepare, "has_chunks", lambda _id: False)
    client.post("/query", json={"doc_id": doc_id, "query": "how long is the term?"})
    _, nodes = _run_tree(spans)
    assert nodes[0].name == "node.prepare" and [e.name for e in nodes[0].events] == ["extracting"]
