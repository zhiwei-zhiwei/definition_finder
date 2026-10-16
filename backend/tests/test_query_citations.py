"""Excerpts and their page labels must stay aligned when some parents have no page."""
import uuid
from types import SimpleNamespace

from app.models.db import SessionLocal
from app.models.schema import Document
from app.routes import query as query_route


def test_summary_context_keeps_pages_aligned(client, monkeypatch):
    seen = {}

    def fake_summarize(query, parent_texts, pages):
        seen["context"] = query_route.summarize._format_context(query, parent_texts, pages)
        yield "ok"

    r = query_route.retrieve
    monkeypatch.setattr(query_route.prepare, "has_chunks", lambda _id: True)
    monkeypatch.setattr(r, "embed_query", lambda _q: None)
    monkeypatch.setattr(r, "hybrid_search", lambda *a: [])
    monkeypatch.setattr(r, "consolidate", lambda *a: (["p1", "p2"], {}))
    monkeypatch.setattr(r, "fetch_parent_texts", lambda db, ids: [
        SimpleNamespace(text="no-page text", page_start=None),
        SimpleNamespace(text="page-seven text", page_start=7),
    ])
    monkeypatch.setattr(query_route.summarize, "summarize_stream", fake_summarize)

    doc_id = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(Document(id=doc_id, filename="x.docx", mime="application/msword", size_bytes=1, status="ready"))
        db.commit()
    client.post("/query", json={"doc_id": doc_id, "query": "q"})

    ctx = seen["context"]
    assert "### Excerpt 1 \nno-page text" in ctx
    assert "### Excerpt 2 (page 7)\npage-seven text" in ctx
