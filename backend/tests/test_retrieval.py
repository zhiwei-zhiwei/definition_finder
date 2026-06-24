"""Retrieval layer: SQLite/numpy vector store, contextual BM25, and the hybrid
fusion + parent-page hydration. These exercise the pieces changed by the move to
contextual embeddings; nothing here hits the network (vectors are inserted by
hand, so embedding/context generation are not involved)."""
import uuid

from app.models.db import SessionLocal
from app.models.schema import ChildChunk, Document, ParentChunk
from app.services import bm25, retrieve, vectorstore
from app.services.prepare import _contextualized


def _seed(children: list[dict], parent_page: int = 7) -> str:
    """Insert a doc + one parent + the given children. Each child dict:
    {text, context_text?, embedding(list[float])?}. Returns doc_id."""
    doc_id = uuid.uuid4().hex
    parent_id = f"{doc_id[:8]}_Parent_00001"
    with SessionLocal() as db:
        db.add(Document(id=doc_id, filename="x.pdf", mime="application/pdf", size_bytes=1, status="ready"))
        db.add(ParentChunk(id=parent_id, doc_id=doc_id, text="parent body",
                           page_start=parent_page, page_end=parent_page, token_count=2))
        for i, c in enumerate(children):
            db.add(ChildChunk(
                id=f"{parent_id.replace('Parent_', 'Child_')}_{i:03d}",
                parent_id=parent_id, doc_id=doc_id,
                text=c["text"], context_text=c.get("context_text"),
                embedding=vectorstore.pack(c["embedding"]) if "embedding" in c else None,
                token_count=1,
            ))
        db.commit()
    return doc_id


def test_vectorstore_round_trip_ranks_by_cosine(client):
    doc_id = _seed([
        {"text": "north", "embedding": [1.0, 0.0, 0.0]},
        {"text": "east", "embedding": [0.0, 1.0, 0.0]},
        {"text": "up", "embedding": [0.0, 0.0, 1.0]},
    ])
    with SessionLocal() as db:
        ranked = vectorstore.search(db, doc_id, [0.9, 0.1, 0.0], top_k=3)
    assert len(ranked) == 3
    assert ranked[0][0].endswith("_000")  # the "north" child wins
    assert ranked[0][1] > ranked[1][1] > ranked[2][1]


def test_contextual_bm25_matches_on_context_only(client):
    # The query word ("photosynthesis") appears only in the situating context,
    # never in the chunk text — contextual BM25 must still surface it. Several
    # filler children keep BM25's IDF positive (a term in half a 2-doc corpus
    # scores 0).
    doc_id = _seed([
        {"text": "The process converts light into chemical energy."},
        {"text": "Unrelated sentence about tax brackets and filing."},
        {"text": "Quarterly revenue grew across every region."},
        {"text": "The committee adjourned before the vote."},
        {"text": "Shipping delays affected the supply chain."},
    ])
    # Add context to the first child.
    with SessionLocal() as db:
        rows = db.query(ChildChunk).filter(ChildChunk.doc_id == doc_id).order_by(ChildChunk.id).all()
        rows[0].context_text = "This passage explains photosynthesis in plants."
        db.commit()
        hits = bm25.bm25_search(db, doc_id, "photosynthesis", top_n=5)
    assert hits, "expected a contextual BM25 hit"
    assert hits[0][0].endswith("_000")


def test_hybrid_search_hydrates_parent_page(client):
    doc_id = _seed([
        {"text": "alpha statute clause", "embedding": [1.0, 0.0]},
        {"text": "beta unrelated", "embedding": [0.0, 1.0]},
    ], parent_page=12)
    with SessionLocal() as db:
        hits = retrieve.hybrid_search(db, doc_id, [1.0, 0.0], "alpha", top_k=2)
    assert hits
    top = hits[0]
    assert "alpha" in top.text
    # Children don't store pages; the span is hydrated from the parent.
    assert top.page_start == 12 and top.page_end == 12
    # Score is normalized by the top hit.
    assert 0.0 < top.score <= 1.0


def test_contextualized_prepends_context():
    assert _contextualized("Section 2 covers X.", "the body") == "Section 2 covers X.\n\nthe body"
    assert _contextualized(None, "the body") == "the body"
    assert _contextualized("", "the body") == "the body"
