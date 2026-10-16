"""Retrieval eval harness — measure retrieval quality with vs without contextual embeddings.

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
groups the per-tag table. A label with no "gold" (tag "no-answer": the
document says nothing about it) is skipped here: retrieval always returns top-k,
so Pass@k can't score abstention -- that needs the summary step.

Metrics per mode (all over the scored queries):
  pass_at_k    share of queries with any gold passage in the top k (hit rate)
  recall_at_k  share of each query's gold passages found in the top k, averaged;
               equals pass_at_k for single-gold queries
  mrr          mean of 1/rank of the first gold hit (a miss beyond top-k scores 0)
  latency_*    p50/p95 wall time (ms) of the query embed call, the hybrid search,
               and their sum
  prepare_seconds  wall time of the whole prepare (Docling + context + embed)

Every run is logged to MLflow: one parent run per invocation (shared params +
contextual-minus-baseline deltas + a comparison report in the run description and
as comparison_report.md / pass_at_k.png) with one nested run per mode (metrics +
per-query table). Store: data/mlflow/ unless MLFLOW_TRACKING_URI is set. View:

    mlflow ui --backend-store-uri sqlite:///../data/mlflow/mlflow.db --port 5055  # 5000 = macOS AirPlay
"""
import argparse
import asyncio
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

import numpy as np

from app.config import settings
from app.models.db import SessionLocal, init_db
from app.models.schema import ChildChunk, Document
from app.services import context, prepare, retrieve

_KS = (1, 3, 5, 10, 20)
_MLFLOW_DIR = Path(__file__).resolve().parents[2] / "data" / "mlflow"


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


def _gold_ranks(snippet_texts: list[str], golds: list[str]) -> list[int | None]:
    """Per gold substring: 1-based rank of the first snippet containing it, else None."""
    norm_snips = [_norm(t) for t in snippet_texts]
    return [next((i + 1 for i, t in enumerate(norm_snips) if _norm(g) in t), None) for g in golds]


def _first_match_rank(snippet_texts: list[str], golds: list[str]) -> int | None:
    """1-based rank of the first snippet containing any gold substring, else None."""
    return min((r for r in _gold_ranks(snippet_texts, golds) if r is not None), default=None)


def _evaluate(doc_id: str, labels: list[dict], top_k: int) -> list[dict]:
    """One record per scored label: gold ranks plus embed/search wall time in ms."""
    out: list[dict] = []
    with SessionLocal() as db:
        for label in labels:
            if "gold" not in label:
                continue
            q = label["query"]
            t0 = time.perf_counter()
            qvec = retrieve.embed_query(q)
            t1 = time.perf_counter()
            hits = retrieve.hybrid_search(db, doc_id, qvec, q, top_k)
            t2 = time.perf_counter()
            granks = _gold_ranks([h.text for h in hits], _gold_list(label))
            out.append({
                "label": label,
                "rank": min((r for r in granks if r is not None), default=None),
                "gold_ranks": granks,
                "embed_ms": (t1 - t0) * 1000,
                "search_ms": (t2 - t1) * 1000,
                "total_ms": (t2 - t0) * 1000,
            })
    return out


def _passk(ranks: list[int | None]) -> dict[int, float]:
    n = len(ranks) or 1
    return {k: sum(1 for r in ranks if r is not None and r <= k) / n for k in _KS}


def _recallk(gold_ranks: list[list[int | None]]) -> dict[int, float]:
    """Share of each query's gold passages in the top k, averaged over queries."""
    n = len(gold_ranks) or 1
    return {k: sum(sum(1 for r in gr if r is not None and r <= k) / len(gr) for gr in gold_ranks) / n
            for k in _KS}


def _mrr(ranks: list[int | None]) -> float:
    """Mean reciprocal rank of the first hit; a miss (beyond top-k) scores 0."""
    return sum(1 / r for r in ranks if r is not None) / (len(ranks) or 1)


