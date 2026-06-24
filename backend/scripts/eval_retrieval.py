"""Retrieval eval harness — measure Pass@k with vs without contextual embeddings.

Runs the *real* pipeline (Docling + OpenAI) against a PDF and a small labelled
query set, preparing the document once per mode and scoring whether each query's
gold passage shows up in the top-k retrieved snippets.

Usage (from backend/, venv active, OPENAI_API_KEY in .env):

    python scripts/eval_retrieval.py \
        --pdf "/path/to/doc.pdf" \
        --labels scripts/eval_labels/streeter_lease.json \
        --top-k 20

Labels file: JSON list of {"query": str, "gold": str | [str, ...]}. A query
"passes@k" when any gold substring appears (case-insensitive, whitespace-
collapsed) in one of the top-k snippet texts. Keep gold phrases short and
distinctive so they fall inside a single child chunk.
"""
import argparse
import asyncio
import json
import re
import shutil
import uuid
from pathlib import Path

from app.config import settings
from app.models.db import SessionLocal, init_db
from app.models.schema import Document
from app.services import prepare, retrieve

_KS = (1, 3, 5, 10, 20)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _gold_list(label: dict) -> list[str]:
    g = label["gold"]
    return [g] if isinstance(g, str) else list(g)


def _ingest_doc(pdf: Path) -> str:
    doc_id = uuid.uuid4().hex
    dest = settings.upload_path / f"{doc_id}{pdf.suffix}"
    shutil.copy(pdf, dest)
    with SessionLocal() as db:
        db.add(Document(id=doc_id, filename=pdf.name, mime="application/pdf",
                        size_bytes=pdf.stat().st_size, status="ready"))
        db.commit()
    return doc_id


async def _prepare(doc_id: str) -> None:
    async for _ in prepare.prepare_document_stream(doc_id):
        pass


def _first_match_rank(snippet_texts: list[str], golds: list[str]) -> int | None:
    """1-based rank of the first snippet containing any gold substring, else None."""
    norm_golds = [_norm(g) for g in golds]
    for i, t in enumerate(snippet_texts):
        nt = _norm(t)
        if any(g in nt for g in norm_golds):
            return i + 1
    return None


def _evaluate(doc_id: str, labels: list[dict], top_k: int) -> list[tuple[dict, int | None]]:
    out: list[tuple[dict, int | None]] = []
    with SessionLocal() as db:
        for label in labels:
            q = label["query"]
            qvec = retrieve.embed_query(q)
            hits = retrieve.hybrid_search(db, doc_id, qvec, q, top_k)
            rank = _first_match_rank([h.text for h in hits], _gold_list(label))
            out.append((label, rank))
    return out


def _passk(ranks: list[int | None]) -> dict[int, float]:
    n = len(ranks) or 1
    return {k: sum(1 for r in ranks if r is not None and r <= k) / n for k in _KS}


def _cleanup(doc_id: str, pdf_suffix: str) -> None:
    (settings.upload_path / f"{doc_id}{pdf_suffix}").unlink(missing_ok=True)
    (settings.cache_path / f"{doc_id}.html").unlink(missing_ok=True)
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        if doc is not None:
            db.delete(doc)  # cascade removes parent/child chunks + embeddings
            db.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True, type=Path)
    ap.add_argument("--labels", required=True, type=Path)
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--keep", action="store_true", help="keep the prepared doc (last mode) instead of deleting it")
    args = ap.parse_args()

    init_db()
    labels = json.loads(args.labels.read_text())
    print(f"loaded {len(labels)} labelled queries; top_k={args.top_k}")

    modes = [("baseline", False), ("contextual", True)]
    per_mode_ranks: dict[str, list[int | None]] = {}
    per_mode_detail: dict[str, list[tuple[dict, int | None]]] = {}

    doc_id = _ingest_doc(args.pdf)
    try:
        for name, flag in modes:
            settings.CONTEXTUAL_EMBEDDINGS = flag
            print(f"\n=== mode: {name} (CONTEXTUAL_EMBEDDINGS={flag}) — preparing… ===")
            asyncio.run(_prepare(doc_id))
            detail = _evaluate(doc_id, labels, args.top_k)
            per_mode_detail[name] = detail
            per_mode_ranks[name] = [r for _, r in detail]
    finally:
        if not args.keep:
            _cleanup(doc_id, args.pdf.suffix)

    # Per-query rank comparison (both detail lists follow the same label order).
    print("\n" + "=" * 78)
    print(f"{'query':<46}{'baseline':>14}{'contextual':>14}")
    print("-" * 78)
    for (label, rb), (_, rc) in zip(per_mode_detail["baseline"], per_mode_detail["contextual"]):
        q = label["query"]
        sb = str(rb) if rb is not None else "miss"
        sc = str(rc) if rc is not None else "miss"
        print(f"{q[:44]:<46}{sb:>14}{sc:>14}")

    # Pass@k summary.
    pb, pc = _passk(per_mode_ranks["baseline"]), _passk(per_mode_ranks["contextual"])
    print("\n" + "=" * 78)
    print(f"{'Pass@k':<10}{'baseline':>14}{'contextual':>14}{'delta':>12}")
    print("-" * 78)
    for k in _KS:
        d = pc[k] - pb[k]
        print(f"@{k:<9}{pb[k]*100:>13.1f}%{pc[k]*100:>13.1f}%{d*100:>+11.1f}%")
    print("\n(rank = position of the first snippet containing the gold passage; "
          "'miss' = not in top-k)")


if __name__ == "__main__":
    main()
