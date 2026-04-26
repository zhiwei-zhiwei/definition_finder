# LexisAI Architect — Technical Spec

**Status:** Draft v1 · **Owner:** Jackson · **Last updated:** 2026-04-17

---

## 1. Overview

A single-page web app that lets a user upload a large document (PDF / DOCX / PPTX / HTML / images / etc.), ask a natural-language question about a **term or definition**, and see:

- the original document on the left,
- the retrieval reasoning + clickable source snippets in the middle,
- an LLM-generated summary on the right.

Clicking any snippet in the middle column scrolls the original doc to the matching sentence and highlights it.

Retrieval uses **hierarchical chunking with parent–child consolidation** over a Chroma vector store, with embeddings from OpenAI `text-embedding-3-large`.

---

## 2. Goals & Non-Goals

### Goals (v1)

- Accept any file format Docling supports (PDF, DOCX, PPTX, XLSX, HTML, MD, PNG/JPG via OCR).
- Reliable click-to-highlight mapping from snippet → original document.
- Visualizer in Column 2 reflects **real** backend pipeline state, not a fake animation.
- Parent–child retrieval with majority-vote parent consolidation.
- Single-user, local-first deployment.

### Non-Goals (v1)

- Multi-user auth / RBAC.
- Collaborative editing or annotations.
- Hosted/SaaS deployment.
- Fine-tuning or custom embeddings.
- Mobile responsive design (desktop-first; mobile deferred).

---

## 3. Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Frontend | Next.js 14 (App Router) + TypeScript + Tailwind CSS | Matches the provided HTML mockup design tokens |
| PDF rendering | `react-pdf` (PDF.js) | Needed for hybrid highlight strategy |
| Animation | Framer Motion | Pipeline stage transitions |
| Icons | Material Symbols Outlined | Per mockup |
| Backend | Python 3.11 + FastAPI + Uvicorn | Async, SSE-friendly |
| Doc extraction | `docling` (IBM) | All supported formats |
| Embeddings | OpenAI `text-embedding-3-large` (3072 dims) | Configurable |
| LLM (summary) | OpenAI `gpt-4.1-mini` | Configurable; swap to `gpt-5-mini` or `gpt-4o-mini` via env |
| Vector DB | Chroma (persistent client, file-backed) | `./data/chroma/` |
| Relational | SQLite via SQLAlchemy | Documents table, parent chunks, upload history |
| Tokenizer | `tiktoken` (`cl100k_base`) | For accurate token-based chunking |
| Streaming | Server-Sent Events (SSE) | Pipeline visualizer + summary streaming |

---

## 4. High-Level Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│  Browser (Next.js 14)                                             │
│  ┌───────────────┬──────────────────────┬─────────────────────┐  │
│  │ Col 1: Viewer │ Col 2: Visualizer +  │ Col 3: Input +      │  │
│  │ (PDF.js / HTML)│        Snippets     │        Summary      │  │
│  └──────┬────────┴──────────▲───────────┴──────────▲──────────┘  │
│         │  click snippet    │ SSE: pipeline stages │             │
│         │  scroll+highlight │ + final results      │             │
└─────────┼───────────────────┼──────────────────────┼─────────────┘
          │ GET /docs/:id     │                      │ POST /query
          ▼                   │                      ▼
┌───────────────────────────────────────────────────────────────────┐
│  FastAPI Backend                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────────┐ │
│  │ Ingestion    │  │ Retrieval    │  │ Summarization           │ │
│  │ (Docling →   │  │ (Chroma +    │  │ (OpenAI Chat Completion │ │
│  │  chunk →     │  │  parent      │  │  streamed back via SSE) │ │
│  │  embed)      │  │  consolidation)│                          │ │
│  └──────┬───────┘  └──────┬───────┘  └────────────┬────────────┘ │
└─────────┼─────────────────┼───────────────────────┼──────────────┘
          ▼                 ▼                       ▼
     ┌────────────────────────────────────────────────────┐
     │  SQLite (metadata, parent chunks, upload history)  │
     │  Chroma (child-chunk embeddings + metadata)        │
     │  ./uploads/ (original files)                       │
     │  ./cache/  (Docling HTML renders for non-PDFs)     │
     └────────────────────────────────────────────────────┘
