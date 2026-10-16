"""Groundedness / citation eval — does the summary stick to what retrieval handed it?

Runs the app's real answer path per labelled query (hybrid search -> consolidate ->
parent texts -> summarize) and has a stronger LLM judge split each answer into claims
and check every claim (and its page citation) against the excerpts the answer model saw.

Usage (from backend/, venv active, OPENAI_API_KEY in .env):

    python scripts/eval_grounding.py --pdf ../AMD_2022_10K.pdf \
        --labels scripts/eval_labels/amd_2022_10k.json

Metrics, mean and std over --reps (the summary runs at temperature 0.2, so it varies):
  groundedness        supported claims / claims, averaged over answers that make claims
  citation_precision  cited claims whose cited page's excerpt supports them / cited claims
  citation_coverage   claims carrying a page citation / claims
  fake_page_rate      answers citing a page outside every excerpt's page span (regex, no LLM)
  abstention_rate     no-answer queries where the answer says the document doesn't cover it
  false_refusal_rate  answerable queries where it says that anyway
  context_gold_rate   answerable queries whose gold passage was in the excerpts (retrieval, no LLM)
  refusal_despite_context_rate  of those, the share it still refused (a model failure,
                      unlike a refusal after a retrieval miss)
  gold_hit_rate       answerable queries whose answer contains every number in the gold
                      passage (or its exact text when it has none): a rough correctness proxy

Logs one MLflow run (experiment grounding-<labels stem>) with per-rep metric steps, a
per-query table and grounding_report.md (every unsupported claim, wrong citation, fake
page and abstention miss). The judge is an LLM: spot-check it against a human before
trusting the numbers.
"""
import argparse
import asyncio
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from app.config import settings
from app.models.db import SessionLocal, init_db
from app.services import retrieve, summarize

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling import also works when loaded by tests
from eval_retrieval import _cleanup, _gold_list, _ingest_doc, _mlflow_experiment, _norm, _prepare  # noqa: E402

JUDGE_PROMPT = """You check whether an ANSWER is grounded in the source EXCERPTS it was written from.

Split the ANSWER into atomic factual claims about the document's content: one fact each,
including the "Key Takeaway" line. Skip filler.

For each claim return:
- "claim": the claim, short
- "kind": "meta" if it is about what the excerpts or document contain, cover or lack
  (e.g. "The excerpts do not state the incorporation date"); "fact" otherwise
- "supported": true only if the excerpts state it or directly imply it (numbers, dates and
  names must match); false otherwise
- "cited_pages": the page numbers the answer cites for this claim (empty list if none)
- "citation_correct": null if cited_pages is empty; otherwise true when an excerpt whose page
  span includes one of the cited pages supports the claim, false if not

Also return "abstained": true if the answer's main message is that the document does not
contain the requested information, else false.

Respond with JSON only: {"claims": [...], "abstained": true|false}"""

# "page 47", "pages 50–51 and 60", "p. 9", "pp. 3-4"
# ponytail: "(page 47, 2022)" also collects 2022; tighten if fake_page_rate flags it
_PAGE_RE = re.compile(r"\bp(?:ages?|p?\.)\s*(\d+(?:\s*(?:,|–|-|and|&)\s*\d+)*)", re.IGNORECASE)
_NO_CONTEXT = "No relevant context was found in this document."  # same fallback as routes/query.py


def _answer_inputs(db, doc_id: str, label: dict, top_k: int) -> list[dict]:
    """The excerpts the app would summarize for this query (same steps as routes/query.py)."""
    q = label["query"]
    hits = retrieve.hybrid_search(db, doc_id, retrieve.embed_query(q), q, top_k)
    winners, _ = retrieve.consolidate(hits, settings.PARENT_CONSOLIDATION_THRESHOLD)
    return [{"text": p.text, "page_start": p.page_start, "page_end": p.page_end or p.page_start}
            for p in retrieve.fetch_parent_texts(db, winners)]


