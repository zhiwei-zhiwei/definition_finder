"""The /query pipeline as a LangGraph state graph.

    START -(not prepared)-> prepare -> retrieve -> consolidate -(evidence)-> summarize -> verify -> finalize -(ok)-> END
          -(prepared)-----------------^   ^              -(none)---> insufficient ----------^    |
                                          +------------ retry (top_k x2) -- review <-(flagged)--+
                                                                              +-- accept / reject --> finalize

A flagged answer pauses the run in `review` (LangGraph `interrupt()`) until a human
accepts it, rejects it, or asks for a retry with more passages (POST /query/resume).

Every SSE event the route streams (except done/error) is written by a node via
`writer`, and the route relays them with `astream(stream_mode="custom")`. Sync nodes
run in LangGraph's thread pool, so the blocking OpenAI calls don't stall the event
loop and tokens still flush as the model produces them. `writer` is injected as a
parameter rather than fetched with `get_stream_writer()`, which async nodes can't
reach on Python < 3.11.

Each run is a checkpointed thread keyed by its query id (the app swaps in a SQLite
checkpointer at startup, see `checkpointed`). A checkpoint is written after every step
(`durability="sync"`), and `run` deletes the thread once the run finishes. A paused run
waits for review; a failed (or cut-off) one can be recovered: it continues from the
failed node, without re-running the nodes before it.
"""
# ponytail: two resumes of one paused run at once both execute; the UI sends one at a time
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import TypedDict

import aiosqlite
import httpx
import openai
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy, StreamWriter, interrupt

from app.config import settings
from app.models.db import SessionLocal
from app.models.schema import Query
from app.services import prepare as prepare_service
from app.services import retrieve as retrieve_service
from app.services import summarize as summarize_service
from app.services import verify as verify_service
from app.services import vectorstore
from app.services.highlight import compute_spans
from app.utils.sse import sse_event

log = logging.getLogger(__name__)

NO_CONTEXT = "No relevant context was found in this document."
MAX_TOP_K = 50  # matches QueryRequest.top_k's bound
STALE_RUN = timedelta(hours=24)  # unfinished runs nobody recovered are dropped at startup after this


class Excerpt(TypedDict):
    """One consolidated parent as the summarizer sees it."""
    text: str  # clean text
    marked: str  # text with [page N] markers (falls back to text for non-PDFs / old docs)
    page_start: int | None
    page_end: int | None


class QueryInput(TypedDict):
    query_id: str  # the run's thread id, and its cached Query row id
    doc_id: str
    query: str
    top_k: int
    user_id: str | None


class QueryState(QueryInput, total=False):
    hits: list[retrieve_service.Hit]
    top_cosine: float  # best child cosine over the whole doc, the evidence gate's signal
    winners: list[str]
    votes: dict[str, int]
    excerpts: list[Excerpt]
    snippets: list[dict]
    answer: str
    verification: dict  # verify.check(): status ok | warning | insufficient_evidence, issues, needs_review
    review: str  # "accepted" | "rejected": a human's verdict on a flagged answer


def _route_start(state: QueryState) -> str:
    """First query on a doc runs the expensive prepare (extract -> chunk -> embed)."""
    return "retrieve" if prepare_service.has_chunks(state["doc_id"]) else "prepare"


async def prepare(state: QueryState, writer: StreamWriter) -> dict:
    async for event in prepare_service.prepare_document_stream(state["doc_id"]):
        writer(event)
    return {}


def retrieve(state: QueryState, writer: StreamWriter) -> dict:
    writer(sse_event("stage", {"stage": "embedding_query", "message": "Embedding your query"}))
    qvec = retrieve_service.embed_query(state["query"])
    # Hybrid search: vector + BM25 fused via Reciprocal Rank Fusion.
    writer(sse_event("stage", {"stage": "searching", "top_k": state["top_k"]}))
    with SessionLocal() as db:
        hits = retrieve_service.hybrid_search(db, state["doc_id"], qvec, state["query"], state["top_k"])
        hits = retrieve_service.enrich_hits_from_db(db, hits)
        # ponytail: a second vector scan (~ms per doc); return it from hybrid_search if docs grow large
        best = vectorstore.search(db, state["doc_id"], qvec, 1)
    return {"hits": hits, "top_cosine": best[0][1] if best else 0.0}