```

---

## 5. Project Structure

```
lexisai-architect/
├── frontend/                        # Next.js 14 app
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx                 # Main workspace
│   │   └── api/                     # (proxied to backend if desired)
│   ├── components/
│   │   ├── TopNav.tsx
│   │   ├── RecentFilesDrawer.tsx
│   │   ├── viewer/
│   │   │   ├── DocumentViewer.tsx   # Routes to PdfViewer or HtmlViewer
│   │   │   ├── PdfViewer.tsx        # react-pdf + bbox overlay
│   │   │   └── HtmlViewer.tsx       # Docling HTML render + scroll/highlight
│   │   ├── process/
│   │   │   ├── PipelineVisualizer.tsx
│   │   │   └── SnippetList.tsx
│   │   └── query/
│   │       ├── QueryInput.tsx
│   │       └── SummaryCard.tsx
│   ├── lib/
│   │   ├── api.ts                   # fetch/SSE helpers
│   │   ├── types.ts                 # shared types
│   │   └── highlight.ts             # bbox + dom-highlight logic
│   ├── tailwind.config.ts           # Use design tokens from mockup
│   └── package.json
│
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI entrypoint
│   │   ├── config.py                # pydantic-settings
│   │   ├── deps.py                  # DI
│   │   ├── routes/
│   │   │   ├── documents.py         # upload, list, get, delete
│   │   │   ├── query.py             # POST /query (SSE)
│   │   │   └── health.py
│   │   ├── services/
│   │   │   ├── ingest.py            # Docling extract + clean
│   │   │   ├── chunker.py           # parent/child token chunking
│   │   │   ├── embed.py             # OpenAI embeddings
│   │   │   ├── retrieve.py          # Chroma query + parent consolidation
│   │   │   └── summarize.py         # OpenAI chat completion (streamed)
│   │   ├── models/
│   │   │   ├── db.py                # SQLAlchemy setup
│   │   │   └── schema.py            # ORM models
│   │   └── utils/
│   │       ├── sse.py               # SSE event helpers
│   │       └── text.py              # cleaning, normalization
│   ├── tests/
│   ├── pyproject.toml
│   └── .env.example
│
├── data/
│   ├── chroma/                      # Chroma persistent store
│   ├── uploads/                     # Original files
│   ├── cache/                       # Docling HTML renders
│   └── app.db                       # SQLite
│
├── docker-compose.yml               # optional
└── README.md
```

---

## 6. Backend

### 6.1 Ingestion pipeline

**Endpoint:** `POST /documents` (multipart upload) → returns `{doc_id}` + streams SSE stages.

Stages emitted over SSE (matching the Column 2 visualizer):

| Stage | Event name | Payload |
|---|---|---|
| `extracting` | `stage` | `{stage: "extracting", message: "Extracting text with Docling"}` |
| `cleaning` | `stage` | `{stage: "cleaning", stats: {chars_before, chars_after}}` |
| `chunking_parents` | `stage` | `{stage: "chunking_parents", count: N}` |
| `chunking_children` | `stage` | `{stage: "chunking_children", count: M}` |
| `embedding` | `stage` | `{stage: "embedding", progress: 0.0→1.0}` |
| `ready` | `done` | `{doc_id, pages, parents: N, children: M}` |

#### 6.1.1 Docling extraction

```python
# services/ingest.py
from docling.document_converter import DocumentConverter

def extract(path: Path) -> DoclingDocument:
    converter = DocumentConverter()
    result = converter.convert(path)
    return result.document  # DoclingDocument
```

The `DoclingDocument` preserves:
- Text items with `provenance` (page number, bbox) for PDFs.
- Structural hierarchy (headings, lists, tables) for all formats.
- An `export_to_html()` method — we cache this output to `./data/cache/<doc_id>.html`.

#### 6.1.2 Cleaning

Run extracted text through a sanity pass:

- Collapse repeated whitespace (`\s{2,}` → single space, preserve paragraph breaks).
- Drop page-number-only lines (regex `^\s*\d+\s*$`).
- Drop repeated header/footer lines that appear on >50% of pages.
- Strip stray bullet glyphs (`•`, `●`, etc.) when they're the only content on a line.
- Keep tables intact (Docling's `TableItem` has its own markdown export — use it).

Log `chars_before`, `chars_after`, and a sample of dropped lines for debugging.

### 6.2 Hierarchical chunking

```python
# services/chunker.py
PARENT_TOKENS = 2000   # configurable
CHILD_TOKENS  = 300    # configurable
OVERLAP       = 50     # between children within a parent

