# Eval metrics notes — what was added, term by term

Date: 2026-10-15. Run: AMD FY2022 10-K, 37 scored queries (7 no-answer skipped), top-k 20.
All code is in `backend/scripts/eval_retrieval.py` unless noted.

---

## 1. Recall@k

**Meaning.** Out of a query's gold passages, the share that shows up in the top k. Averaged over queries.

**How it differs from Pass@k.** Pass@k asks "was _any_ gold passage found?" Recall@k asks "_how many_ of them were found?" For a query with one gold passage they're identical.

**What changed.**

- New `_gold_ranks()` (line 85) gives the rank of _each_ gold string. The old code only kept the first hit.
- `_first_match_rank()` now reuses it, so the old behaviour is the same.
- New `_recallk()` (line 126).

**Result.** One query has two gold phrasings ("gross margin 45% vs 48%"). Baseline found one of them at rank 1, so that query scores 0.5. That's why baseline **Recall@1 = 50.0%** but **Pass@1 = 51.4%**. At every other k they match.

## 2. MRR (Mean Reciprocal Rank)

**Meaning.** Score each query 1 ÷ (rank of the first correct hit), then average. Rank 1 scores 1.0, rank 2 scores 0.5, rank 10 scores 0.1, and a miss scores 0. One number that rewards putting the answer _near the top_.

**What changed.** New `_mrr()` (line 133). It's added to the summary table and to the per-tag table as an "MRR" column.

**Result.** **0.605 → 0.637 (+0.031)** with contextual. Biggest gain on `ambiguous` (0.278 → 0.395). `numeric` dipped (0.676 → 0.629).

## 3. p50 / p95 latency

**Meaning.** Sort the per-query times. p50 is the middle one (a typical query). p95 is the time that 95% of queries beat (the slow tail).

**What changed.**

- `_evaluate()` times every query with `time.perf_counter()` (lines 104–108), split into the **embed** call (OpenAI network), the **search** (`hybrid_search`, local), and the **total**.
- p50 and p95 come from `np.percentile` (lines 147–148). numpy was already a dependency.
- Separately, each mode's whole **prepare** is timed (line 259).

**Result.**

|            | baseline | contextual |
| ---------- | -------- | ---------- |
| search p50 | 39 ms    | 74 ms      |
| total p50  | 290 ms   | 327 ms     |
| prepare    | 117 s    | 244 s      |

- Search got slower because BM25 now tokenizes context + chunk text, which is longer.
- The embed time (~255 ms) is the same model and the same query. Its p95 difference (531 vs 364 ms) is network noise, not a real effect.
- Prepare doubles because of the context LLM calls. The harness forces 1 worker; the app uses 3, so it's faster there.
- Caveat: with 37 queries, p95 is roughly the 2nd-slowest query, so it's unstable. It's marked with a `ponytail:` comment in the code.

## 4. A/B rerun

**Meaning.** Run the same queries on the same document twice, changing only one thing (here `CONTEXTUAL_EMBEDDINGS` off/on), and compare. "Rerun" means repeating it to check whether the result holds.

**What changed.** No new A/B code. The harness already ran both modes. I re-ran it with the new metrics. Each mode's prepare time is now recorded so the cost side is compared too.

**Result.** This is the most important finding:

- **Baseline reproduced exactly**, because retrieval is deterministic.
- **Contextual did not.** The LLM writes slightly different blurbs every run.

| Pass@k | baseline | contextual, last run | contextual, this run |
| ------ | -------- | -------------------- | -------------------- |
| @1     | 51.4%    | 51.4%                | 54.1%                |
| @3     | 64.9%    | 75.7%                | 70.3%                |
| @5     | 78.4%    | 81.1%                | 75.7%                |
| @10    | 81.1%    | 86.5%                | 86.5%                |
| @20    | 83.8%    | 91.9%                | 89.2%                |

- Run-to-run noise on the contextual arm (up to ±5.4%) is **as big as the gain itself**.
- The _direction_ held in both runs: better @20, better MRR, mostly on `ambiguous`. The _size_ isn't pinned down.
- To pin it down you'd need several contextual reruns averaged, or many more queries.

## 5. MLflow tracking

**Meaning.** A tool that stores every eval run: its **params** (settings), **metrics** (numbers) and **artifacts** (files). A web UI then lets you compare runs side by side instead of pasting tables into markdown.

**What changed.**

- `backend/pyproject.toml`: `mlflow>=3.0` added to the `dev` extras and installed (3.17.1). Nothing existing was downgraded.
- New `_log_mlflow()` (line 179), called at the end of every run, including baseline-only and invalid runs. Invalid runs get the tags `valid=False` and `invalid_reason`.
  - **Parent run** (e.g. "AMD_2022_10K A/B"): shared params (pdf, labels, top_k, models, chunk sizes, worker count) plus `delta_*` metrics (contextual − baseline).
  - **One nested run per mode** (`baseline`, `contextual`): every metric above, per-tag `tag_<tag>_pass_at_k` / `tag_<tag>_mrr`, `prepare_seconds`, and a `per_query.json` table (query, tag, rank, embed_ms, search_ms; rank −1 = miss).