def _metrics(recs: list[dict]) -> dict[str, float]:
    """Flat metric dict for one mode (keys double as MLflow metric names)."""
    ranks = [r["rank"] for r in recs]
    m = {f"pass_at_{k}": v for k, v in _passk(ranks).items()}
    m |= {f"recall_at_{k}": v for k, v in _recallk([r["gold_ranks"] for r in recs]).items()}
    m["mrr"] = _mrr(ranks)
    for part in ("embed", "search", "total"):
        ms = [r[f"{part}_ms"] for r in recs]
        # ponytail: p95 over ~40 queries is roughly the 2nd-slowest; needs more queries to be stable
        m[f"latency_{part}_p50_ms"] = float(np.percentile(ms, 50)) if ms else 0.0
        m[f"latency_{part}_p95_ms"] = float(np.percentile(ms, 95)) if ms else 0.0
    return m


def _fmt(key: str, v: float, signed: bool = False) -> str:
    sign = "+" if signed else ""
    if key.startswith(("pass_", "recall_")):
        return f"{v * 100:{sign}.1f}%"
    if key == "mrr":
        return f"{v:{sign}.3f}"
    return f"{v:{sign}.1f}"  # latency ms / seconds


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


def _comparison_report(title: str, names: list[str], summ: dict, prep_s: dict, detail: dict) -> str:
    """Markdown A/B report: metric table, per-tag view, and every query whose rank moved."""
    a, b = names
    rows = {n: summ[n] | {"prepare_seconds": prep_s[n]} for n in names}

    def table(keys):
        return f"| metric | {a} | {b} | delta |\n|---|---:|---:|---:|\n" + "\n".join(
            f"| {k} | {_fmt(k, rows[a][k])} | {_fmt(k, rows[b][k])} | {_fmt(k, rows[b][k] - rows[a][k], signed=True)} |"
            for k in keys)

    def show(r):
        return "miss" if r is None else str(r)

    n = len(detail[a])
    out = [f"# {a} vs {b} — {title}", "",
           f"{n} scored queries (one query = {100 / (n or 1):.1f}%). Rank = position of the first "
           "snippet containing the gold passage; miss = not in top-k.", "",
           "## Retrieval quality", "", table([k for k in rows[a] if k.startswith(("pass_", "recall_", "mrr"))]),
           "", "## Speed and cost (latency in ms, prepare in seconds)", "",
           table([k for k in rows[a] if k.startswith(("latency_", "prepare_"))]),
           "", "## By tag", "", "| tag | n | Pass@1 | Pass@5 | Pass@20 | MRR |\n|---|---:|---|---|---|---|"]
    for tag in sorted({r["label"].get("tag", "untagged") for r in detail[a]}):
        ra, rb = ([r["rank"] for r in detail[m] if r["label"].get("tag", "untagged") == tag] for m in names)
        pa, pb = _passk(ra), _passk(rb)
        out.append(f"| {tag} | {len(ra)} | " + " | ".join(f"{pa[k] * 100:.1f}% → {pb[k] * 100:.1f}%" for k in (1, 5, 20))
                   + f" | {_mrr(ra):.3f} → {_mrr(rb):.3f} |")

    def worse(r):
        return float("inf") if r is None else r

    moved = [(x["label"]["query"], x["rank"], y["rank"]) for x, y in zip(detail[a], detail[b]) if x["rank"] != y["rank"]]
    up = sum(1 for _, x, y in moved if worse(y) < worse(x))
    out += ["", f"## Per-query rank changes ({b} better: {up}, worse: {len(moved) - up}, "
                f"unchanged: {n - len(moved)})", "", f"| query | {a} | {b} |\n|---|---:|---:|"]
    out += [f"| {q} | {show(x)} | {show(y)} |" for q, x, y in moved]
    return "\n".join(out) + "\n"


def _passk_figure(names: list[str], summ: dict):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6, 3.5))
    for n in names:
        ax.plot(_KS, [summ[n][f"pass_at_{k}"] * 100 for k in _KS], marker="o", label=f"{n} (MRR {summ[n]['mrr']:.3f})")
    ax.set_xscale("log")
    ax.set_xticks(_KS, [str(k) for k in _KS])
    ax.set(xlabel="k", ylabel="Pass@k (%)", ylim=(0, 100), title="Pass@k by mode")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    return fig