@dataclass
class ParentChunk:
    id: str              # "Parent_00001"
    doc_id: str
    text: str
    page_span: tuple[int, int]   # (start_page, end_page) — PDFs only
    bboxes: list[dict]           # [{page, x0, y0, x1, y1}, ...] — PDFs only
    html_anchor: str | None      # e.g. "#block-37" — non-PDFs only

@dataclass
class ChildChunk:
    id: str              # "Child_00001_001"
    parent_id: str       # "Parent_00001"
    doc_id: str
    text: str
    # Same positional fields as parent — inherited/narrowed
```

**Algorithm:**

1. Walk `DoclingDocument` text items in reading order. Accumulate into parents until reaching `PARENT_TOKENS` (measured with `tiktoken`), then emit with a fresh `Parent_XXXXX` id.
2. For each parent, split its text into children of `CHILD_TOKENS` with `OVERLAP` tokens of overlap. Assign `Child_XXXXX_YYY` ids.
3. Propagate positional metadata:
   - **PDFs:** collect the union of source bboxes for each child from Docling's `provenance`. Store as a list of `{page, bbox}` tuples (a child may span page breaks).
   - **Non-PDFs:** assign each parent/child an `html_anchor` — we'll inject `<span data-chunk-id="Child_XXXXX_YYY">…</span>` wrappers around the rendered HTML during caching.

### 6.3 Embedding & storage

**Embed only children.** Parents are stored in SQLite and retrieved by id after consolidation.

```python
# services/embed.py
from openai import OpenAI

client = OpenAI()  # reads OPENAI_API_KEY

def embed_batch(texts: list[str]) -> list[list[float]]:
    resp = client.embeddings.create(
        model=settings.EMBED_MODEL,   # "text-embedding-3-large"
        input=texts,
    )
    return [d.embedding for d in resp.data]
```

**Chroma schema** (one collection per document, named `doc_<doc_id>`):

```python
collection.add(
    ids=[child.id for child in children],
    embeddings=embeddings,
    documents=[child.text for child in children],
    metadatas=[{
        "parent_id": child.parent_id,
        "doc_id": child.doc_id,
        "page_span_start": child.page_span[0],
        "page_span_end":   child.page_span[1],
        "has_bboxes":      bool(child.bboxes),
        "html_anchor":     child.html_anchor or "",
    } for child in children],
)
```

Bboxes are stored in SQLite (Chroma's metadata is flat scalars only).

### 6.4 Retrieval + parent consolidation

**Endpoint:** `POST /query` (SSE).

Request:

```json
{
  "doc_id": "abc123",
  "query": "What is the definition of 'deferred revenue'?",
  "top_k": 5,
  "summary_style": "concise"
}
```

SSE stages:

| Stage | Event | Payload |
|---|---|---|
| `embedding_query` | `stage` | `{stage, message: "Embedding query"}` |
| `searching` | `stage` | `{stage, top_k: 5}` |
| `consolidating` | `stage` | `{stage, parent_votes: {"Parent_00007": 3, "Parent_00012": 2}}` |
| `fetching_context` | `stage` | `{stage, winning_parents: ["Parent_00007"]}` |
| `snippets` | `snippets` | `[{id, text, page, bbox, parent_id, score}, ...]` |
| `summary_start` | `stage` | `{stage: "summarizing"}` |
| `summary_token` | `token` | `"streamed token"` |
| `done` | `done` | `{}` |

**Consolidation logic** (the "parent majority vote" you specified):

```python
def consolidate(hits: list[Hit], threshold: int = 3) -> list[str]:
    """Given top-K child hits, return parent_ids that have at least
       `threshold` children in the results. Falls back to the single
       best-scoring parent if no parent meets threshold."""
    votes = Counter(h.parent_id for h in hits)
    winners = [p for p, c in votes.items() if c >= threshold]
    if not winners:
        winners = [hits[0].parent_id]   # fallback: best single hit's parent
    return winners
