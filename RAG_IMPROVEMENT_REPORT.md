# RAG Improvement Report — Contextual Retrieval + Efficient Vector Storage

**Date:** 2026-06-24
**Scope:** Backend retrieval pipeline (`backend/app/...`), driven by Anthropic's
[Contextual Embeddings cookbook](https://github.com/anthropics/claude-cookbooks/blob/main/capabilities/contextual-embeddings/guide.ipynb).
**Constraints honored:** embedding + retrieval stay on the OpenAI API (no Voyage,
no Cohere); vectors moved to an efficient single-file store.

---

## 1. TL;DR

Two changes, both contained to the backend services layer:

1. **Contextual Retrieval.** Before a chunk is embedded, an LLM writes a 1–2
   sentence blurb situating it inside the _whole document_; that blurb is
   prepended to the chunk for **both** the dense embedding and the BM25 index.
   This is the cookbook's core technique. The only deviations: the situating
   model is OpenAI `gpt-4.1-mini` (one vendor, as requested), and context is
   generated **per parent** rather than per child to fit this app's existing
   parent–child design and keep first-query latency bounded.

2. **Efficient vector storage.** Embeddings moved out of a separate per-document
   Chroma collection and into a `float32` BLOB column on `child_chunks` in the
   existing SQLite file. Search is a brute-force numpy cosine over the doc's
   vectors. This removes an entire store (and a class of "two stores drifted out
   of sync" bugs), one dependency (`chromadb`), and — at this app's per-document
   scale — is _faster_ than an HNSW index.

Status: implemented, wired end-to-end, lint-clean, **14/14 tests pass** (10
pre-existing + 4 new retrieval/pipeline tests).

**Honest headline result (§6):** on the one document we measured (a 51-page
lease, 16 gold queries), contextual retrieval did **not** improve recall and
slightly _hurt_ at mid-k — the storage change is an unambiguous win, but the
contextual-embedding win is corpus-dependent and unproven on this data. It ships
behind the `CONTEXTUAL_EMBEDDINGS` flag; recommended **off** for single-topic
docs like this lease until re-measured on a more diverse corpus.

---

## 2. The problem with the old pipeline

The old flow embedded **raw child text** (≈120 tokens) in isolation:

```
Docling extract → parent/child chunks → embed(child.text) → Chroma → hybrid(vec+BM25) → consolidate → summarize
```

A 120-token child sentence like _"It was increased to $4.2M in the third
year."_ has no idea which contract, which party, or which clause "it" refers to.
Embedded alone, it lands near every other dollar-figure sentence in the corpus.
The cookbook's measurement of this exact failure: isolated-chunk embedding leaves
~12.85% of relevant chunks out of the top-20. Contextual embeddings cut that
failure rate by ~35%, and contextual BM25 on top by ~49% (cookbook's dataset).

## 3. What changed

### 3a. Contextual embeddings (`services/context.py` — new)

For each **parent** chunk, we call `gpt-4.1-mini` with the cookbook prompt:

```
<document>{full_document}</document>
Here is the chunk we want to situate within the whole document
<chunk>{parent_text}</chunk>
Please give a short succinct context to situate this chunk within the overall
document for the purposes of improving search retrieval of the chunk. Answer
only with the succinct context and nothing else.
```

- **Why OpenAI, not Claude:** keeps the whole stack on one vendor/key. The
  cookbook uses Anthropic prompt caching (`cache_control`) to make the repeated
  document prefix cheap; OpenAI does the equivalent **automatically** for
  identical prefixes >1024 tokens, so we get the same economics by putting the
  document first and keeping it byte-identical across a doc's calls.
- **Why per-parent, not per-child:** children already inherit their parent's
  pages/bboxes in this design, so they inherit its context too. A 33-parent /
  275-child document drops from ~275 LLM calls to ~33 (~8×). Calls run
  concurrently (3 threads, with SDK 429 backoff) and **degrade to empty context
  on error**, so one bad call never aborts a document.
- **What gets stored/indexed:** the blurb is saved to `child_chunks.context_text`
  and prepended as `"{context}\n\n{text}"` before embedding. The user still sees
  the bare snippet — context is a retrieval-only signal, never displayed.
- **Toggle:** `CONTEXTUAL_EMBEDDINGS=false` reverts to raw-text embedding for a
  clean A/B (see §6).

### 3b. Contextual BM25 (`services/bm25.py`)

The per-query `BM25Okapi` corpus is now built from `context_text + " " + text`
instead of `text` alone — mirroring the cookbook's `multi_match` over both
`content` and `contextualized_content`. A statute code or proper noun mentioned
only in the situating context now produces a lexical hit.

### 3c. Efficient vector storage (`services/vectorstore.py` — rewritten)

|                        | Before (Chroma)                 | After (SQLite + numpy)                    |
| ---------------------- | ------------------------------- | ----------------------------------------- |
| Where vectors live     | `data/chroma/` (separate store) | `child_chunks.embedding` BLOB in `app.db` |
| Stores to keep in sync | 4                               | 3                                         |
| Index                  | HNSW (ANN)                      | brute-force cosine (exact)                |
| Search scope           | per-doc collection              | per-doc `WHERE doc_id=…`                  |
| Dependency             | `chromadb` (+ its deep tree)    | `numpy` (was already transitive)          |
| Delete path            | `delete_collection` + cascade   | cascade only (vectors are child rows)     |

At this app's scale (hundreds–few-thousand child vectors _per document_), a numpy
matmul is sub-millisecond and needs no index build — HNSW was pure overhead.
Vectors are little-endian `float32` (`<f4`) so they're portable across machines.

### 3d. Pipeline wiring & robustness

- `prepare_document_stream` gained a `contextualizing` SSE stage and now writes
  embeddings back to SQLite per batch (no Chroma upsert).
- **`prepare` is now idempotent** — it clears a doc's existing parent/child rows
  before rebuilding, so re-prepare can't collide on the deterministic chunk IDs.
- **`has_chunks` gates on the embedding**, not just any child row. This is the
  migration trick: docs left over from the Chroma era have child rows but a NULL
  `embedding`, so they transparently re-prepare on their next query.
- `retrieve.hybrid_search` hydrates the fused top-k from SQLite in one pass,
  including the parent page span (children don't store their own pages — that
  used to come from Chroma metadata).
- **Optional `EMBED_DIM`** (OpenAI Matryoshka truncation) lets you trade recall
  for ~3× smaller BLOBs / faster cosine; default `None` preserves full 3072-dim
  vectors and existing behavior.

## 4. Files touched

| File                                                        | Change                                                                                                                      |
| ----------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `services/context.py`                                       | **new** — per-parent situating-context generation (OpenAI, concurrent, graceful)                                            |
| `services/vectorstore.py`                                   | **rewritten** — Chroma → numpy cosine over SQLite BLOBs (`pack`/`unpack`/`search`)                                          |
| `services/prepare.py`                                       | per-parent context stage; embed contextualized text → SQLite; idempotent rebuild; embedding-gated `has_chunks`              |
| `services/retrieve.py`                                      | vector arm → `vectorstore.search`; single-pass `_hydrate_hits` with parent pages; removed dead `search()`/`search_with_vec` |
| `services/bm25.py`                                          | index `context_text + text`                                                                                                 |
| `services/embed.py`                                         | honor optional `EMBED_DIM`                                                                                                  |
| `models/schema.py`                                          | `child_chunks.context_text` (Text), `child_chunks.embedding` (BLOB)                                                         |
| `models/db.py`                                              | idempotent migration for the two new columns                                                                                |
| `config.py`                                                 | `CONTEXTUAL_EMBEDDINGS`, `CONTEXT_MODEL`, `CONTEXT_MAX_DOC_TOKENS`, `EMBED_DIM`; removed `CHROMA_DIR`/`chroma_path`         |
| `routes/documents.py`                                       | drop `vectorstore.delete_collection` (cascade handles it)                                                                   |
| `pyproject.toml`                                            | `-chromadb`, `+numpy`                                                                                                       |
| `frontend/.../PipelineVisualizer.tsx`                       | label the new `contextualizing` stage                                                                                       |
| `CLAUDE.md`, `scripts/wipe_data.sh`                         | docs aligned to the new storage model                                                                                       |
| `tests/test_retrieval.py`, `tests/test_prepare_pipeline.py` | **new** — see §5                                                                                                            |

## 5. Verification

New tests (network-free — vectors inserted by hand; Docling/OpenAI mocked):

- `test_vectorstore_round_trip_ranks_by_cosine` — store vectors, nearest ranks first.
- `test_contextual_bm25_matches_on_context_only` — a term appearing **only** in
  `context_text` is retrieved by BM25.
- `test_hybrid_search_hydrates_parent_page` — fusion works and the page span comes
  from the parent; score normalized to (0,1].
- `test_prepare_then_retrieve` — full `prepare → retrieve` with mocks: every child
  ends up with an embedding + inherited context, `has_chunks` flips true, and a
  vector query reads it back.

```
14 passed     ·     ruff: No issues found
```

## 6. Measured results — and an honest negative finding

A reusable harness ships at `backend/scripts/eval_retrieval.py` with gold labels
at `backend/scripts/eval_labels/streeter_lease.json`. It prepares the doc once per
mode (`CONTEXTUAL_EMBEDDINGS` off/on) and scores whether each query's gold passage
lands in the top-k snippets. To run it:

```bash
python scripts/eval_retrieval.py --pdf "<file.pdf>" --labels scripts/eval_labels/streeter_lease.json --top-k 20
```

### Measured on _The Streeter_ lease (51 pp, 16 hand-authored gold queries, 2026-06-24)

Validity check: this run logged **0 context-generation failures / 0 rate-limit
errors**, so every parent got a real situating context.

| Pass@k | baseline | contextual | delta      |
| ------ | -------- | ---------- | ---------- |
| @1     | 43.8%    | 50.0%      | **+6.2%**  |
| @3     | 68.8%    | 62.5%      | −6.2%      |
| @5     | 87.5%    | 75.0%      | **−12.5%** |
| @10    | 87.5%    | 81.2%      | −6.2%      |
| @20    | 93.8%    | 93.8%      | ±0.0%      |

**Contextual embeddings did not help here, and slightly hurt at @3–@10.** Most
per-query ranks were identical; contextual nudged a few up by one (admin fee 2→1,
holdover 3→2, guests 5→4, eviction 4→3) but regressed two specific-fact queries
that drove the mid-k drop: cable-$75 (3→8) and bed-bug responsibility (3→11).

Why this corpus is a poor fit for the technique (best guesses, ranked):

1. **Strong baseline + tiny eval set.** Baseline is already 87.5% @5 / 93.8% @20 —
   little headroom — and at 16 queries each one is worth 6.25%, so −12.5% @5 is
   _two_ queries moving. Not statistically robust; treat as directional.
2. **Single-topic boilerplate.** Contextual Retrieval shines when a chunk is
   ambiguous out of context. A lease is one coherent document, so the per-parent
   blurbs come out near-identical and _homogenize_ chunks rather than
   differentiate them — the opposite of the cookbook's diverse-corpus setting.
3. **Contextual-BM25 dilution.** Injecting repetitive blurb vocabulary
   ("lease", "tenant", "section") into every chunk reduces BM25 contrast on a
   homogeneous doc — a plausible cause of both regressions.
4. **Per-parent granularity** (the efficiency choice) is coarser than the
   cookbook's per-child; a shared blurb can pull a specific child (the $75 cable
   line) toward its section's general theme.

**Takeaway:** the feature is correct and measurable, but its value is
corpus-dependent. It's behind the `CONTEXTUAL_EMBEDDINGS` flag for exactly this
reason — on single-topic documents like this lease, leave it **off**; re-measure
before enabling it on a more diverse corpus. See §8 for higher-leverage next
steps (per-child context, reranking, larger label set).

### Cookbook reference numbers (different corpus — for contrast, not a promise)

The cookbook measured these averaged across diverse datasets (code, finance,
etc.); they are _not_ what this lease showed:

| Configuration                          | Pass@5 | Pass@10 | Pass@20 |
| -------------------------------------- | ------ | ------- | ------- |
| Baseline (isolated embeddings)         | 80.9%  | 87.2%   | 90.1%   |
| + Contextual embeddings                | 88.1%  | 92.3%   | 94.3%   |
| + Contextual BM25                      | 88.9%  | 92.3%   | 95.2%   |
| + Reranking (not implemented — see §8) | 92.2%  | 95.3%   | 97.5%   |

## 7. Cost & latency (with a real-run gotcha)

- **One-time, per document, on first query:** ~1 LLM call per _parent_ (cheap
  model, document prefix auto-cached). Queries themselves are unchanged in cost
  (still one query embedding + one summary).
- **TPM gotcha found during the eval.** Each context call ships the _whole_
  document as its prefix. On the 51-page lease (~50k tokens) that's ~50k tokens
  per call; at the original 5-way concurrency the burst blew past OpenAI's 200k
  tokens/min cap for `gpt-4.1-mini`, and 429s were silently degrading parents to
  _empty_ context (corrupting the first eval run). **Fix applied:** SDK 429
  backoff (`max_retries=8`, honors `Retry-After`) + concurrency capped at 3
  (`context._MAX_WORKERS`). Cost is small, but **wall-clock on a large doc is
  TPM-bound** — roughly `doc_tokens × n_parents ÷ TPM` (here ~8–10 min for the
  context pass alone). For big docs under a tight limit, lower
  `CONTEXT_MAX_DOC_TOKENS` or raise your org's rate limit.
- **Storage:** 3072-dim `float32` ≈ 12 KB/child. A 300-child doc ≈ 3.6 MB in
  `app.db`. Set `EMBED_DIM=1024` to cut that ~3× if size matters.
- **Removed:** the `chromadb` dependency and its native/runtime footprint.

## 8. Next steps & out of scope

Given the negative finding (§6), the highest-leverage follow-ups if you want to
make contextual retrieval pay off — none implemented here:

- **Bigger labelled set + more diverse docs.** 16 queries on one boilerplate lease
  can't separate signal from noise. Add a few documents (ideally less homogeneous)
  and ~50+ gold queries before trusting any delta. The harness already supports
  any `--pdf` + `--labels`.
- **Per-child context** (vs the current per-parent). Finer situating may recover
  the specific-fact regressions, at ~8× the context-gen calls — re-measure the
  cost/quality trade with the harness.
- **An isolating ablation** the harness doesn't yet do: contextual-vector-only vs
  contextual-BM25-only, to confirm the hypothesis that blurb vocabulary is
  diluting BM25 on homogeneous docs (§6 reason 3). If confirmed, embed the
  contextualized text but keep BM25 on the bare chunk.

Deliberately out of scope:

- **Reranking.** The cookbook's biggest single jump (Pass@10 92→95%) comes from a
  Cohere reranker — a third vendor you asked to avoid. An OpenAI-only alternative
  is an LLM-as-reranker pass over the candidate pool, but it adds latency + per-
  query cost; left as future work rather than silently swapping vendors.
- **ANN index.** Not needed at per-document scale; revisit only if you move to
  cross-document / multi-thousand-vector search (then `sqlite-vec` or returning
  to a dedicated store would be the call).
- **Re-embedding existing docs** happens lazily via the NULL-embedding gate; no
  bulk migration script was added.