def _log_report(title: str, names: list[str], summ: dict, prep_s: dict, detail: dict) -> None:
    """Attach the A/B report to the active MLflow run: description (rendered) + artifacts."""
    import mlflow

    report = _comparison_report(title, names, summ, prep_s, detail)
    # MLflow caps tag values at 8000 chars; the full report is always in the artifact.
    mlflow.set_tag("mlflow.note.content", report if len(report) <= 8000
                   else report[:7900] + "\n\n…truncated, see comparison_report.md")
    mlflow.log_text(report, "comparison_report.md")
    mlflow.log_figure(_passk_figure(names, summ), "pass_at_k.png")


def _log_mlflow(args, modes, summ, per_mode_detail, prep_s, invalid, n_skipped) -> None:
    import mlflow  # lazy: heavy import, only needed once a real run has finished

    _MLFLOW_DIR.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", f"sqlite:///{_MLFLOW_DIR / 'mlflow.db'}"))
    if mlflow.get_experiment_by_name(args.experiment) is None:
        mlflow.create_experiment(args.experiment, artifact_location=(_MLFLOW_DIR / "artifacts").as_uri())
    mlflow.set_experiment(args.experiment)

    names = [n for n, _ in modes]
    common = {
        "pdf": args.pdf.name, "labels": args.labels.name, "top_k": args.top_k,
        "n_scored": len(per_mode_detail[names[0]]), "n_no_answer_skipped": n_skipped,
        "embed_model": settings.EMBED_MODEL, "embed_dim": settings.EMBED_DIM,
        "context_model": settings.CONTEXT_MODEL, "parent_tokens": settings.PARENT_TOKENS,
        "child_tokens": settings.CHILD_TOKENS, "context_workers": context._MAX_WORKERS,
    }
    with mlflow.start_run(run_name=f"{args.pdf.stem} {'A/B' if len(names) == 2 else names[0]}") as parent:
        mlflow.log_params(common | {"modes": ",".join(names)})
        mlflow.set_tag("valid", str(not invalid))
        if len(names) == 2:
            a, b = summ[names[0]], summ[names[1]]
            mlflow.log_metrics({f"delta_{k}": b[k] - a[k] for k in a}
                               | {"delta_prepare_seconds": prep_s[names[1]] - prep_s[names[0]]})
            _log_report(args.pdf.stem, names, summ, prep_s, per_mode_detail)
        for name, flag in modes:
            recs = per_mode_detail[name]
            tag_m = {}
            for tag in sorted({r["label"].get("tag", "untagged") for r in recs}):
                rs = [r["rank"] for r in recs if r["label"].get("tag", "untagged") == tag]
                tag_m |= {f"tag_{tag}_pass_at_{k}": v for k, v in _passk(rs).items()}
                tag_m[f"tag_{tag}_mrr"] = _mrr(rs)
            with mlflow.start_run(run_name=name, nested=True):
                mlflow.log_params(common | {"mode": name, "contextual_embeddings": flag})
                mlflow.set_tag("valid", str(name not in invalid))
                if name in invalid:
                    mlflow.set_tag("invalid_reason", invalid[name])
                mlflow.log_metrics(summ[name] | tag_m | {"prepare_seconds": prep_s[name]})
                mlflow.log_table({
                    "query": [r["label"]["query"] for r in recs],
                    "tag": [r["label"].get("tag", "untagged") for r in recs],
                    "rank": [r["rank"] if r["rank"] is not None else -1 for r in recs],  # -1 = miss
                    "embed_ms": [round(r["embed_ms"], 1) for r in recs],
                    "search_ms": [round(r["search_ms"], 1) for r in recs],
                }, artifact_file="per_query.json")
                if len(names) == 2:  # same A/B report on each mode, so it's there whichever run is opened
                    _log_report(args.pdf.stem, names, summ, prep_s, per_mode_detail)
    print(f"\nlogged to MLflow: experiment '{args.experiment}', run {parent.info.run_id} "
          f"({mlflow.get_tracking_uri()})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True, type=Path)
    ap.add_argument("--labels", required=True, type=Path)
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--keep", action="store_true", help="keep the prepared doc (last mode) instead of deleting it")
    ap.add_argument("--baseline-only", action="store_true", help="skip the contextual pass (no per-parent LLM calls)")
    ap.add_argument("--experiment", help="MLflow experiment name (default: eval-<labels file stem>)")
    args = ap.parse_args()
    args.experiment = args.experiment or f"eval-{args.labels.stem}"

    init_db()
    labels = json.loads(args.labels.read_text())
    skipped = sum(1 for lab in labels if "gold" not in lab)
    print(f"loaded {len(labels)} labelled queries ({skipped} no-answer skipped); top_k={args.top_k}")

    modes = [("baseline", False)] + ([] if args.baseline_only else [("contextual", True)])
    names = [n for n, _ in modes]
    per_mode_detail: dict[str, list[dict]] = {}
    prep_s: dict[str, float] = {}

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
            t0 = time.perf_counter()
            asyncio.run(_prepare(doc_id))
            prep_s[name] = time.perf_counter() - t0
            if flag:
                empty, total = _empty_context(doc_id)
                if empty:
                    invalid[name] = f"{empty}/{total} chunks have no context (context calls failed)"
            per_mode_detail[name] = _evaluate(doc_id, labels, args.top_k)
    finally:
        if not args.keep:
            _cleanup(doc_id, args.pdf.suffix)

    # Per-query rank comparison (all detail lists follow the same label order).
    print("\n" + "=" * 78)
    print(f"{'query':<46}" + "".join(f"{n:>14}" for n in names))
    print("-" * 78)
    for i, rec in enumerate(per_mode_detail[names[0]]):
        ranks = [per_mode_detail[n][i]["rank"] for n in names]
        print(f"{rec['label']['query'][:44]:<46}" + "".join(f"{r if r is not None else 'miss':>14}" for r in ranks))

    # Metric summary (delta column only when two modes ran).
    summ = {n: _metrics(per_mode_detail[n]) for n in names}
    summ_rows = {n: summ[n] | {"prepare_seconds": prep_s[n]} for n in names}
    print("\n" + "=" * 78)
    print(f"{'metric':<26}" + "".join(f"{n:>14}" for n in names) + (f"{'delta':>12}" if len(names) == 2 else ""))
    print("-" * 78)
    for key in summ_rows[names[0]]:
        row = f"{key:<26}" + "".join(f"{_fmt(key, summ_rows[n][key]):>14}" for n in names)
        if len(names) == 2:
            row += f"{_fmt(key, summ_rows[names[1]][key] - summ_rows[names[0]][key], signed=True):>12}"
        print(row)
    # Pass@k + MRR per tag (labels without a tag land in "untagged").
    tags = sorted({r["label"].get("tag", "untagged") for r in per_mode_detail["baseline"]})
    if tags != ["untagged"]:
        print("\n" + "=" * 78)
        print(f"{'Pass@k by tag':<28}" + "".join(f"{'@' + str(k):>8}" for k in _KS) + f"{'MRR':>8}")
        print("-" * 78)
        for tag in tags:
            for name in names:
                rs = [r["rank"] for r in per_mode_detail[name] if r["label"].get("tag", "untagged") == tag]
                pk = _passk(rs)
                print(f"{f'{tag} (n={len(rs)}) {name[:4]}':<28}" + "".join(f"{pk[k] * 100:>7.1f}%" for k in _KS)
                      + f"{_mrr(rs):>8.3f}")
    print("\n(rank = position of the first snippet containing the gold passage; "
          "'miss' = not in top-k; latency in ms, prepare in seconds)")
    for name, why in invalid.items():
        print(f"\n!! INVALID RUN: {name}: {why}. Its column is not a clean measurement; re-run.")

    _log_mlflow(args, modes, summ, per_mode_detail, prep_s, invalid, skipped)
    if invalid:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
