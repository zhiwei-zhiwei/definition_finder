"""Groundedness / citation eval — does the summary stick to what retrieval handed it?

Runs every labelled query through the app's own query graph (services/query_graph.py:
retrieve -> consolidate -> evidence gate -> summarize -> verify, exactly what POST /query
runs) and has a stronger LLM judge split each answer into claims and check every claim (and
its page citation) against the excerpts the answer model saw.

Usage (from backend/, venv active, OPENAI_API_KEY in .env):

    python scripts/eval_grounding.py --pdf ../AMD_2022_10K.pdf \
        --labels scripts/eval_labels/amd_2022_10k.json

Metrics, mean and std over --reps (the summary runs at temperature 0.2, so it varies):
  groundedness        supported claims / claims, averaged over answers that make claims
  citation_precision  cited claims citing a page the judge located the support on / cited claims
  citation_coverage   claims carrying a page citation / claims
  fake_page_rate      answers citing a page outside every excerpt's page span (regex, no LLM)
  abstention_rate     no-answer queries where the answer says the document doesn't cover it
  false_refusal_rate  answerable queries where it says that anyway
  context_gold_rate   answerable queries whose gold passage was in the excerpts (retrieval, no LLM)
  refusal_despite_context_rate  of those, the share it still refused (a model failure,
                      unlike a refusal after a retrieval miss)
  number_exact_rate   numeric queries whose answer states every key number (label
                      "key_numbers", else the gold's numbers; rounding to the gold's precision ok)
  unsourced_number_rate  answers stating a number no excerpt contains (computed or invented)
  gold_hit_rate       answerable queries with an exact number match, or the gold's exact
                      text when it has no numbers: a rough correctness proxy
  failure_rate        answers with any failure in _FAILURES; fail_<name>_rate is the share
                      whose first (primary) failure is <name>
  gate_rate           answers the evidence gate gave without calling the summarizer
  gate_false_refusal_rate  answerable queries the gate refused
  verify_flag_rate    answers the app's verify step flagged for review (needs_review)
  verify_recall       answers with any failure that verify flagged: how much the app's own
                      check catches. unsourced_number and fake_page failures use verify's
                      functions, so they count as caught by construction; the report's
                      taxonomy table shows recall per failure kind
  verify_precision    flagged answers that have a failure (a flag without one is a false
                      alarm, or a failure the judge missed)

Regression gate: --only scripts/eval_labels/amd_regressions.txt runs just the queries that
failed before, and each --require "metric>=x" / "metric<=x" exits 1 if a mean misses it
(the file's header holds the current gate command).

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
import uuid
from pathlib import Path

import numpy as np

from app.config import settings
from app.models.db import init_db
from app.services import query_graph, summarize
from app.services import verify as verify_service
from app.services.verify import cited_pages as _cited_pages
from app.services.verify import fake_pages as _fake_pages
from app.services.verify import has_number as _has_number
from app.services.verify import numbers as _numbers
from app.services.verify import unsourced_numbers as _unsourced_numbers

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
- "support_pages": the page number(s) where the supporting text actually sits (empty list if
  unsupported). Excerpts mark page breaks with [page N]: text belongs to the nearest [page N]
  marker before it. Find the page from the markers; ignore what the answer cites.

Also return "abstained": true if the answer's main message is that the document does not
contain the requested information, else false.

Respond with JSON only: {"claims": [...], "abstained": true|false}"""

async def _run_graph(doc_id: str, query: str, top_k: int, answer: bool = True) -> dict:
    """One query through the app's query graph; its final state (or the state where it paused
    for review). answer=False stops before the summarizer, for re-judging stored answers."""
    qid = uuid.uuid4().hex
    config = {"configurable": {"thread_id": qid}}
    graph = query_graph.graph  # module default: in-memory checkpoints, nothing written to disk
    try:
        await graph.ainvoke({"query_id": qid, "doc_id": doc_id, "query": query, "top_k": top_k, "user_id": None},
                            config, durability="exit", interrupt_before=None if answer else ["summarize"])
        return (await graph.aget_state(config)).values
    finally:
        await graph.checkpointer.adelete_thread(qid)


