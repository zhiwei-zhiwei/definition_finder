"""Groundedness / citation eval — does the summary stick to what retrieval handed it?

Runs the app's real answer path per labelled query (hybrid search -> consolidate ->
parent texts -> summarize) and has a stronger LLM judge split each answer into claims
and check every claim (and its page citation) against the excerpts the answer model saw.

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
from concurrent.futures import ThreadPoolExecutor
from decimal import ROUND_HALF_UP, Decimal
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
- "support_pages": the page number(s) where the supporting text actually sits (empty list if
  unsupported). Excerpts mark page breaks with [page N]: text belongs to the nearest [page N]
  marker before it. Find the page from the markers; ignore what the answer cites.

Also return "abstained": true if the answer's main message is that the document does not
contain the requested information, else false.

Respond with JSON only: {"claims": [...], "abstained": true|false}"""

# "page 47", "pages 50–51 and 60", "pages 1, 5, and 41", "p. 9", "pp. 3-4"
# ponytail: "(page 47, 2022)" also collects 2022; tighten if fake_page_rate flags it
_PAGE_RE = re.compile(r"\bp(?:ages?|p?\.)\s*(\d+(?:\s*(?:,\s*(?:and|&)?|–|-|and|&)\s*\d+)*)", re.IGNORECASE)
_LIST_MARK_RE = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)  # "2. Expanding data center…"
_NO_CONTEXT = "No relevant context was found in this document."  # same fallback as routes/query.py


def _answer_inputs(db, doc_id: str, label: dict, top_k: int) -> list[dict]:
    """The excerpts the app would summarize for this query (same steps as routes/query.py)."""
    q = label["query"]
    hits = retrieve.hybrid_search(db, doc_id, retrieve.embed_query(q), q, top_k)
    winners, _ = retrieve.consolidate(hits, settings.PARENT_CONSOLIDATION_THRESHOLD)
    return [{"text": p.text, "marked": p.marked_text or p.text,
             "page_start": p.page_start, "page_end": p.page_end or p.page_start}
            for p in retrieve.fetch_parent_texts(db, winners)]


def _summarize(query: str, ex: list[dict]) -> str:
    if not ex:
        return _NO_CONTEXT
    return "".join(summarize.summarize_stream(query, [e["marked"] for e in ex], [e["page_start"] for e in ex]))


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


def _cited_pages(answer: str) -> list[int]:
    return sorted({int(n) for m in _PAGE_RE.findall(answer) for n in re.findall(r"\d+", m)})


def _fake_pages(cited: list[int], ex: list[dict]) -> list[int]:
    """Cited pages that fall outside every excerpt's page span: the model never saw them."""
    spans = [(e["page_start"], e["page_end"]) for e in ex if e["page_start"] is not None]
    return [p for p in cited if not any(a <= p <= b for a, b in spans)]


# "25,000", "$23.6 billion", "16%", "$5B"; not the 300 in "MI300" or the 1 in "Q1"
_NUM_RE = re.compile(r"(?<![\w.])(\d+(?:,\d{3})*(?:\.\d+)?)(?!\d)"
                     r"(?:\s*(%|percent\b|thousand\b|million\b|billion\b)|([BM])\b)?", re.IGNORECASE)
_SCALE = {"thousand": 3, "million": 6, "m": 6, "billion": 9, "b": 9}


def _numbers(text: str) -> list[tuple[Decimal, Decimal, bool, str]]:
    """(value, precision, is_percent, as written) per number; page citations and list markers
    are not numbers here."""
    out = []
    for m in _NUM_RE.finditer(_LIST_MARK_RE.sub(" ", _PAGE_RE.sub(" ", text))):
        unit = (m[2] or m[3] or "").lower()
        d, k = Decimal(m[1].replace(",", "")), _SCALE.get(unit, 0)
        out.append((d.scaleb(k), Decimal(1).scaleb(d.as_tuple().exponent + k), unit in ("%", "percent"), m[0]))
    return out


def _has_number(want: tuple, text: str) -> bool:
    """`text` states `want`, possibly more precisely: $23,601 million counts for $23.6 billion.
    In text saying "in millions" (10-K tables), a bare 23,601 also counts as 23,601 million."""
    v, q, pct = want[:3]
    have = [(h, p) for h, _, p, _ in _numbers(text)]
    if re.search(r"in millions", text, re.IGNORECASE):  # ponytail: per text, not per table
        have += [(h.scaleb(6), p) for h, p in have if not p]
    return any(p == pct and (h / q).to_integral_value(ROUND_HALF_UP) * q == v for h, p in have)


def _key_numbers(label: dict) -> list[tuple]:
    if "key_numbers" in label:
        return [n for k in label["key_numbers"] for n in _numbers(k)]
    return _numbers(_gold_list(label)[0]) if "gold" in label else []


def _unsourced_numbers(answer: str, ex: list[dict]) -> list[str]:
    """Numbers the answer states that no excerpt states (to the answer's precision)."""
    src = "\n".join(e["text"] for e in ex)
    return [n[3].strip() for n in _numbers(answer) if not _has_number(n, src)]


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


def _grade(label: dict, rep: int, ex: list[dict], answer: str, claims: list[dict], abstained: bool) -> dict:
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
            "| failure | primary | any | first example |", "|---|---:|---:|---|"]
    for name, _ in _FAILURES:
        prim = [r for r in recs if r["failures"][:1] == [name]]
        out.append(f"| {name} | {len(prim)} | {sum(name in r['failures'] for r in recs)} | "
                   f"{_cell(prim[0]['label']['query']) if prim else ''} |")
    out.append(f"| (none) | {sum(not r['failures'] for r in recs)} | | |")

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

    old = {}
    if args.rejudge:
        import mlflow

        _mlflow_experiment(args.experiment)
        t = mlflow.artifacts.load_dict(f"runs:/{args.rejudge}/per_query.json")
        old = {(r[0], r[1]): r[2] for r in ([row[t["columns"].index(c)] for c in ("rep", "query", "answer")]
                                            for row in t["data"])}
        args.reps = 1 + max(rep for rep, _ in old)

    def run(job: tuple[int, int]) -> dict:
        rep, i = job
        q, ex = labels[i]["query"], excerpts[i]
        answer = old[(rep, q)] if old else _summarize(q, ex)
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