- Store: `data/mlflow/` (SQLite DB + artifacts). It's already git-ignored via `data/`. Override it with `MLFLOW_TRACKING_URI`.
- New `--experiment` flag (line 234); the default is `eval-<labels file stem>`. This run used `--experiment eval-amd-10k`.
- The `mlflow` import is lazy, so the tests and the script's startup don't pay for it.

**Verified.** The real run logged 3 runs (parent + 2 modes), all `valid=True`. `mlflow ui` served them (HTTP 200), and the API returned the same numbers as the console.

**How to view.**

```bash
cd backend && source .venv/bin/activate
mlflow ui --backend-store-uri sqlite:///../data/mlflow/mlflow.db --port 5055   # keep running
# open http://127.0.0.1:5055 → experiment "eval-amd-10k" (not 5000: macOS AirPlay Receiver owns that port)
# tick the "baseline" and "contextual" runs → Compare
```


## 6. Comparison report in MLflow

**Meaning.** A one-page baseline-vs-contextual summary attached to the A/B parent run, so you don't have to tick runs and build the comparison yourself.

**What changed.**
- New `_comparison_report()`, which builds the Markdown, and `_passk_figure()`, which draws the chart (matplotlib comes with MLflow).
- New `_log_report()` attaches both. It's called automatically for every two-mode run.
- The report lands in two places on the parent run:
  - The **Description** box (the `mlflow.note.content` tag), rendered on the run's Overview page.
  - The **Artifacts** tab: `comparison_report.md` and `pass_at_k.png`.
- The report has four sections: quality table (Pass@k, Recall@k, MRR, with deltas), speed and cost table, per-tag view, and every query whose rank moved.
- The existing run `fc9913ee…` was backfilled from its own logged metrics and `per_query.json`. Nothing was re-run.
- +1 test (`test_comparison_report_counts_rank_moves`). The suite is now 19 passed.

**Where to find it.** MLflow 3 opens in **GenAI** mode (empty trace dashboards). Switch the top-left toggle to **Model training**, or open http://127.0.0.1:5055/#/experiments/1/runs?workflowType=machine_learning directly. Then click **AMD_2022_10K A/B** (click **+** to show `baseline` / `contextual`). Every one of the three runs has the report:
- **Artifacts tab:** `comparison_report.md`, rendered, and `pass_at_k.png`.
- **Overview tab:** the report in the Description box (click "Show more").

---

## 7. Groundedness / citation checks

Date: 2026-10-16. Run `e2020cc6…`, experiment `grounding-amd-10k`: 44 queries × 3 reps, app settings (top-k 5, baseline retrieval), answer `gpt-4.1-mini`, judge `gpt-4.1`.

**What it measures.** Retrieval scores asked "did the right passage come back?". This asks "did the *answer* stick to what came back?".

**What changed.**
- Bug fix (`routes/query.py`): excerpts and page labels were zipped after dropping `None` pages, so DOCX/HTML docs sent the LLM *no excerpts* and mixed docs shifted page labels. +1 route test.
- New `backend/scripts/eval_grounding.py` (reuses the retrieval harness helpers). Per query: real answer path → summary → judge splits it into claims, marks each supported / cited page correct, tags it `fact` or `meta` ("the excerpts don't say X" is meta, not scored — the judge ignored a plain "don't list these" rule).
- Code-only checks: fake pages (cited page outside every excerpt's span), gold-in-excerpts, gold numbers in the answer.
- +2 tests (`tests/test_eval_grounding.py`). Suite: 22 passed.

**Result (mean ± std over 3 reps).**

| metric | value | meaning |
|---|---|---|
| groundedness | 0.993 ± 0.002 | 4 unsupported claims in 132 answers |
| citation precision | 0.995 | 2 wrong citations (the same 2 hallucinated claims) |
| citation coverage | 0.873 | mostly the uncited Key Takeaway line |
| fake pages | 0 | never cites a page it wasn't shown |
| abstention (no-answer) | 100% | all 7 correctly refused |
| false refusal | 11.7% | **every one** had the gold missing from the excerpts |
| gold in excerpts | 75.7% | retrieval at top-k 5 misses ~1 in 4 |
| refused despite gold | 0% | never refused when the answer was there |

**Takeaways.**
- Generation is not the problem. Retrieval at the app's top-k 5 is: every refusal was a retrieval miss.
- One real failure mode: "How much did net revenue grow?" — the total wasn't retrieved, so the model summed the 3 visible segments (missing Embedded) and reported 47% / 49% instead of 44%. It should have refused. Prompt fix candidate: "don't compute totals from partial figures".
- One soft miss: "older-generation wafers" answer added UMC/Samsung, which the excerpts don't say.

**Caveat.** The judge (an LLM) is not yet checked against a human. Sheet: `data/judge_check_amd.md` (20 claims, verdicts hidden; key in `judge_check_amd.key.json`).

## Other files touched

- `backend/tests/test_eval_scoring.py`: +1 test covering MRR, multi-gold ranks and Recall@k. The suite is now 19 passed (with §6); ruff is clean.
- `CLAUDE.md`: one line about the new metrics and the MLflow store.
- `RAG_IMPROVEMENT_REPORT.md`: rerun paragraph in §6, and the stale "14/14 tests" line updated to 18/18.
