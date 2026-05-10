# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

LexisAI Architect — a single-user, local-first web app that ingests a document (PDF/DOCX/PPTX/HTML/images via Docling), runs hierarchical parent–child RAG over it, and streams an LLM summary back with clickable source snippets. The full design is in `spec.md`; user-facing setup is in `how_to_run.md`.

The repo is split into two independently managed apps that talk over HTTP:

- `backend/` — Python 3.11 + FastAPI, dependencies via `uv` in `backend/.venv`
- `frontend/` — Next.js 14 (App Router) + TypeScript + Tailwind, dependencies via `pnpm`

There is no monorepo tooling. Each side is built, run, and tested on its own.

## Commands

Backend (run from `backend/`, with `.venv` activated):

```bash
uv venv --python 3.11 && source .venv/bin/activate    # one-time
uv pip install -e ".[dev]"                            # install / refresh deps
uvicorn app.main:app --reload --port 8000             # dev server (http://localhost:8000, /docs for Swagger)
pytest                                                # tests (the tests/ dir is currently empty)
pytest tests/path/to/test_file.py::test_name          # single test
ruff check app                                        # lint
```

Frontend (run from `frontend/`):

```bash
pnpm install
pnpm dev              # http://localhost:3000
pnpm build && pnpm start   # prod build
pnpm lint             # next lint / eslint
```

Wipe local state: `bash backend/scripts/wipe_data.sh` from the repo root. The script first writes a timestamped backup at `data.bak.<ts>` (embeddings cost real OpenAI dollars to regenerate) and then removes `app.db`, `chroma/`, `uploads/`, `cache/`. Restore with `mv data.bak.<ts> data` after `rm -rf data`.

## Architecture

### Storage layout — everything under `data/` at the repo root

The backend resolves paths relative to its CWD (`backend/`) using `../data/...`. All four stores live there and must stay in sync; deleting one without the others leaves the app in a broken state. Use `DELETE /documents/{id}` (which calls `vectorstore.delete_collection` + SQLAlchemy cascade + filesystem unlink) rather than removing files by hand.

