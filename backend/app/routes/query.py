import logging

from fastapi import APIRouter, Depends
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


async def _query_stream(req: QueryRequest, user_id: str | None):
    try:
        with SessionLocal() as db:
            doc = db.get(Document, req.doc_id)
            if doc is None:
                yield sse_event("error", {"message": "document not found"})
                return
            # Owned docs may only be queried by their owner; anon (unclaimed)
            # docs stay open to whoever holds the id until claimed on sign-in.
            if doc.user_id is not None and doc.user_id != user_id:
                yield sse_event("error", {"message": "forbidden"})
                return

        state = {"doc_id": req.doc_id, "query": req.query, "top_k": req.top_k, "user_id": user_id}
        async for event in query_graph.graph.astream(state, stream_mode="custom"):
            yield event
        yield sse_event("done", {})
    except Exception as exc:
        log.exception("Query failed")
        yield sse_event("error", {"message": str(exc)})

