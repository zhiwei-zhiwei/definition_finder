"""Freeze a prepared document + its labelled query vectors into an offline retrieval fixture.

scripts/eval_smoke.py replays retrieval on this fixture with no Docling and no OpenAI calls,
so CI can gate on Pass@k for free and get the same answer every run. Re-freeze (and re-set
the gates in eval_smoke.py) when chunking, the embedding model or the labels change.

Usage (from backend/, venv active, OPENAI_API_KEY in .env; ~3 min, < $0.05):

    python scripts/freeze_retrieval_fixture.py --pdf ../AMD_2022_10K.pdf \
        --labels scripts/eval_labels/amd_2022_10k.json

Vectors are stored as float16 (half the size; cosine ranks barely move) next to the chunk
texts, parent page spans and the scored labels, in one compressed .npz.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

import numpy as np

from app.config import settings
from app.models.db import SessionLocal, init_db
from app.models.schema import ChildChunk, ParentChunk
from app.services import vectorstore
from app.services.embed import embed_batch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_retrieval import _cleanup, _ingest_doc, _prepare  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "retrieval_amd.npz"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True, type=Path)
    ap.add_argument("--labels", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=FIXTURE)
    args = ap.parse_args()

    # The gate measures the default pipeline: no LLM-written context (it varies run to run), no reranker.
    settings.CONTEXTUAL_EMBEDDINGS, settings.RERANK_MODEL = False, ""
    init_db()
    labels = [lab for lab in json.loads(args.labels.read_text()) if "gold" in lab]
    doc_id = _ingest_doc(args.pdf)
    try:
        asyncio.run(_prepare(doc_id))
        with SessionLocal() as db:
            parents = db.query(ParentChunk).filter(ParentChunk.doc_id == doc_id).order_by(ParentChunk.id).all()
            children = db.query(ChildChunk).filter(ChildChunk.doc_id == doc_id).order_by(ChildChunk.id).all()
            meta = {
                "pdf": args.pdf.name, "labels": labels, "embed_model": settings.EMBED_MODEL,
                "embed_dim": settings.EMBED_DIM, "parent_tokens": settings.PARENT_TOKENS,
                "child_tokens": settings.CHILD_TOKENS,
                "parents": [{"id": p.id, "text": p.text, "page_start": p.page_start, "page_end": p.page_end}
                            for p in parents],
                "children": [{"id": c.id, "parent_id": c.parent_id, "text": c.text} for c in children],
            }
            child_vecs = np.stack([vectorstore.unpack(c.embedding) for c in children])
    finally:
        _cleanup(doc_id, args.pdf.suffix)
    query_vecs = np.asarray(embed_batch([lab["query"] for lab in labels]))

    np.savez_compressed(
        args.out, meta=np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8),
        child_vecs=child_vecs.astype(np.float16), query_vecs=query_vecs.astype(np.float16))
    print(f"wrote {args.out} ({args.out.stat().st_size / 1e6:.1f} MB): {len(parents)} parents, "
          f"{len(children)} children, {len(labels)} queries")


if __name__ == "__main__":
    main()