```

The LLM is then fed the **full parent text** for each winner, not the children. The children are still sent to the frontend as the clickable snippet list (because that's what the user should click to see highlighted).

### 6.5 Summarization

```python
# services/summarize.py
SYSTEM_PROMPT = """You are a legal/financial document analyst. You will be
given (1) a user's question about a term or definition, and (2) relevant
excerpts from the source document. Produce a concise, well-structured
summary that directly answers the question. If the document does not
contain a definition, say so plainly — do not fabricate one. Cite the
page number(s) in parentheses where the information appears."""

def summarize_stream(query: str, parent_texts: list[str], pages: list[int]):
    resp = client.chat.completions.create(
        model=settings.CHAT_MODEL,          # "gpt-4.1-mini"
        stream=True,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": format_context(query, parent_texts, pages)},
        ],
        temperature=0.2,
    )
    for chunk in resp:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
```

### 6.6 API surface (complete)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/documents` | Upload + ingest (SSE) |
| `GET` | `/documents` | List recent uploads |
| `GET` | `/documents/{id}` | Metadata + download URL |
| `GET` | `/documents/{id}/file` | Original file stream |
| `GET` | `/documents/{id}/html` | Cached Docling HTML (for non-PDF viewer) |
| `DELETE` | `/documents/{id}` | Remove file + Chroma collection + SQLite rows |
| `POST` | `/query` | Retrieve + summarize (SSE) |
| `GET` | `/health` | Healthcheck |

---

## 7. Frontend

### 7.1 Layout

The top-level grid matches the provided mockup exactly:

- **Global header** (shrink-0): hamburger (opens `RecentFilesDrawer`), logo, nav, avatar.
- **Main** (flex row, gap-6, p-4): three columns at 50% / 25% / 25%.

Design tokens (colors, radius, fonts) copied verbatim from the mockup's `tailwind.config` into `frontend/tailwind.config.ts`.

### 7.2 Column 1 — Document Viewer

`DocumentViewer.tsx` routes based on file type:

```tsx
const isPdf = doc.mime === "application/pdf";
return isPdf ? <PdfViewer doc={doc}/> : <HtmlViewer doc={doc}/>;
```

Both expose the same imperative handle:

```ts
type ViewerHandle = {
  highlight(chunkId: string): void;   // scrolls + flashes highlight
  clearHighlight(): void;
};
```

`DocumentViewer` accepts a `highlightedChunkId` prop from the parent and calls `.highlight()` on the underlying ref when it changes.

#### 7.2.1 `PdfViewer.tsx` (PDF path — strategy B)

- `react-pdf` `<Document>` + `<Page>` with a scroll container.
- For each loaded page, render an absolutely-positioned overlay div sized to the PDF page's dimensions.
- When `highlight(chunkId)` is called:
  1. Look up `bboxes` from chunk metadata (fetched with the query response, not hardcoded).
  2. Scroll to the first bbox's page.
  3. For each `{page, bbox}`, inject a `<div class="bbox-highlight">` into that page's overlay at `(x0, y0, x1-x0, y1-y0)` in PDF user-space coords, scaled by current zoom.
  4. Flash class for 1.5s, then persist a lighter background until cleared.

Bbox coordinate system: Docling emits bboxes in PDF points from bottom-left. Convert to top-left CSS coords with `y_css = page_height - y1`.

#### 7.2.2 `HtmlViewer.tsx` (non-PDF path — strategy A)

- Fetch `/documents/:id/html` and `dangerouslySetInnerHTML` it into a scroll container.
  *(The HTML is ours, produced by Docling and post-processed by us — not user-authored, so this is safe, but still sanitize with DOMPurify for defense in depth.)*
- During ingestion, children's spans are wrapped with `data-chunk-id` attributes (see §6.2). `highlight(chunkId)` then:
  1. `document.querySelector('[data-chunk-id="…"]')` in the viewer's ShadowRoot.
  2. `scrollIntoView({ block: "center", behavior: "smooth" })`.
  3. Add `.chunk-highlight` class for 1.5s flash, then persist `.chunk-highlight-faded`.