| Store      | Path                         | Holds                                                                                                                                                                              |
| ---------- | ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SQLite     | `data/app.db`                | `users`, `email_codes` (auth); `documents`, `parent_chunks`, `child_chunks` (metadata + full text + bbox JSON); `queries` (cached past-query results for the drawer's Queries tab) |
| Chroma     | `data/chroma/`               | One collection per doc (`doc_<id_no_dashes>`), child-chunk embeddings + scalar metadata                                                                                            |
| Uploads    | `data/uploads/<doc_id><ext>` | Original files                                                                                                                                                                     |
| HTML cache | `data/cache/<doc_id>.html`   | Docling's HTML render with `<span data-chunk-id=...>` anchors injected by `prepare._inject_chunk_anchors`                                                                          |

### Lazy ingestion (important)

`POST /documents` only stores the file and a row with `status="ready"`. **It does not chunk or embed.** The expensive pipeline (Docling extract → clean → parent chunks → child chunks → embed) runs the first time `POST /query` is called, gated by `prepare.has_chunks(doc_id)`. The same SSE stream then carries those preparation stages followed by the retrieval/summary stages, so the frontend's `PipelineVisualizer` works off real backend events, not animations.

Implication: changing chunking or extraction logic does not invalidate already-prepared documents — the gate is "any child chunk exists in SQLite for this doc?". To force a re-prepare, delete the doc and re-upload, or clear its rows from `child_chunks` + the corresponding Chroma collection.

### Authentication

Real email-based auth with two sign-in methods: password and email code (6-digit OTP). Signup requires email + username + password.

- All auth surface lives in `backend/app/services/auth.py` (bcrypt hashing, JWT issue/decode, FastAPI deps `get_current_user_optional` / `get_current_user`) — single swap point for production hardening. Document/query routes consume the dep and never inspect tokens themselves.
- Outbound email goes through `backend/app/services/email.py::send_code` — today only `EMAIL_BACKEND=console` (logs the code via stdlib INFO). Future SMTP/SES/Resend backends plug in here without changing call sites.
- JWT lifetime 30 days, no refresh, no revocation; token in `localStorage.lexisai_token`. Includes a `jti` claim ready for a future revoked-jtis table.
- Email codes: 6 digits, 10-min TTL, 3 attempts max, 60s resend throttle. Codes are bcrypt-hashed at rest, marked `consumed_at` on success / 3rd failure / TTL expiry — strict one-time-use.
- Login by email-code requires an existing user (honest 404 → frontend offers "Create one" link). Codes never auto-create accounts.
- Signup password policy is enforced server-side in `routes/auth.py::_validate_signup_password`: 8+ chars (Pydantic `min_length`) + one uppercase + one symbol from `string.punctuation`. Existing users are grandfathered — `LoginRequest` and `CodeVerifyRequest` deliberately do **not** call the policy, so legacy hashes keep working. Don't add policy checks to the login paths.
- Login responses are intentionally generic 401 `invalid_credentials` for both unknown-email and bad-password; `verify_password` runs a dummy bcrypt compare on missing users to flatten timing. Don't add early returns that shortcut this.
- Anon-doc claim: anon uploads have `documents.user_id IS NULL`; on sign-in, `sessionStorage.lexisai_anon_doc` is sent to `POST /auth/claim`, which flips ownership for any null-owned matching id. uuid4 hex doc ids make squatting impractical; signed claim tokens are a deferred hardening (TODO in `routes/auth.py::claim`).
- Frontend: `AuthContext` exposes `signup` / `loginWithPassword` / `loginWithCode` / `requestCode` / `logout`; all login paths run claim-on-login internally. `LoginModal` has a Sign in / Create account toggle with Password / Email-code sub-tabs.

### Retrieval pipeline (`backend/app/services/`)

1. **`ingest.extract`** — runs Docling, walks `doc.iterate_items()`, captures per-item page + bbox provenance (PDFs only), and also stashes `doc.export_to_html()`. Bboxes are normalized to `{x0, y0, x1, y1}` from Docling's `{l, t, r, b}` PDF-points convention.
2. **`chunker.chunk_document`** — token-based (tiktoken `cl100k_base`):
   - Parents: greedy pack of extracted items up to `PARENT_TOKENS` (default 1000). Parent text is `"\n\n".join(items)`; page span = min/max of item pages.
   - Children: sentence-split each parent on `[.!?]\s+[A-Z"'(\[]` then greedy-pack to `CHILD_TOKENS` (default 120). Oversized single sentences are emitted as their own child. IDs are deterministic: `<doc_prefix>_Parent_NNNNN` / `<...>_Child_NNNNN_KKK`.
   - Children inherit the parent's `bboxes` and page span — they do **not** track their own sub-bboxes.
3. **`prepare.prepare_document_stream`** — orchestrates extract → chunk → SQLite insert → HTML anchor injection → batched embed (batches of 64) → Chroma upsert. Yields one SSE `stage` event per phase (and per embedding batch with `progress`).
4. **`retrieve.hybrid_search`** — runs the dense-vector arm (`search_with_vec`, Chroma cosine) and the lexical arm (`bm25.bm25_search`, in-memory `BM25Okapi` built per query from the doc's `child_chunks` rows) over a candidate pool of `max(top_k * 4, 20)`, then fuses with **Reciprocal Rank Fusion** (`_rrf_merge`, k=60). The fused score is normalized by the top hit so the UI's percentage badge stays in [0,1]. BM25-only hits are hydrated from SQLite. Don't replace this with vector-only — BM25 is what catches rare lexical matches like statute codes (`K-2L-L50`) and proper nouns.
5. **`retrieve.consolidate`** — majority-votes parent IDs: parents with ≥`PARENT_CONSOLIDATION_THRESHOLD` (default 3) child hits win; if none clear the bar, fall back to the top 3 unique parents by best-child score. This fallback matters for short queries where votes spread thin — don't remove it.
6. **`retrieve.enrich_hits_from_db`** — Chroma metadata is scalars-only (the JSON bbox blob is stored in SQLite as `bboxes_json`), so hits are re-hydrated from `child_chunks` before being sent to the frontend.
7. **`highlight.compute_spans`** — runs after retrieval (no LLM call): two-pass scanner over each snippet's text against the user query. Token pass bolds every non-stop-word query token (whole-word, case-insensitive). Intent pass fires regex categories (date, currency, address, phone, percent) when the query contains trigger words like "when", "how much", "where", "address" — so the snippet shows the _answer_ highlighted even when the literal question word isn't present. Returns merged non-overlapping `[start, end]` spans. Shares its tokenizer + stop list with `bm25.py`; keep them in sync.
8. **`summarize.summarize_stream`** — sends consolidated **parent** texts (not children) as context to OpenAI Chat with a system prompt that requires a final `**Key Takeaway:** <sentence>` line. The frontend's `SummaryCard` renders that line specially.

### SSE event protocol (`POST /query`)

The single endpoint streams typed events; the frontend's `consumeSSE` (in `frontend/lib/api.ts`) dispatches by `event:` name:

- `stage` — `{stage: "...", ...}` — drives the visualizer. Stage names from prepare: `extracting`, `cleaning`, `chunking_parents`, `chunking_children`, `embedding`. From query: `embedding_query`, `searching`, `consolidating`, `fetching_context`, `summarizing`.
- `snippets` — array of clickable child hits (with bboxes + html_anchor + `highlight_spans: [[start,end], ...]`). Emitted **before** the summary tokens, so the UI can render the snippet list while the LLM is still generating. Spans are character offsets into `text`; the frontend slices and wraps matches in `<strong>` (no HTML injection, so no DOMPurify).
- `token` — raw string fragments of the LLM summary; concatenated client-side.
- `done` / `error` — terminal events.

Token streaming bridges OpenAI's blocking generator into asyncio via `_bridge_token_stream` (worker thread + `asyncio.Queue` + sentinel). Don't make `summarize_stream` async — keep the thread-bridge pattern.

### Frontend layout

`frontend/app/page.tsx` is a 3-column workspace held together by local React state:

- Col 1 (`viewer/`): `DocumentViewer` routes by mime — `PdfViewer` (react-pdf, bbox overlay) for PDFs, `HtmlViewer` (Docling render + `data-chunk-id` scroll/highlight) for everything else.
- Col 2 (`process/`): `PipelineVisualizer` consumes stage events; `SnippetList` renders each hit as a collapsible card (header = bold "Page N" + parent_id + score badge + chevron toggle; expanded body = `HighlightedText` (slices `text` by `highlight_spans` and wraps matches in `<strong>` with a primary-tint background) + a "Jump to page N →" button that drives `pageTarget`). A "Collapse all / Expand all" toggle sits in the section header. Cards must keep `shrink-0` — the container is `flex flex-col` with overflow scroll, and without it collapsed cards get flex-shrunk to a few pixels.
- Col 3 (`query/`): `QueryInput` + `SummaryCard` (streams the `**Key Takeaway:**` line into a callout).

The PDF viewer uses `react-pdf`'s reported page dimensions (which include current scale) — bbox overlay math accounts for that. If you change zoom or fit logic, retest highlighting.

`TopNav` carries the auth pill (avatar with `username.slice(0,2).toUpperCase()` when signed in, "Sign in" button otherwise). The drawer mounts a per-user CTA when `!user` and a Files / Queries tab strip when signed in. The login modal is mounted in `app/page.tsx`; both `TopNav` and the drawer trigger it via `onOpenLogin`.

## Conventions worth knowing

- **Two terminals during dev** — backend venv must stay active in one; frontend `pnpm dev` runs in the other. Don't cross the streams.
- **Config flows through `app.config.settings`** (pydantic-settings, reads `backend/.env`). Tunables: `PARENT_TOKENS`, `CHILD_TOKENS`, `DEFAULT_TOP_K`, `PARENT_CONSOLIDATION_THRESHOLD`, model names, paths, `JWT_SECRET` (override in prod — RFC 7518 wants ≥32 bytes for HS256), `JWT_EXP_SECONDS` (default 30d), `CODE_LENGTH` / `CODE_TTL_SECONDS` / `CODE_RESEND_SECONDS` / `CODE_MAX_ATTEMPTS`, `EMAIL_BACKEND` (today: `"console"`).
- **Re-prepare on chunk-config change** isn't automatic. If you change `CHILD_TOKENS` / `PARENT_TOKENS`, already-prepared docs keep their old chunks (the gate is "any child rows exist"). Delete + re-upload to pick up the new sizes.
- **CORS** is driven by the comma-separated `CORS_ORIGINS` env var; default permits only `http://localhost:3000`.
- **Without `OPENAI_API_KEY`**, upload still works but `/query` 401s on the first embed call (query embedding) — fail mode is loud, not silent.
