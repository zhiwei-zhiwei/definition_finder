"""Freeze answers from a past grounding run into offline regression fixtures.

Each case keeps the real answer, the judge's claims and the excerpts that answer was
written from, trimmed to the paragraphs that matter, plus the failures the current
scorer gives it. tests/test_eval_grounding.py re-scores every case, so a scorer
change that flips a known verdict fails pytest. No API calls: excerpts are rebuilt with
Docling + the chunker and matched to the run's logged page spans.

Usage (from backend/, venv active):

    python scripts/freeze_regressions.py --pdf ../AMD_2022_10K.pdf \
        --labels scripts/eval_labels/amd_2022_10k.json \
        --case "<RUN_ID>:0:How much did net revenue grow compared with 2021?" --note "why it matters"

--case and --note repeat and pair up in order (Docling runs once per call, ~3 min).

Check the printed failures before committing: they become the expected verdict.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from app.services import chunker, ingest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_grounding as g  # noqa: E402
from eval_retrieval import _mlflow_experiment, _norm  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "grounding_regressions.json"


def _spans(pages: str) -> list[tuple[int, int]]:
    """'page 43; pages 47–49' -> [(43, 43), (47, 49)]"""
    return [(int(m[0]), int(m[1] or m[0])) for m in re.findall(r"(\d+)(?:–(\d+))?", pages)]


def _trim(text: str, answer: str, golds: list[str]) -> str:
    """Paragraphs stating a number the answer states, or a gold passage; keeps "(In millions)"
    headers so table numbers still scale."""
    nums = g._numbers(answer)
    keep = [p for p in text.split("\n\n") if re.search(r"in millions", p, re.IGNORECASE)
            or any(_norm(x) in _norm(p) for x in golds) or any(g._has_number(n, p) for n in nums)]
    return "\n\n".join(keep)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True, type=Path)
    ap.add_argument("--labels", required=True, type=Path)
    ap.add_argument("--case", action="append", required=True, help='"RUN_ID:REP:QUERY" from a grounding run')
    ap.add_argument("--note", action="append", default=[], help="why the matching --case is worth keeping")
    ap.add_argument("--experiment", default="grounding-amd-10k")
    args = ap.parse_args()

    import mlflow

    _mlflow_experiment(args.experiment)
    rows = {}
    for run in {c.split(":", 1)[0] for c in args.case}:
        t = mlflow.artifacts.load_dict(f"runs:/{run}/per_query.json")
        rows |= {(run, r["rep"], r["query"]): r for r in (dict(zip(t["columns"], row)) for row in t["data"])}
    labels = {lab["query"]: lab for lab in json.loads(args.labels.read_text())}
    parents, _ = chunker.chunk_document("f" * 32, ingest.extract(args.pdf).items)

    cases = json.loads(FIXTURES.read_text()) if FIXTURES.exists() else []
    for i, spec in enumerate(args.case):
        run, rep, query = spec.split(":", 2)
        row, label = rows[(run, int(rep), query)], labels[query]
        golds = g._gold_list(label) if "gold" in label else []
        full = [{"text": p.text, "page_start": p.page_start, "page_end": p.page_end or p.page_start}
                for a, b in _spans(row["excerpt_pages"]) for p in parents if (p.page_start, p.page_end) == (a, b)]
        ex = [{**e, "text": _trim(e["text"], row["answer"], golds)} for e in full]
        grade = lambda e: g._grade(label, int(rep), e, row["answer"], json.loads(row["claims"]), row["abstained"])  # noqa: E731
        failures = grade(ex)["failures"]
        if failures != grade(full)["failures"]:
            sys.exit(f"trimming changed the verdict for {spec!r}; not frozen")
        if len(full) != len(_spans(row["excerpt_pages"])):
            print(f"  note: {len(full)} parents matched {row['excerpt_pages']!r} (shared page spans)")
        cases = [c for c in cases if (c["run"], c["rep"], c["label"]["query"]) != (run, int(rep), query)]
        cases.append({"note": args.note[i] if i < len(args.note) else "", "run": run, "rep": int(rep), "label": label, "excerpts": ex,
                      "answer": row["answer"], "claims": json.loads(row["claims"]),
                      "abstained": row["abstained"], "failures": failures})
        print(f"{failures or ['(clean)']}  <- {run[:8]} rep {rep}: {query}  (logged: {row.get('failures', 'n/a')!r})")
    FIXTURES.parent.mkdir(exist_ok=True)
    FIXTURES.write_text(json.dumps(cases, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(cases)} cases in {FIXTURES}")


if __name__ == "__main__":
    main()
