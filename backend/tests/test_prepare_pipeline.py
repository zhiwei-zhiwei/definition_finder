"""End-to-end prepare → retrieve, with Docling/OpenAI mocked. Verifies the new
storage path: contextual blurbs land on children, embeddings are written to
SQLite, has_chunks gates on the embedding, and hybrid search reads it back."""
import asyncio
import uuid

from app.models.db import SessionLocal
from app.models.schema import ChildChunk, Document
from app.services import context, embed, ingest, prepare, retrieve
from app.services.ingest import Extracted, ExtractedItem


def _fake_extract(_path):
    items = [
        ExtractedItem(text="Alpha statute clause K-2L-L50 governs filings.", page=3,
                      bbox={"x0": 1.0, "y0": 2.0, "x1": 3.0, "y1": 4.0}),
        ExtractedItem(text="Beta paragraph about unrelated weather patterns.", page=3, bbox=None),
    ]
    full = " ".join(i.text for i in items)
    return Extracted(items=items, html=f"<p>{full}</p>", pages=3, full_text=full,
                     stats={"chars_before": len(full), "chars_after": len(full)})


def _fake_embed(texts):
    # 2-dim, deterministic: [1,0] for chunks mentioning the statute, else [0,1].
    return [[1.0, 0.0] if "K-2L-L50" in t or "statute" in t.lower() else [0.0, 1.0] for t in texts]


def _fake_parent_contexts(doc_text, parents):
    return {pid: "This section defines statutory filing rules." for pid, _ in parents}


def test_prepare_then_retrieve(client, monkeypatch):
    monkeypatch.setattr(ingest, "extract", _fake_extract)
    monkeypatch.setattr(embed, "embed_batch", _fake_embed)
    monkeypatch.setattr(context, "generate_parent_contexts", _fake_parent_contexts)

    doc_id = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(Document(id=doc_id, filename="x.pdf", mime="application/pdf",
                        size_bytes=1, status="ready"))
        db.commit()

    assert not prepare.has_chunks(doc_id)

    async def _run():
        async for _ in prepare.prepare_document_stream(doc_id):
            pass

    asyncio.run(_run())

    # Every child has an embedding and inherited its parent's context.
    with SessionLocal() as db:
        kids = db.query(ChildChunk).filter(ChildChunk.doc_id == doc_id).all()
        assert kids
        assert all(k.embedding is not None for k in kids)
        assert all(k.context_text == "This section defines statutory filing rules." for k in kids)

    assert prepare.has_chunks(doc_id)

    # Vector arm (query near the statute child) retrieves it, page hydrated.
    with SessionLocal() as db:
        hits = retrieve.hybrid_search(db, doc_id, [1.0, 0.0], "statute clause", top_k=3)
    assert hits
    assert "K-2L-L50" in hits[0].text
    assert hits[0].page_start == 3