def _excerpts(state: dict) -> list[dict]:
    # Single-page parents store page_end NULL; the report's page spans want both ends.
    return [{**e, "page_end": e["page_end"] or e["page_start"]} for e in state["excerpts"]]


def _span(e: dict) -> str:
    a, b = e["page_start"], e["page_end"]
    return "no page" if a is None else f"page {a}" if a == b else f"pages {a}–{b}"


def _judge(query: str, ex: list[dict], answer: str, model: str) -> tuple[list[dict], bool]:
    ctx = "\n\n".join(f"### Excerpt {i} ({_span(e)})\n{e.get('marked', e['text'])}" for i, e in enumerate(ex, 1))
    resp = summarize._get_client().chat.completions.create(
        model=model, temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": JUDGE_PROMPT},
                  {"role": "user", "content": f"QUESTION:\n{query}\n\nEXCERPTS:\n{ctx}\n\nANSWER:\n{answer}"}],
    )
    out = json.loads(resp.choices[0].message.content)
    return out.get("claims", []), bool(out.get("abstained"))


def _key_numbers(label: dict) -> list[tuple]:
    if "key_numbers" in label:
        return [n for k in label["key_numbers"] for n in _numbers(k)]
    return _numbers(_gold_list(label)[0]) if "gold" in label else []


def _gold_hit(label: dict, answer: str) -> bool:
    if keys := _key_numbers(label):
        return all(_has_number(k, answer) for k in keys)
    return any(_norm(g) in _norm(answer) for g in _gold_list(label))


# Checked in order; a record's first match is its primary failure. Model fabrication comes
# first (fix it in the prompt whatever retrieval did), then retrieval, then answer quality.
# Non-numeric answers that miss the gold text are not failures: that check misses paraphrases.
_FAILURES = [
    ("unsourced_number", lambda r: bool(r["unsourced_numbers"])),
    ("unsupported_claim", lambda r: any(c.get("supported") is not True for c in r["claims"])),
    ("answered_no_answer", lambda r: "gold" not in r["label"] and not r["abstained"]),
    ("retrieval_miss", lambda r: "gold" in r["label"] and not r["gold_in_context"]
     and (r["abstained"] or r["numeric"] and not r["gold_hit"])),
    ("refused_with_context", lambda r: r["abstained"] and r["gold_in_context"]),
    ("wrong_number", lambda r: r["numeric"] and not r["gold_hit"] and not r["abstained"]),
    ("fake_page", lambda r: bool(r["fake_pages"])),
    ("wrong_page", lambda r: any(c.get("citation_correct") is False for c in r["claims"])),
]


