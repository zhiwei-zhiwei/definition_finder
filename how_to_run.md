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

# Install the exact versions in uv.lock (same as Docker and CI)
uv sync --extra dev

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
| Sync backend deps       | `cd backend && uv sync --extra dev` (after a pull that changed `uv.lock`)             |
| Add a backend dep       | edit `pyproject.toml`, then `uv lock && uv sync --extra dev`; commit both files       |
| Upgrade backend deps    | `uv lock --upgrade` (all) or `uv lock --upgrade-package docling`; run tests + gate     |
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

---

## 8. Docker（一条命令启动前后端）

```bash
# 仓库根目录；先停掉本地的 pnpm dev / uvicorn（会抢 3000 / 8000 端口）
docker compose up --build        # 打开 http://localhost:3000
docker compose down              # 停止（数据还在 data/ 里）
```

- 读取 `backend/.env` 作为运行时环境变量。密钥不会打进镜像，`.dockerignore` 排除了 `.env`。
- `./data` 挂载到容器里的 `/app/data`，和本地开发共用同一个数据库和同一批上传文件。
- Docling 模型（约 500 MB）缓存在 Docker 卷 `model-cache` 里，只在第一次处理文档时下载。
- 后端镜像约 1.65 GB（使用 CPU 版 torch），前端镜像约 156 MB（Next standalone）。
- 前端的 API 地址在构建时写进 JS。要改地址，修改 `docker-compose.yml` 里的 `NEXT_PUBLIC_API_BASE`，然后重新 `--build`。

### 可观测性（OpenTelemetry + Grafana，可选）

`lgtm` 服务（`grafana/otel-lgtm`）在一个容器里打包了 OTel Collector、Tempo（存 trace）、Prometheus（存指标）和 Grafana，只在加了 `--profile obs` 时才启动。启动后，看板 “LexisAI query graph” 会自动导入。

#### 方式 A：本地开发（常用）

1. 启动看板容器（仓库根目录）。第一次需要拉取约 930 MB 的镜像，之后几秒就能起来：
   ```bash
   docker compose --profile obs up -d lgtm
   ```
2. 启动后端时带上导出地址（在 `backend/` 下，venv 已激活）：
   ```bash
   OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uvicorn app.main:app --reload --port 8000
   ```
   如果想一直开着，可以把 `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318` 写进 `backend/.env`，以后照常启动即可。但看板容器没开的时候，后端会不停打印连接失败的警告。
3. 前端照常 `pnpm dev`，然后在页面上问几个问题。
4. 等约 10 秒（指标每 10 秒推送一次），打开看板：http://localhost:3002/d/lexisai
5. 想看单次查询的 trace：Grafana 左侧菜单 → Explore → 数据源选 Tempo → 切到 TraceQL，输入：
   - `{name="query.run"}`：所有查询
   - `{name="query.run" && span.outcome="error"}`：失败的查询
   - `{span.query.id="<query_id>"}`：某一次查询，包括它后来的 accept 或 recover

#### 方式 B：全部跑在 Docker 里

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://lgtm:4318 docker compose --profile obs up --build
```

注意地址是 `lgtm` 而不是 `localhost`：后端容器要通过 compose 的服务名找到看板容器。

#### 停止

```bash
docker compose --profile obs stop lgtm    # 只停止，下次 start 时数据还在
docker compose --profile obs down         # 删除容器，trace 和指标一起清空
```

#### 看板是空的？

- **后端启动时没带 `OTEL_EXPORTER_OTLP_ENDPOINT`**：没设地址时后端不导出任何数据，这是默认行为，CI 和平时开发因此不受影响。
- **刚查询完**：指标最多要等 10 秒才会推送过去。
- **时间范围不对**：右上角的时间范围要覆盖你查询的时间段，默认是最近 30 分钟。
- **改了 `observability/grafana/lexisai.json` 但没生效**：执行 `docker compose --profile obs restart lgtm` 让 Grafana 重新加载。

## 9. CI 与检索门禁

每次 push 都会触发 `.github/workflows/ci.yml`：

1. `ruff check app scripts tests`。ruff 版本锁在 `pyproject.toml` 里，因为新版会默认开启更多规则。
2. `pytest`
3. **检索门禁** `python scripts/eval_smoke.py`：在冻结的 AMD 10-K 数据（`tests/fixtures/retrieval_amd.npz`）上回放真实的 `hybrid_search` 和 `consolidate`。不调用 API，约 3 秒。任何一个指标低于 `GATES` 就 exit 1。
4. 前 3 步都通过后，构建后端和前端两个镜像，只构建不推送。

### 本地先跑一遍（在 `backend/` 下）

```bash
ruff check app scripts tests && pytest -q && python scripts/eval_smoke.py
```

### 门禁失败时

- 输出会列出每条丢失的查询（排名，以及答案是否在上下文里）。先确认这些是不是你的改动造成的。
- 如果确实是改进（指标变好），门禁不会失败。但要把 `eval_smoke.py` 里的 `GATES` 调高，阈值设为新值减 0.027（一条查询），不然以后指标回落时不会报警。

### 需要重新冻结 fixture 的情况

改了切块（`CHILD_TOKENS` / `PARENT_TOKENS`、chunker）、embedding 模型或标签文件时，门禁看不到这些变化，需要重新冻结：

```bash
# 约 3 分钟，不到 $0.05；之后按新数字重设 GATES，并提交新的 .npz
python scripts/freeze_retrieval_fixture.py --pdf ../AMD_2022_10K.pdf \
    --labels scripts/eval_labels/amd_2022_10k.json
python scripts/eval_smoke.py
```

门禁只覆盖检索。回答质量仍然要靠 `eval_grounding.py`，它要花钱，所以只手动运行。
