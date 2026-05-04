import json
from collections import Counter
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import settings
from app.models.schema import ChildChunk, ParentChunk
from app.services import bm25 as bm25_service
from app.services.embed import embed_batch
from app.services.vectorstore import collection_for


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


def search_with_vec(doc_id: str, qvec: list[float], top_k: int) -> list[Hit]:
    coll = collection_for(doc_id)
    res = coll.query(query_embeddings=[qvec], n_results=top_k)

    ids = res["ids"][0] if res["ids"] else []
    docs = res["documents"][0] if res.get("documents") else []
    metas = res["metadatas"][0] if res.get("metadatas") else []
    dists = res["distances"][0] if res.get("distances") else [0.0] * len(ids)

    hits: list[Hit] = []
    for cid, text, meta, dist in zip(ids, docs, metas, dists):
        bboxes_raw = meta.get("bboxes_json") or "[]"
        try:
            bboxes = json.loads(bboxes_raw)
        except Exception:
            bboxes = []
        ps = meta.get("page_start")
        pe = meta.get("page_end")
        hits.append(
            Hit(
                child_id=cid,
                parent_id=meta.get("parent_id", ""),
                text=text,
                score=float(1.0 - dist) if dist is not None else 0.0,
                page_start=ps if ps else None,
                page_end=pe if pe else None,
                bboxes=bboxes,
                html_anchor=meta.get("html_anchor") or None,
            )
        )
    return hits


def search(doc_id: str, query: str, top_k: int) -> list[Hit]:
    return search_with_vec(doc_id, embed_query(query), top_k)


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
    vec_hits = search_with_vec(doc_id, qvec, n)
    bm25_hits = bm25_service.bm25_search(db, doc_id, query_text, n)

    fused = _rrf_merge(
        [
            [h.child_id for h in vec_hits],
            [cid for cid, _ in bm25_hits],
        ]
    )
    if not fused:
        return []

    by_id: dict[str, Hit] = {h.child_id: h for h in vec_hits}
    missing = [cid for cid in fused if cid not in by_id]
    if missing:
        rows = db.query(ChildChunk).filter(ChildChunk.id.in_(missing)).all()
        for r in rows:
            by_id[r.id] = Hit(
                child_id=r.id,
                parent_id=r.parent_id,
                text=r.text,
                score=0.0,
                page_start=None,
                page_end=None,
                bboxes=[],
                html_anchor=r.html_anchor,
            )

    ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)
    top_score = ranked[0][1] if ranked else 1.0
    out: list[Hit] = []
    for cid, fused_score in ranked[:top_k]:
        h = by_id.get(cid)
        if h is None:
            continue
        h.score = fused_score / top_score if top_score > 0 else 0.0
        out.append(h)
    return out


def consolidate(hits: list[Hit], threshold: int | None = None) -> tuple[list[str], dict[str, int]]:
    """Return (winning_parent_ids, all_votes).

    Winners = parents with >= `threshold` children in the top-K. If none
    clear the bar, fall back to the top-3 unique parents ordered by their
    best child's score — this keeps the LLM context grounded even when
    votes are spread thin (common on short queries like 'when did X start?')."""
    th = threshold if threshold is not None else settings.PARENT_CONSOLIDATION_THRESHOLD
    votes = Counter(h.parent_id for h in hits if h.parent_id)
    winners = [p for p, _ in votes.most_common() if votes[p] >= th]
    if not winners and hits:
        seen: list[str] = []
        for h in hits:
            if h.parent_id and h.parent_id not in seen:
                seen.append(h.parent_id)
            if len(seen) >= 3:
                break
        winners = seen
    return winners, dict(votes)


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
