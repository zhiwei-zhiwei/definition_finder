"""Vector store: embeddings live as float32 BLOBs on `child_chunks.embedding`
(see models/schema.py). Retrieval is per-document, so the candidate set is a few
hundred to a few thousand vectors — small enough that a brute-force numpy cosine
beats an HNSW index on both latency and operational weight. This replaces the
former per-doc Chroma collection, folding the vectors into the one SQLite file.
"""
import numpy as np
from sqlalchemy.orm import Session

from app.models.schema import ChildChunk

_DTYPE = "<f4"  # little-endian float32 — portable across machines


def pack(vec) -> bytes:
    return np.asarray(vec, dtype=_DTYPE).tobytes()


def unpack(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=_DTYPE)


def search(db: Session, doc_id: str, qvec: list[float], top_k: int) -> list[tuple[str, float]]:
    """Return [(child_id, cosine_score), ...] for the doc, best first, len <= top_k."""
    rows = (
        db.query(ChildChunk.id, ChildChunk.embedding)
        .filter(ChildChunk.doc_id == doc_id, ChildChunk.embedding.isnot(None))
        .all()
    )
    if not rows:
        return []

    q = np.asarray(qvec, dtype=_DTYPE)
    dim = q.shape[0]
    ids: list[str] = []
    vecs: list[np.ndarray] = []
    for cid, blob in rows:
        v = unpack(blob)
        if v.shape[0] != dim:  # stale row from a different EMBED_DIM; skip
            continue
        ids.append(cid)
        vecs.append(v)
    if not ids:
        return []

    mat = np.vstack(vecs)  # (N, D)
    denom = np.linalg.norm(mat, axis=1) * (np.linalg.norm(q) or 1.0)
    sims = (mat @ q) / np.where(denom == 0, 1.0, denom)

    k = min(top_k, len(ids))
    top = np.argpartition(-sims, k - 1)[:k]
    top = top[np.argsort(-sims[top])]
    return [(ids[i], float(sims[i])) for i in top]
