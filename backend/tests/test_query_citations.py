"""Excerpts and their page labels must stay aligned when some parents have no page."""
import uuid
from types import SimpleNamespace

from app.models.db import SessionLocal
from app.models.schema import Document
from app.services import prepare, retrieve, summarize
from app.services.chunker import chunk_document
from app.services.ingest import ExtractedItem


def test_summary_context_keeps_pages_aligned(client, monkeypatch):
    seen = {}

    def fake_summarize(query, parent_texts, pages):
        seen["context"] = summarize._format_context(query, parent_texts, pages)
        yield "ok"

    r = retrieve
    monkeypatch.setattr(prepare, "has_chunks", lambda _id: True)
    monkeypatch.setattr(r, "embed_query", lambda _q: None)
    monkeypatch.setattr(r, "hybrid_search", lambda *a: [])
    monkeypatch.setattr(r, "consolidate", lambda *a: (["p1", "p2"], {}))
    monkeypatch.setattr(r, "fetch_parent_texts", lambda db, ids: [
        SimpleNamespace(text="no-page text", page_start=None, page_end=None, marked_text=None),  # falls back to text
        SimpleNamespace(text="page-seven text", page_start=7, page_end=7, marked_text="[page 7] page-seven text"),
    ])
    monkeypatch.setattr(summarize, "summarize_stream", fake_summarize)

    doc_id = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(Document(id=doc_id, filename="x.docx", mime="application/msword", size_bytes=1, status="ready"))
        db.commit()
    client.post("/query", json={"doc_id": doc_id, "query": "q"})

    ctx = seen["context"]
    assert "### Excerpt 1 \nno-page text" in ctx
    assert "### Excerpt 2 \n[page 7] page-seven text" in ctx  # markers replace the first-page header
    assert "(page 7)" not in summarize._format_context("q", ["[page 7] a\n\n[page 8] b"], [7])
    assert "### Excerpt 1 (page 3)\nold" in summarize._format_context("q", ["old"], [3])  # pre-marker docs


def test_parent_marks_each_page_break():
    items = [ExtractedItem("a1", page=10), ExtractedItem("a2", page=10), ExtractedItem("furniture", page=None),
             ExtractedItem("b1", page=11)]
    (parent,), _ = chunk_document("d" * 32, items)
    assert parent.marked_text == "[page 10] a1\n\na2\n\nfurniture\n\n[page 11] b1"
    assert parent.text == "a1\n\na2\n\nfurniture\n\nb1"  # stored/shown text stays clean
    (no_pages,), _ = chunk_document("d" * 32, [ExtractedItem("docx para")])
    assert no_pages.marked_text is None
