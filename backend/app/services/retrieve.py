import json
from collections import Counter
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import settings
from app.models.schema import ChildChunk, ParentChunk
from app.services import bm25 as bm25_service
from app.services import vectorstore
from app.services.embed import embed_batch


@dataclass
class Hit:
    child_id: str
    parent_id: str
    text: str
    score: float
    page_start: int | None
    page_end: int | None
    bboxes: list[dict]
    html_anchor: str | None


def embed_query(query: str) -> list[float]:
    [qvec] = embed_batch([query])
    return qvec


def _hydrate_hits(db: Session, scores: dict[str, float], order: list[str]) -> list[Hit]:
    """Build Hits for `order` (child ids, best first), pulling child text + bboxes
    and the parent's page span from SQLite in one pass. Children don't store their
    own page columns — the page span lives on the parent."""
    if not order:
        return []
    rows = (
        db.query(ChildChunk, ParentChunk.page_start, ParentChunk.page_end)
        .join(ParentChunk, ChildChunk.parent_id == ParentChunk.id)
        .filter(ChildChunk.id.in_(order))
        .all()
    )
    by_id: dict[str, Hit] = {}
    for child, page_start, page_end in rows:
        try:
            bboxes = json.loads(child.bboxes_json) if child.bboxes_json else []
        except Exception:
            bboxes = []
        by_id[child.id] = Hit(
            child_id=child.id,
            parent_id=child.parent_id,
            text=child.text,
            score=scores.get(child.id, 0.0),
            page_start=page_start,
            page_end=page_end,
            bboxes=bboxes,
            html_anchor=child.html_anchor,
        )
    return [by_id[cid] for cid in order if cid in by_id]


def _rrf_merge(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion: combine multiple rankers without score normalization.

    `score(d) = sum over rankers of 1 / (k + rank(d))`. Higher is better.
    """
    out: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking):
            out[cid] = out.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return out


def hybrid_search(
    db: Session,
    doc_id: str,
    qvec: list[float],
    query_text: str,
    top_k: int,
    candidate_n: int | None = None,
) -> list[Hit]:
    """Run vector + BM25 in parallel and fuse with RRF.

    - Vector arm captures semantic neighbours.
    - BM25 arm captures rare lexical matches (proper nouns, statute codes).
    - Fused score replaces the per-arm score on the returned Hit so the UI
      reflects the hybrid ranking.
    """
    n = candidate_n or max(top_k * 4, 20)
    vec_ranked = vectorstore.search(db, doc_id, qvec, n)
    bm25_ranked = bm25_service.bm25_search(db, doc_id, query_text, n)

    fused = _rrf_merge(
        [
            [cid for cid, _ in vec_ranked],
            [cid for cid, _ in bm25_ranked],
        ]
    )
    if not fused:
        return []

    ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:top_k]
    top_score = ranked[0][1] if ranked else 1.0
    # Normalize by the top hit so the UI's percentage badge stays in [0, 1].
    scores = {cid: (s / top_score if top_score > 0 else 0.0) for cid, s in ranked}
    return _hydrate_hits(db, scores, [cid for cid, _ in ranked])


def consolidate(hits: list[Hit], threshold: int | None = None) -> tuple[list[str], dict[str, int]]:
    """Return (parent_ids, all_votes): every parent with a child in the top-K,
    parents with >= `threshold` child hits first, the rest by best-child rank.

    Votes only order parents. Dropping the rest (the old rule, with a top-3
    fallback when nothing cleared the bar) discarded the gold parent for 5 of
    37 AMD 10-K eval queries, once at rank 1, because another parent had 3 votes."""
    th = threshold if threshold is not None else settings.PARENT_CONSOLIDATION_THRESHOLD
    votes = Counter(h.parent_id for h in hits if h.parent_id)
    ranked = list(dict.fromkeys(h.parent_id for h in hits if h.parent_id))  # hits are best-first
    # ponytail: context grows to top_k parents (5 by default); cap it if callers raise top_k a lot
    return sorted(ranked, key=lambda p: votes[p] < th), dict(votes)


def fetch_parent_texts(db: Session, parent_ids: list[str]) -> list[ParentChunk]:
    if not parent_ids:
        return []
    rows = (
        db.query(ParentChunk)
        .filter(ParentChunk.id.in_(parent_ids))
        .all()
    )
    order = {pid: i for i, pid in enumerate(parent_ids)}
    rows.sort(key=lambda p: order.get(p.id, 999))
    return rows


def enrich_hits_from_db(db: Session, hits: list[Hit]) -> list[Hit]:
    """Hit metadata in Chroma is truncated to scalars; pull full bboxes
    from SQLite so the frontend can highlight accurately."""
    if not hits:
        return hits
    ids = [h.child_id for h in hits]
    rows = db.query(ChildChunk).filter(ChildChunk.id.in_(ids)).all()
    by_id = {r.id: r for r in rows}
    for h in hits:
        row = by_id.get(h.child_id)
        if row is None:
            continue
        if row.bboxes_json:
            try:
                h.bboxes = json.loads(row.bboxes_json)
            except Exception:
                pass
        if row.html_anchor:
            h.html_anchor = row.html_anchor
    return hits