def _grade(label: dict, rep: int, ex: list[dict], answer: str, claims: list[dict], abstained: bool,
           verification: dict | None = None) -> dict:
    """verification: what the graph's verify (or evidence gate) said; recomputed with verify.check
    when absent (re-judged or frozen answers never ran through the graph)."""
    verification = verification or verify_service.check(answer, ex)
    # Only factual claims are scored; "the excerpts don't say X" is covered by `abstained`.
    claims = [c for c in claims if c.get("kind") != "meta"]
    # The judge locates support but rubber-stamps citations when asked to verify them
    # (it passed every page a human flagged), so the page comparison is done here.
    # The judge sometimes writes pages as "page 11"; keep the digits.
    pages = lambda xs: {int(n) for x in xs or [] for n in re.findall(r"\d+", str(x))}  # noqa: E731
    for c in claims:
        cited = pages(c.get("cited_pages"))
        c["citation_correct"] = bool(cited & pages(c.get("support_pages"))) if cited else None
    rec = {
        "label": label, "rep": rep, "answer": answer, "claims": claims, "abstained": abstained,
        "pages": [_span(e) for e in ex],
        "fake_pages": _fake_pages(_cited_pages(answer), ex),
        "numeric": bool(_key_numbers(label)),
        "gold_hit": "gold" in label and _gold_hit(label, answer),
        "unsourced_numbers": [] if abstained else _unsourced_numbers(answer, ex),
        "gold_in_context": "gold" in label and any(_norm(g) in _norm(e["text"]) for e in ex for g in _gold_list(label)),
        "verify_status": verification["status"],
        "flagged": verification["needs_review"],
        "gated": any(i["kind"] == "low_evidence" for i in verification["issues"]),
    }
    rec["failures"] = [name for name, hit in _FAILURES if hit(rec)]
    return rec


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
        "number_exact_rate": _rate(r["gold_hit"] for r in answerable if r["numeric"]),
        "unsourced_number_rate": _rate(bool(r["unsourced_numbers"]) for r in recs),
        "gold_hit_rate": _rate(r["gold_hit"] for r in answerable),
        "claims_per_answer": len(claims) / (len(recs) or 1),
        "failure_rate": _rate(bool(r["failures"]) for r in recs),
        "gate_rate": _rate(r["gated"] for r in recs),
        "gate_false_refusal_rate": _rate(r["gated"] for r in answerable),
        "verify_flag_rate": _rate(r["flagged"] for r in recs),
        "verify_recall": _rate(r["flagged"] for r in recs if r["failures"]),
        "verify_precision": _rate(bool(r["failures"]) for r in recs if r["flagged"]),
    } | {f"fail_{name}_rate": _rate(r["failures"][:1] == [name] for r in recs) for name, _ in _FAILURES}


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

    out += ["", "## Failure taxonomy (all reps pooled)", "",
            "flagged = of the answers whose primary failure is this kind, how many the app's verify step flagged.", "",
            "| failure | primary | any | flagged | first example |", "|---|---:|---:|---:|---|"]
    for name, _ in _FAILURES:
        prim = [r for r in recs if r["failures"][:1] == [name]]
        out.append(f"| {name} | {len(prim)} | {sum(name in r['failures'] for r in recs)} | "
                   f"{sum(r['flagged'] for r in prim)} | {_cell(prim[0]['label']['query']) if prim else ''} |")
    out.append(f"| (none) | {sum(not r['failures'] for r in recs)} | | {sum(r['flagged'] for r in recs if not r['failures'])} | |")

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
    section("Numeric queries missing a key number", ["rep", "query", "key numbers", "answer"],
            [(r["rep"], r["label"]["query"], r["label"].get("key_numbers") or r["label"]["gold"], r["answer"][:300])
             for r in recs if r["numeric"] and not r["gold_hit"] and not r["abstained"]])
    section("Numbers no excerpt states", ["rep", "query", "numbers", "answer"],
            [(r["rep"], r["label"]["query"], r["unsourced_numbers"], r["answer"][:300])
             for r in recs if r["unsourced_numbers"]])
    section("No-answer queries it answered anyway", ["rep", "query", "answer"],
            [(r["rep"], r["label"]["query"], r["answer"][:300]) for r in recs
             if "gold" not in r["label"] and not r["abstained"]])
    section("Answerable queries it refused", ["rep", "query", "gold in excerpts", "answer"],
            [(r["rep"], r["label"]["query"], r["gold_in_context"], r["answer"][:300]) for r in recs
             if "gold" in r["label"] and r["abstained"]])
    section("Flagged by verify with no failure", ["rep", "query", "verify status", "answer"],
            [(r["rep"], r["label"]["query"], r["verify_status"], r["answer"][:300]) for r in recs
             if r["flagged"] and not r["failures"]])
    return "\n".join(out) + "\n"


def _log_mlflow(args, params: dict, summ: dict, per_rep: list[dict], recs: list[dict], report: str) -> None:
    import mlflow

    _mlflow_experiment(args.experiment)
    with mlflow.start_run(run_name=f"{args.pdf.stem} grounding" + (" (rejudged)" if args.rejudge else "")) as run:
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
            "gold_hit": [r["gold_hit"] for r in recs],
            "unsourced_numbers": [str(r["unsourced_numbers"]) for r in recs],
            "failures": [", ".join(r["failures"]) for r in recs],
            "verify_status": [r["verify_status"] for r in recs],
            "gated": [r["gated"] for r in recs],
        }, artifact_file="per_query.json")
        # MLflow caps tag values at 8000 chars; the full report is always in the artifact.
        mlflow.set_tag("mlflow.note.content", report if len(report) <= 8000
                       else report[:7900] + "\n\n…truncated, see grounding_report.md")
        mlflow.log_text(report, "grounding_report.md")
    print(f"\nlogged to MLflow: experiment '{args.experiment}', run {run.info.run_id}")


