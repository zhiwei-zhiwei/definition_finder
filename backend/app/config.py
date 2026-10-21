from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    OPENAI_API_KEY: str = ""
    EMBED_MODEL: str = "text-embedding-3-large"
    CHAT_MODEL: str = "gpt-4.1-mini"
    # Optional Matryoshka truncation (OpenAI `dimensions`). None = model default
    # (3072 for text-embedding-3-large). Lower = smaller BLOBs / faster cosine,
    # at some recall cost. Query + stored vectors share this, so they stay aligned.
    EMBED_DIM: int | None = None

    # Contextual embeddings (Anthropic "Contextual Retrieval" technique): before
    # embedding, an LLM writes a 1-2 sentence blurb situating each chunk in its
    # document; that blurb is prepended to the chunk for both embedding and BM25.
    # Default OFF: on a single-topic lease (16-query eval) it gave no recall gain
    # and slightly hurt at mid-k — see RAG_IMPROVEMENT_REPORT.md §6. Set true to
    # A/B on a more diverse corpus.
    CONTEXTUAL_EMBEDDINGS: bool = False
    CONTEXT_MODEL: str = "gpt-4.1-mini"
    # Cap the document prefix fed to the context model (keeps cost bounded on
    # huge docs; the prefix is identical per doc so OpenAI auto-caches it).
    CONTEXT_MAX_DOC_TOKENS: int = 100_000

    PARENT_TOKENS: int = 1000
    CHILD_TOKENS: int = 120

    DEFAULT_TOP_K: int = 5
    # Cross-encoder reranker (Hugging Face model id, e.g. BAAI/bge-reranker-v2-m3); empty = off.
    # It rescores the top RERANK_CANDIDATES fused hits and keeps the top k.
    # Off by default: bge-reranker-v2-m3 cut grounding failures 12.9% -> 6.8% on the AMD 10-K but
    # costs ~5.6 s/query on an M1 (MPS); turn it on where it runs on a GPU (e.g. a TEI pod).
    RERANK_MODEL: str = ""
    RERANK_CANDIDATES: int = 50
    PARENT_CONSOLIDATION_THRESHOLD: int = 3
    # Evidence gate: below this best child cosine (vector arm, whole doc) the query graph answers
    # "not in the document" without calling the LLM; 0 = off. Tied to EMBED_MODEL
    # (text-embedding-3-large). Kept low on purpose: vague questions ("Give me an overview" 0.157,
    # "Summarize this document" 0.268) score like off-topic ones (AMD questions on the lease
    # 0.07-0.36), so 0.12 refuses none of 73 valid questions (53 labelled + 20 vague, min 0.157)
    # and only 10 of 60 cross-document ones. The answer-side refusal check catches the rest.
    EVIDENCE_MIN_COSINE: float = 0.12

    UPLOAD_DIR: str = "../data/uploads"
    CACHE_DIR: str = "../data/cache"
    SQLITE_URL: str = "sqlite:///../data/app.db"
    # LangGraph checkpoints (query graph runs paused for human review); own file so the
    # async checkpointer never contends with SQLAlchemy's writes to app.db.
    CHECKPOINT_DB: str = "../data/checkpoints.db"

    CORS_ORIGINS: str = "http://localhost:3000"

    # Dev default — RFC 7518 wants ≥32 bytes for HS256. Override via env in prod.
    JWT_SECRET: str = "dev-only-not-secret-change-me-please-32b"
    JWT_ALG: str = "HS256"
    JWT_EXP_SECONDS: int = 2_592_000  # 30 days

    CODE_LENGTH: int = 6
    CODE_TTL_SECONDS: int = 600  # 10 min
    CODE_RESEND_SECONDS: int = 60
    CODE_MAX_ATTEMPTS: int = 3

    # Email delivery. "console" logs links/codes via stdlib INFO (dev default,
    # nothing leaves the box). "resend" POSTs to the Resend HTTPS API.
    EMAIL_BACKEND: str = "console"  # "console" | "resend"
    RESEND_API_KEY: str = ""
    EMAIL_FROM: str = "LexisAI <onboarding@resend.dev>"

    # Base URL the verification / reset links point back to (the frontend).
    FRONTEND_URL: str = "http://localhost:3000"
    # One-time link lifetimes.
    VERIFY_TTL_SECONDS: int = 86_400  # 24h
    RESET_TTL_SECONDS: int = 1_800  # 30 min

    @property
    def upload_path(self) -> Path:
        p = Path(self.UPLOAD_DIR).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cache_path(self) -> Path:
        p = Path(self.CACHE_DIR).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


settings = Settings()
