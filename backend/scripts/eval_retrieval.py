"""Retrieval eval harness — measure Pass@k with vs without contextual embeddings.

Runs the *real* pipeline (Docling + OpenAI) against a PDF and a small labelled
query set, preparing the document once per mode and scoring whether each query's
gold passage shows up in the top-k retrieved snippets.

Usage (from backend/, venv active, OPENAI_API_KEY in .env):

    python scripts/eval_retrieval.py \
        --pdf "/path/to/doc.pdf" \
        --labels scripts/eval_labels/streeter_lease.json \
        --top-k 20

Add --baseline-only to skip the contextual pass (the expensive, rate-limit-prone half).

Labels file: JSON list of {"query": str, "gold": str | [str, ...], "tag": str}.
A query "passes@k" when any gold substring appears (case-insensitive, whitespace-
collapsed) in one of the top-k snippet texts. Keep gold phrases short and
distinctive so they fall inside a single child chunk. "tag" is optional and only
groups the per-tag Pass@k table. A label with no "gold" (tag "no-answer": the
document says nothing about it) is skipped here: retrieval always returns top-k,
so Pass@k can't score abstention -- that needs the summary step.
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
from app.models.schema import ChildChunk, Document
from app.services import context, prepare, retrieve

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
            if "gold" not in label:
                continue
            q = label["query"]
            qvec = retrieve.embed_query(q)
            hits = retrieve.hybrid_search(db, doc_id, qvec, q, top_k)
            rank = _first_match_rank([h.text for h in hits], _gold_list(label))
            out.append((label, rank))
    return out


def _passk(ranks: list[int | None]) -> dict[int, float]:
    n = len(ranks) or 1
    return {k: sum(1 for r in ranks if r is not None and r <= k) / n for k in _KS}


def _empty_context(doc_id: str) -> tuple[int, int]:
    """(children with no situating context, total children). In contextual mode any
    empty one means a context call failed (e.g. 429), so the run isn't a clean test."""
    with SessionLocal() as db:
        rows = db.query(ChildChunk.context_text).filter(ChildChunk.doc_id == doc_id).all()
    return sum(1 for (c,) in rows if not c), len(rows)


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
    ap.add_argument("--baseline-only", action="store_true", help="skip the contextual pass (no per-parent LLM calls)")
    args = ap.parse_args()

    init_db()
    labels = json.loads(args.labels.read_text())
    skipped = sum(1 for lab in labels if "gold" not in lab)
    print(f"loaded {len(labels)} labelled queries ({skipped} no-answer skipped); top_k={args.top_k}")

    modes = [("baseline", False)] + ([] if args.baseline_only else [("contextual", True)])
    names = [n for n, _ in modes]
    per_mode_ranks: dict[str, list[int | None]] = {}
    per_mode_detail: dict[str, list[tuple[dict, int | None]]] = {}

    # Every context call ships the whole doc (~100k tokens), so the default 3 workers
    # burst past OpenAI's tokens-per-minute limit on a long filing and some calls give
    # up. One worker is slower but stays under it.
    context._MAX_WORKERS = 1
    invalid: dict[str, str] = {}

    doc_id = _ingest_doc(args.pdf)
    try:
        for name, flag in modes:
            settings.CONTEXTUAL_EMBEDDINGS = flag
            print(f"\n=== mode: {name} (CONTEXTUAL_EMBEDDINGS={flag}) — preparing… ===")
            asyncio.run(_prepare(doc_id))
            if flag:
                empty, total = _empty_context(doc_id)
                if empty:
                    invalid[name] = f"{empty}/{total} chunks have no context (context calls failed)"
            detail = _evaluate(doc_id, labels, args.top_k)
            per_mode_detail[name] = detail
            per_mode_ranks[name] = [r for _, r in detail]
    finally:
        if not args.keep:
            _cleanup(doc_id, args.pdf.suffix)

    # Per-query rank comparison (all detail lists follow the same label order).
    print("\n" + "=" * 78)
    print(f"{'query':<46}" + "".join(f"{n:>14}" for n in names))
    print("-" * 78)
    for i, (label, _) in enumerate(per_mode_detail[names[0]]):
        ranks = [per_mode_detail[n][i][1] for n in names]
        print(f"{label['query'][:44]:<46}" + "".join(f"{r if r is not None else 'miss':>14}" for r in ranks))

    # Pass@k summary (delta column only when two modes ran).
    pk = {n: _passk(per_mode_ranks[n]) for n in names}
    print("\n" + "=" * 78)
    print(f"{'Pass@k':<10}" + "".join(f"{n:>14}" for n in names) + (f"{'delta':>12}" if len(names) == 2 else ""))
    print("-" * 78)
    for k in _KS:
        row = f"@{k:<9}" + "".join(f"{pk[n][k] * 100:>13.1f}%" for n in names)
        if len(names) == 2:
            row += f"{(pk[names[1]][k] - pk[names[0]][k]) * 100:>+11.1f}%"
        print(row)
    # Pass@k per tag (labels without a tag land in "untagged").
    tags = sorted({lab.get("tag", "untagged") for lab, _ in per_mode_detail["baseline"]})
    if tags != ["untagged"]:
        print("\n" + "=" * 78)
        print(f"{'Pass@k by tag':<28}" + "".join(f"{'@' + str(k):>10}" for k in _KS))
        print("-" * 78)
        for tag in tags:
            for name in names:
                rs = [r for lab, r in per_mode_detail[name] if lab.get("tag", "untagged") == tag]
                pk = _passk(rs)
                print(f"{f'{tag} (n={len(rs)}) {name[:4]}':<28}" + "".join(f"{pk[k] * 100:>9.1f}%" for k in _KS))
    print("\n(rank = position of the first snippet containing the gold passage; "
          "'miss' = not in top-k)")
    for name, why in invalid.items():
        print(f"\n!! INVALID RUN: {name}: {why}. Its column is not a clean measurement; re-run.")
    if invalid:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
