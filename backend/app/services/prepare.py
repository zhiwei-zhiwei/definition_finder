import asyncio
import json
import logging
from pathlib import Path

from bs4 import BeautifulSoup

from app.config import settings
from app.models.db import SessionLocal
from app.models.schema import ChildChunk, Document, ParentChunk
from app.services import chunker, embed, ingest, vectorstore
from app.utils.sse import sse_event

log = logging.getLogger(__name__)


def has_chunks(doc_id: str) -> bool:
    with SessionLocal() as db:
        return db.query(ChildChunk).filter(ChildChunk.doc_id == doc_id).first() is not None


def _resolve_file_path(doc_id: str, filename: str) -> Path:
    ext = Path(filename).suffix
    return settings.upload_path / f"{doc_id}{ext}"


async def prepare_document_stream(doc_id: str):
    """Extract, chunk, embed a document. Yields SSE events so the caller
    can relay progress to the client. Safe to call only when chunks are
    not yet present for the doc."""
    loop = asyncio.get_event_loop()

    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        if doc is None:
            yield sse_event("error", {"message": "document not found"})
            return
        filename = doc.filename

    stored_path = _resolve_file_path(doc_id, filename)

    yield sse_event("stage", {"stage": "extracting", "message": "Extracting text with Docling"})
    await asyncio.sleep(0)
    extracted = await loop.run_in_executor(None, ingest.extract, stored_path)

    yield sse_event(
        "stage",
        {
            "stage": "cleaning",
            "stats": {
                "chars_before": extracted.stats.get("chars_before", 0),
                "chars_after": extracted.stats.get("chars_after", 0),
            },
        },
    )

    parents, children = chunker.chunk_document(doc_id, extracted.items)
    yield sse_event("stage", {"stage": "chunking_parents", "count": len(parents)})
    await asyncio.sleep(0)
    yield sse_event("stage", {"stage": "chunking_children", "count": len(children)})
    await asyncio.sleep(0)

    enhanced_html = _inject_chunk_anchors(extracted.html, children)
    cache_file = settings.cache_path / f"{doc_id}.html"
    cache_file.write_text(enhanced_html, encoding="utf-8")

    with SessionLocal() as db:
        for p in parents:
            db.add(
                ParentChunk(
                    id=p.id,
                    doc_id=doc_id,
                    text=p.text,
                    page_start=p.page_start,
                    page_end=p.page_end,
                    token_count=p.token_count,
                )
            )
        for c in children:
            db.add(
                ChildChunk(
                    id=c.id,
                    parent_id=c.parent_id,
                    doc_id=doc_id,
                    text=c.text,
                    bboxes_json=json.dumps(c.bboxes) if c.bboxes else None,
                    html_anchor=c.html_anchor,
                    token_count=c.token_count,
                )
            )
        doc = db.get(Document, doc_id)
        if doc is not None:
            doc.pages = extracted.pages or None
        db.commit()

    batch_size = 64
    total = len(children)
    if total == 0:
        yield sse_event("stage", {"stage": "embedding", "progress": 1.0})
    else:
        coll = vectorstore.collection_for(doc_id)
        for start in range(0, total, batch_size):
            batch = children[start : start + batch_size]
            vecs = await loop.run_in_executor(None, embed.embed_batch, [c.text for c in batch])
            coll.add(
                ids=[c.id for c in batch],
                embeddings=vecs,
                documents=[c.text for c in batch],
                metadatas=[
                    {
                        "parent_id": c.parent_id,
                        "doc_id": c.doc_id,
                        "page_start": c.page_start or 0,
                        "page_end": c.page_end or 0,
                        "has_bboxes": bool(c.bboxes),
                        "html_anchor": c.html_anchor or "",
                    }
                    for c in batch
                ],
            )
            progress = min(1.0, (start + len(batch)) / total)
            yield sse_event("stage", {"stage": "embedding", "progress": progress})

    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        if doc is not None:
            doc.status = "ready"
            db.commit()


def _inject_chunk_anchors(html: str, children) -> str:
    if not html or not children:
        return html
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return html

    def _refresh_nodes():
        return [n for n in soup.find_all(string=True) if n.strip()]

    text_nodes = _refresh_nodes()

    for child in children:
        needle = child.text.strip()[:80]
        if len(needle) < 20:
            continue
        target = None
        for node in text_nodes:
            if needle in str(node):
                target = node
                break
        if target is None:
            continue
        original = str(target)
        idx = original.find(needle)
        before = original[:idx]
        after = original[idx + len(needle) :]
        span = soup.new_tag("span", attrs={"data-chunk-id": child.html_anchor})
        span.string = needle
        before_node = soup.new_string(before)
        after_node = soup.new_string(after)
        target.replace_with(before_node)
        before_node.insert_after(span)
        span.insert_after(after_node)
        text_nodes = _refresh_nodes()
    return str(soup)
