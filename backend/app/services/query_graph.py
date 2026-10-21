"""The /query pipeline as a LangGraph state graph.

    START -(not prepared)-> prepare -> retrieve -> consolidate -> summarize -> verify -> finalize -> END
          -(prepared)-----------------^

Every SSE event the route streams (except done/error) is written by a node via
`writer`, and the route relays them with `astream(stream_mode="custom")`. Sync nodes
run in LangGraph's thread pool, so the blocking OpenAI calls don't stall the event
loop and tokens still flush as the model produces them. `writer` is injected as a
parameter rather than fetched with `get_stream_writer()`, which async nodes can't
reach on Python < 3.11.
"""
import json
import logging
import uuid
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import StreamWriter

from app.config import settings
from app.models.db import SessionLocal
from app.models.schema import Query
from app.services import prepare as prepare_service
from app.services import retrieve as retrieve_service
from app.services import summarize as summarize_service
from app.services import verify as verify_service
from app.services.highlight import compute_spans
from app.utils.sse import sse_event

log = logging.getLogger(__name__)

NO_CONTEXT = "No relevant context was found in this document."


class Excerpt(TypedDict):
    """One consolidated parent as the summarizer sees it."""
    text: str  # clean text
    marked: str  # text with [page N] markers (falls back to text for non-PDFs / old docs)
    page_start: int | None
    page_end: int | None


class QueryInput(TypedDict):
    doc_id: str
    query: str
    top_k: int
    user_id: str | None


class QueryState(QueryInput, total=False):
    hits: list[retrieve_service.Hit]
    winners: list[str]
    votes: dict[str, int]
    excerpts: list[Excerpt]
    snippets: list[dict]
    answer: str
    verification: dict  # verify.check(): status ok | warning | insufficient_evidence, issues, needs_review


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
    return {"hits": hits}


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
    writer(sse_event("snippets", snippets))
    return {"winners": winners, "votes": votes, "excerpts": excerpts, "snippets": snippets}


def summarize(state: QueryState, writer: StreamWriter) -> dict:
    writer(sse_event("stage", {"stage": "summarizing"}))
    excerpts = state["excerpts"]
    if not excerpts:
        writer(sse_event("token", NO_CONTEXT))
        return {"answer": NO_CONTEXT}
    tokens = []
    # Keep None pages (non-PDFs): summarize zips texts with pages by position.
    for tok in summarize_service.summarize_stream(
        state["query"], [e["marked"] for e in excerpts], [e["page_start"] for e in excerpts]
    ):
        tokens.append(tok)
        writer(sse_event("token", tok))
    return {"answer": "".join(tokens)}


def verify(state: QueryState, writer: StreamWriter) -> dict:
    """Runs after the answer has streamed, so it annotates rather than blocks it."""
    writer(sse_event("stage", {"stage": "verifying"}))
    result = verify_service.check(state["answer"], state["excerpts"])
    writer(sse_event("verification", result))
    return {"verification": result}


def finalize(state: QueryState) -> dict:
    """Cache the result for the signed-in user's Queries tab; a failed cache never fails the query."""
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
        db.add(Query(
            id=uuid.uuid4().hex,
            user_id=state["user_id"],
            doc_id=state["doc_id"],
            query_text=state["query"],
            top_k=state["top_k"],
            snippets_json=json.dumps(state["snippets"]),
            summary_text=state["answer"],
            key_takeaway=_extract_key_takeaway(state["answer"]),
        ))
        db.commit()


def _build() -> StateGraph:
    g = StateGraph(QueryState, input_schema=QueryInput)
    for node in (prepare, retrieve, consolidate, summarize, verify, finalize):
        g.add_node(node.__name__, node)
    g.add_conditional_edges(START, _route_start, ["prepare", "retrieve"])
    g.add_edge("prepare", "retrieve")
    g.add_edge("retrieve", "consolidate")
    g.add_edge("consolidate", "summarize")
    g.add_edge("summarize", "verify")
    g.add_edge("verify", "finalize")
    g.add_edge("finalize", END)
    return g


graph = _build().compile()
