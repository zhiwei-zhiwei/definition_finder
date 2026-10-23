import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from langgraph.types import Command
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.models.db import SessionLocal
from app.models.schema import Document, User
from app.services import query_graph
from app.services.auth import get_current_user_optional
from app.utils.sse import sse_event

log = logging.getLogger(__name__)
router = APIRouter(tags=["query"])


class QueryRequest(BaseModel):
    doc_id: str
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    summary_style: str = "concise"


@router.post("/query")
async def query_stream(
    req: QueryRequest,
    user: User | None = Depends(get_current_user_optional),
):
    user_id = user.id if user is not None else None
    return EventSourceResponse(_query_stream(req, user_id), ping=15)


class ResumeRequest(BaseModel):
    query_id: str
    action: Literal["accept", "reject", "retry", "recover"]


@router.post("/query/resume")
async def resume_stream(
    req: ResumeRequest,
    user: User | None = Depends(get_current_user_optional),
):
    """Continue a run paused for review (the `review` SSE event's query_id), or recover
    one that failed (`recover`, with the `error` event's query_id)."""
    user_id = user.id if user is not None else None
    return EventSourceResponse(_resume_stream(req, user_id), ping=15)


def _doc_error(doc_id: str, user_id: str | None) -> str | None:
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        if doc is None:
            return "document not found"
        # Owned docs may only be queried by their owner; anon (unclaimed)
        # docs stay open to whoever holds the id until claimed on sign-in.
        if doc.user_id is not None and doc.user_id != user_id:
            return "forbidden"
    return None


async def _error(exc: Exception, query_id: str | None) -> str:
    """The error event; it names the run when the run can be recovered."""
    payload = {"message": str(exc)}
    if query_id and await query_graph.recoverable(query_id) is not None:
        payload["query_id"] = query_id
    return sse_event("error", payload)


async def _query_stream(req: QueryRequest, user_id: str | None):
    query_id = None
    try:
        if err := _doc_error(req.doc_id, user_id):
            yield sse_event("error", {"message": err})
            return

        query_id = uuid.uuid4().hex
        state = {"query_id": query_id, "doc_id": req.doc_id, "query": req.query, "top_k": req.top_k,
                 "user_id": user_id}
        async for event in query_graph.run(state, query_id):
            yield event
        yield sse_event("done", {})
    except Exception as exc:
        log.exception("Query failed")
        yield await _error(exc, query_id)



async def _resume_stream(req: ResumeRequest, user_id: str | None):
    try:
        # Only the user who ran it may resume; an anon run's id is its capability, like an anon doc's.
        recover = req.action == "recover"
        state = await (query_graph.recoverable if recover else query_graph.paused)(req.query_id)
        if state is None or state["user_id"] != user_id:
            yield sse_event("error", {"message": "nothing to recover" if recover else "nothing to review"})
            return
        if err := _doc_error(state["doc_id"], user_id):
            yield sse_event("error", {"message": err})
            return
        # No input continues a failed run from its last checkpoint.
        async for event in query_graph.run(None if recover else Command(resume=req.action), req.query_id,
                                             req.action):
            yield event
        yield sse_event("done", {})
    except Exception as exc:
        log.exception("Resume failed")
        yield await _error(exc, req.query_id)