def _summarize(query: str, ex: list[dict]) -> str:
    if not ex:
        return _NO_CONTEXT
    return "".join(summarize.summarize_stream(query, [e["text"] for e in ex], [e["page_start"] for e in ex]))


def _span(e: dict) -> str:
    a, b = e["page_start"], e["page_end"]
    return "no page" if a is None else f"page {a}" if a == b else f"pages {a}–{b}"


def _judge(query: str, ex: list[dict], answer: str, model: str) -> tuple[list[dict], bool]:
    ctx = "\n\n".join(f"### Excerpt {i} ({_span(e)})\n{e['text']}" for i, e in enumerate(ex, 1))
    resp = summarize._get_client().chat.completions.create(
        model=model, temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": JUDGE_PROMPT},
                  {"role": "user", "content": f"QUESTION:\n{query}\n\nEXCERPTS:\n{ctx}\n\nANSWER:\n{answer}"}],
    )
    out = json.loads(resp.choices[0].message.content)
    return out.get("claims", []), bool(out.get("abstained"))


def _cited_pages(answer: str) -> list[int]:
    return sorted({int(n) for m in _PAGE_RE.findall(answer) for n in re.findall(r"\d+", m)})


def _fake_pages(cited: list[int], ex: list[dict]) -> list[int]:
    """Cited pages that fall outside every excerpt's page span: the model never saw them."""
    spans = [(e["page_start"], e["page_end"]) for e in ex if e["page_start"] is not None]
    return [p for p in cited if not any(a <= p <= b for a, b in spans)]


def _gold_hit(gold: str, answer: str) -> bool:
    # ponytail: number matching misses "23.6 billion" written as "$23,600 million"; judge it if that matters
    nums = re.findall(r"\d[\d,.]*\d|\d", gold)
    return all(n in answer for n in nums) if nums else _norm(gold) in _norm(answer)


def _grade(label: dict, rep: int, ex: list[dict], answer: str, claims: list[dict], abstained: bool) -> dict:
    # Only factual claims are scored; "the excerpts don't say X" is covered by `abstained`.
    claims = [c for c in claims if c.get("kind") != "meta"]
    return {
        "label": label, "rep": rep, "answer": answer, "claims": claims, "abstained": abstained,
        "pages": [_span(e) for e in ex],
        "fake_pages": _fake_pages(_cited_pages(answer), ex),
        "gold_hit": "gold" in label and any(_gold_hit(g, answer) for g in _gold_list(label)),
        "gold_in_context": "gold" in label and any(_norm(g) in _norm(e["text"]) for e in ex for g in _gold_list(label)),
    }


def _rate(xs) -> float:
    xs = list(xs)
    return sum(xs) / (len(xs) or 1)


def _metrics(recs: list[dict]) -> dict[str, float]:
    """Flat metric dict for one rep (keys double as MLflow metric names)."""
    answerable = [r for r in recs if "gold" in r["label"]]
    no_answer = [r for r in recs if "gold" not in r["label"]]
    claims = [c for r in recs for c in r["claims"]]
    cited = [c for c in claims if c.get("cited_pages")]
    return {
        "groundedness": _rate(_rate(c.get("supported") is True for c in r["claims"]) for r in recs if r["claims"]),
        "citation_precision": _rate(c.get("citation_correct") is True for c in cited),
        "citation_coverage": len(cited) / (len(claims) or 1),
        "fake_page_rate": _rate(bool(r["fake_pages"]) for r in recs),
        "abstention_rate": _rate(r["abstained"] for r in no_answer),
        "false_refusal_rate": _rate(r["abstained"] for r in answerable),
        "context_gold_rate": _rate(r["gold_in_context"] for r in answerable),
        "refusal_despite_context_rate": _rate(r["abstained"] for r in answerable if r["gold_in_context"]),
        "gold_hit_rate": _rate(r["gold_hit"] for r in answerable),
        "claims_per_answer": len(claims) / (len(recs) or 1),
    }