### 7.3 Column 2 — Pipeline Visualizer + Snippets

#### 7.3.1 `PipelineVisualizer.tsx`

Renders a vertical stepper whose active step is driven by the last received SSE `stage` event. States map 1:1 to the backend stages listed in §6.4. Only show the query stages here; the upload stages (§6.1) are shown in the `RecentFilesDrawer`.

```tsx
const STAGES = [
  { key: "embedding_query", label: "Embedding your query", icon: "bolt" },
  { key: "searching",       label: "Searching candidate passages", icon: "search" },
  { key: "consolidating",   label: "Consolidating by parent context", icon: "hub" },
  { key: "fetching_context",label: "Fetching targeted context", icon: "filter_alt" },
  { key: "summarizing",     label: "Synthesizing summary", icon: "psychology" },
];
```

Each stage has three visual states: pending (muted), active (spinner + primary color), done (check). Include the backend payload (`top_k`, `parent_votes`, etc.) as a small caption under the active stage — this is what makes it real rather than fake.

#### 7.3.2 `SnippetList.tsx`

Receives the `snippets` SSE event and renders a clickable card per child chunk. Card layout matches the mockup ("Page 3 • Revenue" label, 3-line clamp, active ring).

```tsx
<button
  className={cn("snippet-card", active && "active")}
  onClick={() => onSelectChunk(snippet.id)}
>
  ...
</button>
```

`onSelectChunk` lifts state to the page, which passes `highlightedChunkId` down to `DocumentViewer`.

### 7.4 Column 3 — Query Input + Summary

- `QueryInput.tsx`: textarea + "Analyze" button. Top-K slider (3–20, default 5). Posts to `/query` and opens an SSE stream.
- `SummaryCard.tsx`: renders streaming tokens into markdown via `react-markdown`. Shows a "Key Takeaway" call-out when the LLM includes a `**Key Takeaway:**` block in its output. Copy/thumbs-up buttons at the bottom match the mockup.

---

## 8. Highlight Mapping — the Tricky Bit

Worth calling out explicitly because this is the hinge that makes the whole UX feel real.

**Problem:** when a user clicks snippet card "Child_00042_003," Column 1 must scroll to and visually mark the exact sentence in the original document.

**Solution (hybrid):**

1. At ingest time, every child chunk gets positional metadata attached:
   - **PDFs** → `bboxes: [{page, x0, y0, x1, y1}, ...]` from Docling's `provenance`.
   - **Non-PDFs** → `html_anchor: "data-chunk-id=Child_00042_003"` plus an injected span in the cached HTML.
2. That metadata is persisted (Chroma metadata for flat fields, SQLite for bbox arrays).
3. The `/query` response includes each snippet's positional metadata.
4. `onSelectChunk` in the frontend calls `viewerRef.current.highlight(chunkId)`. The viewer component (PDF or HTML) knows how to interpret its metadata type.

**Edge cases to handle:**