def consolidate(state: QueryState, writer: StreamWriter) -> dict:
    hits = state["hits"]
    winners, votes = retrieve_service.consolidate(hits, settings.PARENT_CONSOLIDATION_THRESHOLD)
    writer(sse_event("stage", {"stage": "consolidating", "parent_votes": votes}))
    writer(sse_event("stage", {"stage": "fetching_context", "winning_parents": winners}))
    with SessionLocal() as db:
        parents = retrieve_service.fetch_parent_texts(db, winners)
    excerpts = [Excerpt(text=p.text, marked=p.marked_text or p.text, page_start=p.page_start, page_end=p.page_end)
                for p in parents]
    # Clickable snippets are the child hits, not the parents.
    snippets = [
        {
            "id": h.child_id,
            "parent_id": h.parent_id,
            "text": h.text,
            "score": h.score,
            "page_start": h.page_start,
            "page_end": h.page_end,
            "bboxes": h.bboxes,
            "html_anchor": h.html_anchor,
            "highlight_spans": compute_spans(h.text, state["query"]),
        }
        for h in hits
    ]
    # Sent by summarize, not here: when the evidence gate fires they are just the least-bad
    # passages of an unrelated doc, and the UI would show them as sources.
    return {"winners": winners, "votes": votes, "excerpts": excerpts, "snippets": snippets}


def summarize(state: QueryState, writer: StreamWriter) -> dict:
    writer(sse_event("snippets", state["snippets"]))
    writer(sse_event("stage", {"stage": "summarizing"}))
    excerpts = state["excerpts"]
    tokens = []
    # Keep None pages (non-PDFs): summarize zips texts with pages by position.
    for tok in summarize_service.summarize_stream(
        state["query"], [e["marked"] for e in excerpts], [e["page_start"] for e in excerpts]
    ):
        tokens.append(tok)
        writer(sse_event("token", tok))
    return {"answer": "".join(tokens)}


def _route_evidence(state: QueryState) -> str:
    """Skip the LLM when nothing in the doc is close to the question (see EVIDENCE_MIN_COSINE)."""
    return "summarize" if state["excerpts"] and state["top_cosine"] >= settings.EVIDENCE_MIN_COSINE else "insufficient"


def insufficient(state: QueryState, writer: StreamWriter) -> dict:
    """Fallback answer with no LLM call, flagged for review like a model refusal. No snippets:
    nothing retrieved supports an answer, so none is shown or cached."""
    writer(sse_event("stage", {"stage": "summarizing", "fallback": "insufficient_evidence"}))
    writer(sse_event("token", NO_CONTEXT))
    reason = (f"best passage similarity {state['top_cosine']:.2f} is below {settings.EVIDENCE_MIN_COSINE:.2f}"
              if state["excerpts"] else "no passages were retrieved")
    result = {"status": "insufficient_evidence", "issues": [{"kind": "low_evidence", "detail": reason}],
              "needs_review": True}
    writer(sse_event("verification", result))
    return {"answer": NO_CONTEXT, "verification": result, "snippets": []}


def verify(state: QueryState, writer: StreamWriter) -> dict:
    """Runs after the answer has streamed, so it annotates rather than blocks it."""
    writer(sse_event("stage", {"stage": "verifying"}))
    result = verify_service.check(state["answer"], state["excerpts"])
    writer(sse_event("verification", result))
    return {"verification": result}


def _route_review(state: QueryState) -> str:
    """Flagged answers wait for a human. The evidence gate's fallback doesn't: more passages
    can't raise the doc's best cosine, so there is nothing to retry."""
    v = state["verification"]
    gated = any(i["kind"] == "low_evidence" for i in v["issues"])
    return "review" if v["needs_review"] and not gated and not state.get("review") else END


def review(state: QueryState) -> Command:
    """Pause until POST /query/resume. On resume LangGraph re-runs this node from the top
    and interrupt() returns the human's decision, so nothing before it may have effects."""
    can_retry = state["top_k"] < MAX_TOP_K
    decision = interrupt({"can_retry": can_retry, "top_k": state["top_k"]})
    if decision == "retry" and can_retry:
        return Command(goto="retrieve", update={"top_k": min(state["top_k"] * 2, MAX_TOP_K)})
    # A rejected answer stays cached: the verdict is a human label for scoring checks later.
    return Command(goto="finalize", update={"review": "rejected" if decision == "reject" else "accepted"})


def finalize(state: QueryState) -> dict:
    """Cache the result for the signed-in user's Queries tab; a failed cache never fails the query.
    Runs again after a retry or a verdict, overwriting the same row."""
    if state["user_id"] is not None:
        try:
            _persist_query(state)
        except Exception:
            log.exception("Failed to cache query result")
    return {}


_KEY_TAKEAWAY_MARKER = "**Key Takeaway:**"


def _extract_key_takeaway(text: str) -> str | None:
    if _KEY_TAKEAWAY_MARKER not in text:
        return None
    tail = text.split(_KEY_TAKEAWAY_MARKER, 1)[1].strip()
    return tail or None


