# How to run LexisAI Architect

This project has two isolated parts that run side by side:

- **backend/** — Python 3.11 + FastAPI, managed by `uv` in its own `.venv`
- **frontend/** — Next.js 14 + TypeScript, managed by `pnpm`

Each has its own dependency store; nothing is installed globally.

---

## 0. Prerequisites

Install once on your machine (the project itself stays isolated):

| Tool         | Install                                            |
| ------------ | -------------------------------------------------- |
| Python 3.11  | `brew install python@3.11` or pyenv                |
| `uv`         | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node.js ≥ 18 | `brew install node`                                |
| `pnpm`       | `npm install -g pnpm` (or `corepack enable pnpm`)  |

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

| Task                    | Command                                                                               |
| ----------------------- | ------------------------------------------------------------------------------------- |
| Run backend tests       | `cd backend && source .venv/bin/activate && pytest`                                   |
| Build frontend for prod | `cd frontend && pnpm build && pnpm start`                                             |
| Wipe all local data     | `rm -rf data/chroma data/uploads data/cache data/app.db`                              |
| Delete a specific doc   | use the drawer's trash icon, or `curl -X DELETE http://localhost:8000/documents/<id>` |
| Update backend deps     | `cd backend && uv pip install -e ".[dev]"`                                            |
| Update frontend deps    | `cd frontend && pnpm install`                                                         |

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
# RERANK_MODEL=BAAI/bge-reranker-v2-m3   # optional reranker, off by default (see section 7)

# Email (account verification + password reset)
EMAIL_BACKEND=console          # "console" logs links to the terminal; "resend" delivers for real
RESEND_API_KEY=                # required when EMAIL_BACKEND=resend
EMAIL_FROM=LexisAI <onboarding@resend.dev>
FRONTEND_URL=http://localhost:3000   # base URL the verify/reset links point to
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
- **Signup says "check your email" but nothing arrives** — with the
  default `EMAIL_BACKEND=console`, the verification (and password-reset)
  link is **printed in the backend terminal**, not emailed. Copy it from
  there and open it. For real delivery set `EMAIL_BACKEND=resend` plus
  `RESEND_API_KEY` / `EMAIL_FROM` and restart uvicorn. New accounts can't
  sign in until the link is clicked (`403 email_unverified` otherwise).
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

---

## 7. Reranker（交叉编码器重排，可选，默认关闭）

在混合检索（向量 + BM25，RRF 融合）之后，可选用交叉编码器把前 50 个候选重新打分排序，再取前 k 个。代码在 `backend/app/services/rerank.py`。

### 开启 / 关闭

1. 打开 `backend/.env`，找到这一行，删掉开头的 `# `：
   ```
   # RERANK_MODEL=BAAI/bge-reranker-v2-m3
   ```
2. 重启后端：`uvicorn app.main:app --reload --port 8000`（在 `backend/` 下，venv 已激活）。
3. 关闭：把 `# ` 加回去，再重启。

- 不需要重新上传或重新处理文档：重排只在查询时运行。
- 第一次查询会下载模型（约 2.2 GB，存到 `~/.cache/huggingface`），加载约 10–40 秒；之后每次查询约 5.6 秒（M1 Pro，MPS）。
- 在 GPU/MPS 上用 fp16 加载，占约 7.7 GB 内存。fp32 会占 14 GB，在 16 GB 的 Mac 上会用到交换内存，变得很慢。

### 设置项（`backend/.env`）

| 变量                | 默认       | 作用                                                 |
| ------------------- | ---------- | ---------------------------------------------------- |
| `RERANK_MODEL`      | 空（关闭） | Hugging Face 模型 id，推荐 `BAAI/bge-reranker-v2-m3` |
| `RERANK_CANDIDATES` | `50`       | 重排的候选数；调小更快，但可能漏掉排名靠后的正确段落 |

不要用 `BAAI/bge-reranker-base`：在 AMD 10-K 上，k ≤ 10 时它比不用重排还差。

### 效果（AMD 2022 10-K，2026-10-18）

| 指标                         |                不重排 |            v2-m3 重排 |
| ---------------------------- | --------------------: | --------------------: |
| Pass@1 / @5 / @10            | 51.4% / 78.4% / 81.1% | 62.2% / 81.1% / 91.9% |
| MRR                          |                 0.605 |                 0.707 |
| 回答失败率（grounding eval） |                 12.9% |                  6.8% |
| 检索延迟 p50（M1 Pro）       |                0.3 秒 |                5.9 秒 |

### 重新测量（在 `backend/` 下）

```bash
# 检索：不重排 vs 重排，输出 Pass@k 和延迟表，记录到 MLflow 实验 eval-amd_2022_10k（不到 $0.10，约 7 分钟）
python scripts/eval_retrieval.py --pdf ../AMD_2022_10K.pdf \
    --labels scripts/eval_labels/amd_2022_10k.json --top-k 20 --rerank BAAI/bge-reranker-v2-m3

# 端到端回答质量：开启重排后跑 grounding eval（约 $2.50，约 30 分钟）
RERANK_MODEL=BAAI/bge-reranker-v2-m3 python scripts/eval_grounding.py --pdf ../AMD_2022_10K.pdf \
    --labels scripts/eval_labels/amd_2022_10k.json --reps 3 --experiment grounding-amd-10k
```

查看结果：`mlflow ui --backend-store-uri sqlite:///../data/mlflow/mlflow.db --port 5055`。

### 以后部署到 Kubernetes

`rerank.score()` 的输入输出与 Hugging Face Text Embeddings Inference（TEI）的 `POST /rerank` 相同。部署时用 TEI 镜像加 `--model-id BAAI/bge-reranker-v2-m3` 起一个 GPU pod，再把 `score()` 的函数体换成对它的 HTTP 调用（约 8 行）即可。在 GPU 上预计约 50–100 毫秒/查询（估算，未实测）。
