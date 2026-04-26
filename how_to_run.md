# How to run LexisAI Architect

This project has two isolated parts that run side by side:

- **backend/** — Python 3.11 + FastAPI, managed by `uv` in its own `.venv`
- **frontend/** — Next.js 14 + TypeScript, managed by `pnpm`

Each has its own dependency store; nothing is installed globally.

---

## 0. Prerequisites

Install once on your machine (the project itself stays isolated):

| Tool | Install |
|---|---|
| Python 3.11 | `brew install python@3.11` or pyenv |
| `uv` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node.js ≥ 18 | `brew install node` |
| `pnpm` | `npm install -g pnpm` (or `corepack enable pnpm`) |

Verify:

```bash
uv --version
pnpm --version
python3.11 --version
```

You will also need an **OpenAI API key** (`sk-…`) with access to
`text-embedding-3-large` and the chat model you plan to use.

---

## 1. First-time setup

### 1a. Backend

```bash
cd backend

# Create an isolated Python venv in backend/.venv
uv venv --python 3.11
source .venv/bin/activate

# Install deps (pinned to this venv only)
uv pip install -e ".[dev]"

# Configure secrets / paths
cp .env.example .env
# then open backend/.env and set OPENAI_API_KEY=sk-...
```

> All data (SQLite DB, uploaded files, Chroma vectors, cached HTML renders)
> is written under `../data/` at the repo root — nothing goes to your home
> directory or /tmp.

### 1b. Frontend

Open a **second terminal** (backend venv stays active in the first):

```bash
cd frontend

# pnpm keeps its store locally in node_modules/.pnpm — fully isolated
pnpm install

cp .env.example .env.local
# default NEXT_PUBLIC_API_BASE=http://localhost:8000 matches the backend
```

---

## 2. Run both servers

### Terminal 1 — backend

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

The API is now at **http://localhost:8000**. Open
`http://localhost:8000/docs` for the interactive Swagger UI.

### Terminal 2 — frontend

```bash
cd frontend
pnpm dev
```

Open **http://localhost:3000** in your browser.

---

## 3. Using the app

1. The **Recent Files drawer** opens automatically on first load.
   Click **Upload document** and pick a PDF / DOCX / PPTX / HTML /
   image — anything Docling supports.
2. You'll see live pipeline stages (`extracting → cleaning →
   chunking_parents → chunking_children → embedding → ready`).
3. Once the doc appears in the list, click it. The left column loads
   the rendered document (PDF via `react-pdf`, everything else via the
   Docling HTML render).
4. In the right column's **Ask LexisAI** box, type a question about
   a term or definition (e.g. "What is the definition of 'deferred
   revenue'?") and click **Analyze**.
5. The middle column shows the real retrieval pipeline stages and a
   list of source snippets. **Click any snippet** to scroll the left
   column to the matching sentence and flash-highlight it.
6. The right column streams the LLM summary. If the model emits a
   `**Key Takeaway:**` line, it renders as a call-out card.

---

## 4. Common tasks

| Task | Command |
|---|---|
| Run backend tests | `cd backend && source .venv/bin/activate && pytest` |
| Build frontend for prod | `cd frontend && pnpm build && pnpm start` |
| Wipe all local data | `rm -rf data/chroma data/uploads data/cache data/app.db` |
| Delete a specific doc | use the drawer's trash icon, or `curl -X DELETE http://localhost:8000/documents/<id>` |
| Update backend deps | `cd backend && uv pip install -e ".[dev]"` |
| Update frontend deps | `cd frontend && pnpm install` |

---

## 5. Environment reference

`backend/.env` (see `backend/.env.example` for the full list):

```
OPENAI_API_KEY=sk-...
EMBED_MODEL=text-embedding-3-large
CHAT_MODEL=gpt-4.1-mini
PARENT_TOKENS=2000
CHILD_TOKENS=300
CHILD_OVERLAP=50
DEFAULT_TOP_K=5
PARENT_CONSOLIDATION_THRESHOLD=3
CORS_ORIGINS=http://localhost:3000
```

`frontend/.env.local`:

```
NEXT_PUBLIC_API_BASE=http://localhost:8000
```

---

## 6. Troubleshooting

- **`OPENAI_API_KEY` unset** — upload works (extraction/chunking only),
  but `/query` fails with a 401. Fill in `backend/.env` and restart
  uvicorn.
- **`pnpm: command not found`** — install it globally (`npm install -g
  pnpm`) or via Corepack (`corepack enable pnpm`).
- **First upload is slow** — Docling downloads model weights on first
  run. Subsequent uploads are fast.
- **CORS error in browser console** — make sure `CORS_ORIGINS` in
  `backend/.env` matches the frontend origin (`http://localhost:3000`
  by default).
- **PDF highlights are offset** — try zooming to 100% and re-clicking
  the snippet; `react-pdf` reports dimensions that include the current
  scale, which the viewer accounts for.