- Child spans a page break → scroll to the first page, draw bbox on both.
- Docling fails to produce a bbox (rare, but happens on malformed PDFs) → fall back to text search inside the PDF.js text layer for the child's first 80 characters.
- User re-clicks the same snippet → re-trigger the flash animation (don't no-op).
- Very small bbox (e.g., a one-word snippet) → pad the highlight rectangle by 4px to keep it visible.

---

## 9. Data Model (SQLite)

```sql
CREATE TABLE documents (
  id             TEXT PRIMARY KEY,     -- uuid
  filename       TEXT NOT NULL,
  mime           TEXT NOT NULL,
  size_bytes     INTEGER NOT NULL,
  pages          INTEGER,
  uploaded_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  status         TEXT NOT NULL         -- 'ingesting'|'ready'|'failed'
);

CREATE TABLE parent_chunks (
  id             TEXT PRIMARY KEY,     -- 'Parent_00001'
  doc_id         TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  text           TEXT NOT NULL,
  page_start     INTEGER,
  page_end       INTEGER,
  token_count    INTEGER NOT NULL
);
CREATE INDEX idx_parent_doc ON parent_chunks(doc_id);

CREATE TABLE child_chunks (
  id             TEXT PRIMARY KEY,     -- 'Child_00001_001'
  parent_id      TEXT NOT NULL REFERENCES parent_chunks(id) ON DELETE CASCADE,
  doc_id         TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  text           TEXT NOT NULL,
  bboxes_json    TEXT,                 -- JSON array [{page,x0,y0,x1,y1}]
  html_anchor    TEXT,
  token_count    INTEGER NOT NULL
);
CREATE INDEX idx_child_parent ON child_chunks(parent_id);
CREATE INDEX idx_child_doc    ON child_chunks(doc_id);
```

---

## 10. Environment & Config

`backend/.env.example`:

```bash
# ─── OpenAI ─────────────────────────────────────────────
OPENAI_API_KEY=           # DO NOT COMMIT. Rotate any leaked key.
EMBED_MODEL=text-embedding-3-large
CHAT_MODEL=gpt-4.1-mini

# ─── Chunking ───────────────────────────────────────────
PARENT_TOKENS=2000
CHILD_TOKENS=300
CHILD_OVERLAP=50

# ─── Retrieval ──────────────────────────────────────────
DEFAULT_TOP_K=5
PARENT_CONSOLIDATION_THRESHOLD=3

# ─── Storage paths ──────────────────────────────────────
UPLOAD_DIR=./data/uploads
CACHE_DIR=./data/cache
CHROMA_DIR=./data/chroma
SQLITE_URL=sqlite:///./data/app.db

# ─── Server ─────────────────────────────────────────────
CORS_ORIGINS=http://localhost:3000
```

`frontend/.env.local`:

```bash
NEXT_PUBLIC_API_BASE=http://localhost:8000
```

**Secrets policy:** API keys are only ever read from environment variables on the backend. The frontend never sees or forwards them. `.env` files are gitignored. The example values above are the only forms of those variables allowed in the repo.

---

## 11. Developer Setup

### One-time

```bash
# Backend
cd backend
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env      # then fill in OPENAI_API_KEY

# Frontend
cd ../frontend
pnpm install
cp .env.example .env.local
```

### Run

```bash
# Terminal 1 — backend
cd backend && uvicorn app.main:app --reload --port 8000

# Terminal 2 — frontend
cd frontend && pnpm dev   # http://localhost:3000
```

### Test

```bash
cd backend && pytest
cd frontend && pnpm test
```

---

## 12. Milestones

| # | Milestone | Deliverable |
|---|---|---|
| M1 | Skeleton | Repo structure, FastAPI + Next.js "hello" both running |
| M2 | Ingestion | Upload → Docling → SQLite; can view extracted text in `/documents/{id}` JSON |
| M3 | Chunking + embedding | Parent/child chunks stored, Chroma collection populated |
| M4 | Retrieval | `/query` returns top-K + consolidated parents (no LLM yet) |
| M5 | Summarization | LLM streaming via SSE; Column 3 working end-to-end |
| M6 | Viewer — PDF | `react-pdf` + bbox highlight path working |
| M7 | Viewer — HTML | Docling HTML render + `data-chunk-id` highlight path working |
| M8 | Visualizer | Column 2 wired to real SSE stage events |
| M9 | Polish | Recent files drawer, copy/thumbs, loading states, error boundaries |

---

## 13. Open Questions / Future Work

- **Model choice:** spec defaults to `gpt-4.1-mini`. Confirm vs `gpt-5-mini` or `gpt-4o-mini` before M5.
- **OCR quality:** Docling uses EasyOCR by default. For scanned PDFs we may want to swap in PaddleOCR or Tesseract — evaluate with a few real docs before committing.
- **Term-definition mode:** the prompt in §6.5 is general; we should A/B a specialized "definition extraction" prompt that explicitly structures output as `{term, definition, source_page}`.
- **Index reuse:** currently one Chroma collection per document. If users repeatedly query the same doc, that's fine. If we later want cross-doc queries, switch to a single collection with `doc_id` filter.
- **Large-file upload:** for v1, enforce a hard 500 MB / 1000-page cap. Streaming upload + chunked ingestion comes in v2.
- **Evaluation harness:** write a small eval set (20–30 known term/definition pairs per doc type) to regression-test retrieval quality whenever chunking params change.
