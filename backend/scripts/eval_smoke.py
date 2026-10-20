"""Offline retrieval gate for CI: replay hybrid search on a frozen document, exit 1 if quality drops.

Loads tests/fixtures/retrieval_amd.npz (chunks + float16 vectors + query vectors, written by
scripts/freeze_retrieval_fixture.py) into an in-memory SQLite and runs the real
retrieve.hybrid_search / consolidate / fetch_parent_texts on it. No Docling, no OpenAI, no
API key: deterministic, free and ~1 s. It catches changes to fusion, BM25, the vector search
and consolidate; changes to chunking or embeddings need a re-freeze (and the live
eval_retrieval.py) to show up.

Usage (from backend/, venv active):

    python scripts/eval_smoke.py

Metrics: pass_at_k / mrr as in eval_retrieval.py (top-k 20), plus context_gold_rate as in
eval_grounding.py: share of queries whose gold passage is in the parent texts the
summarizer gets (app top-k -> consolidate -> parents).
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.models.db import Base
from app.models.schema import ChildChunk, Document, ParentChunk
from app.services import retrieve, vectorstore

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_retrieval import _gold_list, _gold_ranks, _metrics, _norm  # noqa: E402
from freeze_retrieval_fixture import FIXTURE  # noqa: E402

# Floors = the fixture's values (RRF k=20, Docling 2.140, 2026-10-19: P@1 .459, P@5 .676, P@20 .973, MRR .567,
# context .919)
# minus one query (1/37 = .027): one rank shift from float rounding on another CPU passes, two lost queries fail.
# Raise them after a real improvement; re-derive them after a re-freeze.
GATES = {"pass_at_1": 0.43, "pass_at_5": 0.64, "pass_at_20": 0.94, "mrr": 0.54, "context_gold_rate": 0.89}


def _load(db: Session, path: Path) -> tuple[str, list[dict], np.ndarray]:
    """Insert the frozen doc into `db`; returns (doc_id, scored labels, query vectors)."""
    z = np.load(path)
    meta = json.loads(z["meta"].tobytes())
    doc_id = "fixture"
    db.add(Document(id=doc_id, filename=meta["pdf"], mime="application/pdf", size_bytes=0, status="ready"))
    db.add_all(ParentChunk(doc_id=doc_id, token_count=0, **p) for p in meta["parents"])
    db.add_all(ChildChunk(doc_id=doc_id, token_count=0, embedding=vectorstore.pack(v.astype(np.float32)), **c)
               for c, v in zip(meta["children"], z["child_vecs"]))
    db.commit()
    return doc_id, meta["labels"], z["query_vecs"].astype(np.float32)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", type=Path, default=FIXTURE)
    ap.add_argument("--top-k", type=int, default=20)
    args = ap.parse_args()

    settings.RERANK_MODEL = ""  # gate the default pipeline, even if .env turns the reranker on
    engine = create_engine("sqlite://")  # in-memory; never touches data/app.db
    Base.metadata.create_all(engine)
    recs, in_ctx = [], []
    with Session(engine) as db:
        doc_id, labels, qvecs = _load(db, args.fixture)
        for label, qvec in zip(labels, qvecs.tolist()):
            q, golds = label["query"], _gold_list(label)
            t0 = time.perf_counter()
            hits = retrieve.hybrid_search(db, doc_id, qvec, q, args.top_k)
            ms = (time.perf_counter() - t0) * 1000
            granks = _gold_ranks([h.text for h in hits], golds)
            recs.append({"label": label, "rank": min((r for r in granks if r is not None), default=None),
                         "gold_ranks": granks, "embed_ms": 0.0, "search_ms": ms, "rerank_ms": 0.0, "total_ms": ms})
            # Separate call, not hits[:5]: the candidate pool depends on top_k, so the ranking can differ
            winners, _ = retrieve.consolidate(retrieve.hybrid_search(db, doc_id, qvec, q, settings.DEFAULT_TOP_K))
            ctx = [_norm(p.text) for p in retrieve.fetch_parent_texts(db, winners)]
            in_ctx.append(any(_norm(g) in t for g in golds for t in ctx))

    m = _metrics(recs) | {"context_gold_rate": sum(in_ctx) / len(in_ctx)}
    print(f"{len(recs)} queries, top-k {args.top_k}, search p50 {m['latency_search_p50_ms']:.0f} ms")
    for r, ok in zip(recs, in_ctx):
        if r["rank"] is None or not ok:
            print(f"  miss: {r['label']['query']} (rank {r['rank'] or '>' + str(args.top_k)}, in context: {ok})")
    failed = []
    for key in [k for k in m if k.startswith(("pass_", "recall_", "mrr", "context_"))]:
        floor = GATES.get(key)
        bad = floor is not None and m[key] < floor
        failed += [key] if bad else []
        print(f"{key:<20}{m[key]:>8.3f}" + (f"   gate >= {floor:.3f}" + ("   FAIL" if bad else "") if floor is not None else ""))
    if failed:
        sys.exit(f"retrieval gate failed: {', '.join(failed)}")
    print("retrieval gate passed")


if __name__ == "__main__":
    main()