def _cell(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def _report(title: str, summ: dict, recs: list[dict], params: dict) -> str:
    names = [k[:-5] for k in summ if k.endswith("_mean")]
    out = [f"# Grounding — {title}", "",
           " · ".join(f"{k}: {v}" for k, v in params.items()), "",
           "## Metrics (mean ± std over reps)", "", "| metric | mean | ± std |", "|---|---:|---:|"]
    out += [f"| {n} | {summ[n + '_mean']:.3f} | {summ[n + '_std']:.3f} |" for n in names]

    out += ["", "## By tag (all reps pooled)", "",
            "| tag | answers | groundedness | citation precision | abstained |", "|---|---:|---:|---:|---:|"]
    for tag in sorted({r["label"].get("tag", "untagged") for r in recs}):
        rs = [r for r in recs if r["label"].get("tag", "untagged") == tag]
        m, cited = _metrics(rs), any(c.get("cited_pages") for r in rs for c in r["claims"])
        g = f"{m['groundedness']:.3f}" if any(r["claims"] for r in rs) else "—"  # — = no factual claims
        cp = f"{m['citation_precision']:.3f}" if cited else "—"
        out.append(f"| {tag} | {len(rs)} | {g} | {cp} | {_rate(r['abstained'] for r in rs):.0%} |")

    def section(head, cols, rows):
        out.extend(["", f"## {head} ({len(rows)})", "", "| " + " | ".join(cols) + " |",
                    "|" + "---|" * len(cols)] + ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows])

    section("Unsupported claims", ["rep", "query", "claim"],
            [(r["rep"], r["label"]["query"], c.get("claim")) for r in recs for c in r["claims"]
             if c.get("supported") is not True])
    section("Wrong citations", ["rep", "query", "claim", "cited", "excerpt pages"],
            [(r["rep"], r["label"]["query"], c.get("claim"), c.get("cited_pages"), "; ".join(r["pages"]))
             for r in recs for c in r["claims"] if c.get("citation_correct") is False])
    section("Fake pages (cited but never shown)", ["rep", "query", "fake pages", "excerpt pages"],
            [(r["rep"], r["label"]["query"], r["fake_pages"], "; ".join(r["pages"])) for r in recs if r["fake_pages"]])
    section("No-answer queries it answered anyway", ["rep", "query", "answer"],
            [(r["rep"], r["label"]["query"], r["answer"][:300]) for r in recs
             if "gold" not in r["label"] and not r["abstained"]])
    section("Answerable queries it refused", ["rep", "query", "gold in excerpts", "answer"],
            [(r["rep"], r["label"]["query"], r["gold_in_context"], r["answer"][:300]) for r in recs
             if "gold" in r["label"] and r["abstained"]])
    return "\n".join(out) + "\n"


