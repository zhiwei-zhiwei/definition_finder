import asyncio
import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.models.db import SessionLocal
from app.models.schema import Document
from app.services import prepare, retrieve, summarize
from app.utils.sse import sse_event

log = logging.getLogger(__name__)
router = APIRouter(tags=["query"])

_SENTINEL = object()


class QueryRequest(BaseModel):
    doc_id: str
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    summary_style: str = "concise"


@router.post("/query")
async def query_stream(req: QueryRequest):
    return EventSourceResponse(_query_stream(req), ping=15)


async def _query_stream(req: QueryRequest):
    loop = asyncio.get_event_loop()
    try:
        with SessionLocal() as db:
            doc = db.get(Document, req.doc_id)
            if doc is None:
                yield sse_event("error", {"message": "document not found"})
                return

        # 0. First-query preparation: extract + chunk + embed, cached for reuse.
        if not prepare.has_chunks(req.doc_id):
            async for event in prepare.prepare_document_stream(req.doc_id):
                yield event

        # 1. Embed the query (actual OpenAI call happens inside this stage).
        yield sse_event("stage", {"stage": "embedding_query", "message": "Embedding your query"})
        await asyncio.sleep(0)
        qvec = await loop.run_in_executor(None, retrieve.embed_query, req.query)

        # 2. Vector search against Chroma.
        yield sse_event("stage", {"stage": "searching", "top_k": req.top_k})
        await asyncio.sleep(0)
        hits = await loop.run_in_executor(
            None, retrieve.search_with_vec, req.doc_id, qvec, req.top_k
        )
        with SessionLocal() as db:
            hits = retrieve.enrich_hits_from_db(db, hits)

        # 3. Consolidate by parent.
        winners, votes = retrieve.consolidate(hits, settings.PARENT_CONSOLIDATION_THRESHOLD)
        yield sse_event("stage", {"stage": "consolidating", "parent_votes": votes})
        await asyncio.sleep(0)

        # 4. Fetch full parent context.
        yield sse_event("stage", {"stage": "fetching_context", "winning_parents": winners})
        await asyncio.sleep(0)
        with SessionLocal() as db:
            parent_rows = retrieve.fetch_parent_texts(db, winners)
        parent_texts = [p.text for p in parent_rows]
        pages = [p.page_start for p in parent_rows if p.page_start is not None]

        # Emit the clickable snippets (children, not parents).
        snippets_payload = [
            {
                "id": h.child_id,
                "parent_id": h.parent_id,
                "text": h.text,
                "score": h.score,
                "page_start": h.page_start,
                "page_end": h.page_end,
                "bboxes": h.bboxes,
                "html_anchor": h.html_anchor,
            }
            for h in hits
        ]
        yield sse_event("snippets", snippets_payload)
        await asyncio.sleep(0)

        # 5. Stream LLM summary — tokens flow as OpenAI produces them.
        yield sse_event("stage", {"stage": "summarizing"})
        await asyncio.sleep(0)

        if not parent_texts:
            yield sse_event("token", "No relevant context was found in this document.")
        else:
            async for tok in _bridge_token_stream(loop, req.query, parent_texts, pages):
                yield sse_event("token", tok)

        yield sse_event("done", {})
    except Exception as exc:
        log.exception("Query failed")
        yield sse_event("error", {"message": str(exc)})


async def _bridge_token_stream(loop, query: str, parent_texts: list[str], pages: list[int]):
    """Run the blocking OpenAI generator in a worker thread and bridge each
    token into the async world via a queue, so tokens flush to the browser
    as soon as the model produces them."""
    q: asyncio.Queue = asyncio.Queue()

    def _producer():
        try:
            for tok in summarize.summarize_stream(query, parent_texts, pages):
                loop.call_soon_threadsafe(q.put_nowait, tok)
        except Exception as exc:
            loop.call_soon_threadsafe(q.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(q.put_nowait, _SENTINEL)

    loop.run_in_executor(None, _producer)

    while True:
        item = await q.get()
        if item is _SENTINEL:
            return
        if isinstance(item, Exception):
            raise item
        yield item
