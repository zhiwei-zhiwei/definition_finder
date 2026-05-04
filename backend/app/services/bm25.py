import re

from rank_bm25 import BM25Okapi
from sqlalchemy.orm import Session

from app.models.schema import ChildChunk

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
STOP_WORDS: frozenset[str] = frozenset({
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "but",
    "is", "are", "was", "were", "be", "been", "being", "by", "with", "at",
    "as", "from", "that", "this", "these", "those", "it", "its", "what",
    "which", "who", "whom", "whose", "when", "where", "why", "how", "do",
    "does", "did", "can", "could", "will", "would", "should", "may", "might",
    "i", "you", "he", "she", "we", "they", "me", "us", "them", "my", "your",
    "our", "their", "if", "then", "than", "about", "into", "over", "under",
    "between", "out", "up", "down", "no", "not", "so", "any", "all", "some",
})


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text) if t.lower() not in STOP_WORDS]


def bm25_search(db: Session, doc_id: str, query: str, top_n: int) -> list[tuple[str, float]]:
    """Build a per-doc BM25 index in memory and rank child chunks by lexical match.

    Returns [(child_id, score), ...] sorted by score desc, length <= top_n.
    """
    rows = (
        db.query(ChildChunk.id, ChildChunk.text)
        .filter(ChildChunk.doc_id == doc_id)
        .all()
    )
    if not rows:
        return []
    qtokens = tokenize(query)
    if not qtokens:
        return []
    ids = [r.id for r in rows]
    corpus = [tokenize(r.text) or [""] for r in rows]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(qtokens)
    ranked = sorted(zip(ids, scores), key=lambda x: x[1], reverse=True)
    return [(cid, float(s)) for cid, s in ranked[:top_n] if s > 0]