def _log_mlflow(args, params: dict, summ: dict, per_rep: list[dict], recs: list[dict], report: str) -> None:
    import mlflow

    _mlflow_experiment(args.experiment)
    with mlflow.start_run(run_name=f"{args.pdf.stem} grounding") as run:
        mlflow.log_params(params)
        mlflow.log_metrics(summ)
        for k, m in enumerate(per_rep):
            mlflow.log_metrics(m, step=k)
        mlflow.log_table({
            "rep": [r["rep"] for r in recs],
            "query": [r["label"]["query"] for r in recs],
            "tag": [r["label"].get("tag", "untagged") for r in recs],
            "answer": [r["answer"] for r in recs],
            "excerpt_pages": ["; ".join(r["pages"]) for r in recs],
            "n_claims": [len(r["claims"]) for r in recs],
            "n_supported": [sum(c.get("supported") is True for c in r["claims"]) for r in recs],
            "claims": [json.dumps(r["claims"]) for r in recs],
            "abstained": [r["abstained"] for r in recs],
            "gold_in_context": [r["gold_in_context"] for r in recs],
            "fake_pages": [str(r["fake_pages"]) for r in recs],
        }, artifact_file="per_query.json")
        # MLflow caps tag values at 8000 chars; the full report is always in the artifact.
        mlflow.set_tag("mlflow.note.content", report if len(report) <= 8000
                       else report[:7900] + "\n\n…truncated, see grounding_report.md")
        mlflow.log_text(report, "grounding_report.md")
    print(f"\nlogged to MLflow: experiment '{args.experiment}', run {run.info.run_id}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True, type=Path)
    ap.add_argument("--labels", required=True, type=Path)
    ap.add_argument("--top-k", type=int, default=5, help="child hits per query (app default: 5)")
    ap.add_argument("--reps", type=int, default=3, help="summary + judge passes per query")
    ap.add_argument("--judge-model", default="gpt-4.1")
    ap.add_argument("--workers", type=int, default=4, help="concurrent summary/judge calls")
    ap.add_argument("--limit", type=int, help="only the first N labels (smoke test)")
    ap.add_argument("--experiment", help="MLflow experiment name (default: grounding-<labels file stem>)")
    args = ap.parse_args()
    args.experiment = args.experiment or f"grounding-{args.labels.stem}"

    init_db()
    labels = json.loads(args.labels.read_text())[: args.limit]
    print(f"{len(labels)} queries x {args.reps} reps; top_k={args.top_k}, answer={settings.CHAT_MODEL}, "
          f"judge={args.judge_model}, contextual={settings.CONTEXTUAL_EMBEDDINGS}")

    # Retrieval is deterministic, so it runs once; only summary + judge repeat per rep.
    doc_id = _ingest_doc(args.pdf)
    try:
        t0 = time.perf_counter()
        asyncio.run(_prepare(doc_id))
        prep_s = time.perf_counter() - t0
        with SessionLocal() as db:
            excerpts = [_answer_inputs(db, doc_id, lab, args.top_k) for lab in labels]
    finally:
        _cleanup(doc_id, args.pdf.suffix)
    print(f"prepared in {prep_s:.0f}s; summarizing + judging…")

    def run(job: tuple[int, int]) -> dict:
        rep, i = job
        q, ex = labels[i]["query"], excerpts[i]
        answer = _summarize(q, ex)
        return _grade(labels[i], rep, ex, answer, *_judge(q, ex, answer, args.judge_model))

    with ThreadPoolExecutor(args.workers) as pool:
        recs = list(pool.map(run, [(rep, i) for rep in range(args.reps) for i in range(len(labels))]))

    per_rep = [_metrics([r for r in recs if r["rep"] == k]) for k in range(args.reps)]
    summ = {}
    for key in per_rep[0]:
        vals = [m[key] for m in per_rep]
        summ |= {f"{key}_mean": float(np.mean(vals)), f"{key}_std": float(np.std(vals))}
    summ["prepare_seconds"] = prep_s

    print("\n" + "=" * 78)
    print(f"{'metric':<30}{'mean':>8}{'± std':>8}" + "".join(f"{'rep ' + str(k):>8}" for k in range(args.reps)))
    print("-" * 78)
    for key in per_rep[0]:
        print(f"{key:<30}{summ[key + '_mean']:>8.3f}{summ[key + '_std']:>8.3f}"
              + "".join(f"{m[key]:>8.3f}" for m in per_rep))

    params = {"pdf": args.pdf.name, "labels": args.labels.name, "n_queries": len(labels), "reps": args.reps,
              "top_k": args.top_k, "answer_model": settings.CHAT_MODEL, "judge_model": args.judge_model,
              "contextual_embeddings": settings.CONTEXTUAL_EMBEDDINGS, "embed_model": settings.EMBED_MODEL,
              "parent_tokens": settings.PARENT_TOKENS, "child_tokens": settings.CHILD_TOKENS}
    report = _report(args.pdf.stem, summ, recs, params)
    print("\n" + report.split("## By tag")[1].split("## Unsupported")[0].strip())
    _log_mlflow(args, params, summ, per_rep, recs, report)


if __name__ == "__main__":
    main()