async def _run_all(args, labels: list[dict], doc_id: str, old: dict) -> list[dict]:
    """Every (rep, label) through the graph, then the judge; --workers at a time."""
    sem = asyncio.Semaphore(args.workers)

    async def one(rep: int, lab: dict) -> dict:
        q = lab["query"]
        async with sem:
            state = await _run_graph(doc_id, q, args.top_k, answer=not old)
            answer = old[(rep, q)] if old else state["answer"]
            ex = _excerpts(state)
            claims, abstained = await asyncio.to_thread(_judge, q, ex, answer, args.judge_model)
        # A re-judged answer is not the one the graph would verify; _grade re-checks it.
        return _grade(lab, rep, ex, answer, claims, abstained, None if old else state["verification"])

    return await asyncio.gather(*(one(rep, lab) for rep in range(args.reps) for lab in labels))


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
    ap.add_argument("--only", type=Path, help="file of queries (one per line, # comments) to keep from --labels")
    ap.add_argument("--require", action="append", default=[], metavar="METRIC>=X",
                    help='gate on a mean metric, e.g. "number_exact_rate>=0.9" or "failure_rate<=0.2"; repeatable')
    ap.add_argument("--rejudge", metavar="RUN_ID", help="judge the answers stored in an earlier run "
                    "instead of generating new ones (score old answers with the current judge)")
    args = ap.parse_args()
    args.experiment = args.experiment or f"grounding-{args.labels.stem}"

    init_db()
    gates = [re.fullmatch(r"\s*(\w+)\s*(>=|<=)\s*([\d.]+)\s*", r) for r in args.require]
    if not all(gates) or {m[1] for m in gates} - set(_metrics([])):
        ap.error(f"--require wants METRIC>=X or METRIC<=X with a metric from _metrics, got {args.require}")
    labels = json.loads(args.labels.read_text())
    if args.only:
        want = [q.strip() for q in args.only.read_text().splitlines() if q.strip() and not q.startswith("#")]
        if missing := set(want) - {lab["query"] for lab in labels}:
            ap.error(f"--only queries not in {args.labels.name}: {sorted(missing)}")
        labels = [lab for lab in labels if lab["query"] in want]
    labels = labels[: args.limit]
    print(f"{len(labels)} queries x {args.reps} reps; top_k={args.top_k}, answer={settings.CHAT_MODEL}, "
          f"judge={args.judge_model}, contextual={settings.CONTEXTUAL_EMBEDDINGS}")

    old = {}
    if args.rejudge:
        import mlflow

        _mlflow_experiment(args.experiment)
        t = mlflow.artifacts.load_dict(f"runs:/{args.rejudge}/per_query.json")
        old = {(r[0], r[1]): r[2] for r in ([row[t["columns"].index(c)] for c in ("rep", "query", "answer")]
                                            for row in t["data"])}
        args.reps = 1 + max(rep for rep, _ in old)

    doc_id = _ingest_doc(args.pdf)
    try:
        t0 = time.perf_counter()
        asyncio.run(_prepare(doc_id))
        prep_s = time.perf_counter() - t0
        print(f"prepared in {prep_s:.0f}s; answering + judging…")
        recs = asyncio.run(_run_all(args, labels, doc_id, old))
    finally:
        _cleanup(doc_id, args.pdf.suffix)

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
              "parent_tokens": settings.PARENT_TOKENS, "child_tokens": settings.CHILD_TOKENS,
              "rejudged_run": args.rejudge or "", "only": args.only.name if args.only else "",
              "rerank_model": settings.RERANK_MODEL or "off"}
    report = _report(args.pdf.stem, summ, recs, params)
    print("\n" + report.split("## By tag")[1].split("## Unsupported")[0].strip())
    _log_mlflow(args, params, summ, per_rep, recs, report)

    failed = [f"{m[1]} = {summ[m[1] + '_mean']:.3f}, needs {m[2]} {m[3]}" for m in gates
              if not (summ[m[1] + "_mean"] >= float(m[3]) if m[2] == ">=" else summ[m[1] + "_mean"] <= float(m[3]))]
    if failed:
        sys.exit("REGRESSION:\n  " + "\n  ".join(failed))
    if gates:
        print(f"all {len(gates)} gates pass")


if __name__ == "__main__":
    main()