def _persist_query(state: QueryState) -> None:
    with SessionLocal() as db:
        db.merge(Query(
            id=state["query_id"],
            user_id=state["user_id"],
            doc_id=state["doc_id"],
            query_text=state["query"],
            top_k=state["top_k"],
            snippets_json=json.dumps(state["snippets"]),
            summary_text=state["answer"],
            key_takeaway=_extract_key_takeaway(state["answer"]),
            verification_json=json.dumps(state["verification"]),
            review_status=state.get("review") or ("pending" if _route_review(state) == "review" else None),
        ))
        db.commit()


def _transient(exc: Exception) -> bool:
    """Worth another attempt: the network or OpenAI hiccupped. Not a 4xx (a bad key or
    request fails the same way again) and not our own bugs."""
    return isinstance(exc, (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError,
                            httpx.TransportError))


# The SDK already retries each request (honouring 429 Retry-After); this re-runs the node for
# what it can't: a stream dropped mid-answer, or a failure that outlasted its retries.
_RETRY = RetryPolicy(max_attempts=2, initial_interval=0.5, retry_on=_transient)


def _build() -> StateGraph:
    g = StateGraph(QueryState, input_schema=QueryInput)
    for node in (prepare, consolidate, insufficient, verify, finalize):
        g.add_node(node.__name__, node)
    # Not prepare: a re-run deletes the doc's chunks and pays for every embedding again.
    for node in (retrieve, summarize):
        g.add_node(node.__name__, node, retry_policy=_RETRY)
    g.add_node("review", review, destinations=("retrieve", "finalize"))
    g.add_conditional_edges(START, _route_start, ["prepare", "retrieve"])
    g.add_edge("prepare", "retrieve")
    g.add_edge("retrieve", "consolidate")
    g.add_conditional_edges("consolidate", _route_evidence, ["summarize", "insufficient"])
    g.add_edge("summarize", "verify")
    g.add_edge("verify", "finalize")
    g.add_edge("insufficient", "finalize")
    g.add_conditional_edges("finalize", _route_review, ["review", END])
    return g


# Checkpoints hold retrieve.Hit dataclasses; msgpack only revives allow-listed classes.
_SERDE = JsonPlusSerializer(allowed_msgpack_modules=[("app.services.retrieve", "Hit")])
_BUILDER = _build()
graph = _BUILDER.compile(checkpointer=InMemorySaver(serde=_SERDE))  # until the app starts


@asynccontextmanager
async def checkpointed():
    """App lifespan: keep checkpoints in SQLite so paused runs survive a restart."""
    global graph
    async with aiosqlite.connect(settings.CHECKPOINT_DB) as conn:
        saver = AsyncSqliteSaver(conn, serde=_SERDE)
        await saver.setup()
        graph = _BUILDER.compile(checkpointer=saver)
        await sweep(STALE_RUN)
        yield


async def sweep(older_than: timedelta) -> None:
    """Drop runs that failed or were cut off and weren't recovered in time. Paused runs
    stay: they wait for a human, however long that takes."""
    cutoff = datetime.now(timezone.utc) - older_than
    async with graph.checkpointer.conn.execute("SELECT DISTINCT thread_id FROM checkpoints") as cur:
        threads = [row[0] for row in await cur.fetchall()]
    for thread_id in threads:
        snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        if not snap.interrupts and datetime.fromisoformat(snap.created_at) < cutoff:
            await graph.checkpointer.adelete_thread(thread_id)


async def run(graph_input, query_id: str):
    """Relay one run's SSE events; drop its checkpoints once it has finished. A pause
    becomes a `review` event carrying the interrupt's payload and the id to resume. A
    failed run keeps them (its last checkpoint is just before the failed node)."""
    config = {"configurable": {"thread_id": query_id}}
    try:
        async for mode, chunk in graph.astream(graph_input, config, stream_mode=["custom", "updates"],
                                               durability="sync"):
            if mode == "custom":
                yield chunk
            elif "__interrupt__" in chunk:
                yield sse_event("review", {"query_id": query_id, **chunk["__interrupt__"][0].value})
    finally:
        if not (await graph.aget_state(config)).next:
            await graph.checkpointer.adelete_thread(query_id)


async def paused(query_id: str) -> dict | None:
    """State of a run waiting in `review`, or None (finished, expired, never existed)."""
    snap = await graph.aget_state({"configurable": {"thread_id": query_id}})
    return snap.values if snap.interrupts else None


async def recoverable(query_id: str) -> dict | None:
    """State of a run that failed or was cut off before finishing (not paused), or None.
    Running it again with no input continues from the node that failed."""
    snap = await graph.aget_state({"configurable": {"thread_id": query_id}})
    return snap.values if snap.next and not snap.interrupts else None
