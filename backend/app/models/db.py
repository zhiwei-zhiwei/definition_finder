from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _resolve_sqlite_url(url: str) -> str:
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
        rel = url.removeprefix("sqlite:///")
        abs_path = Path(rel).resolve()
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{abs_path}"
    return url


engine = create_engine(
    _resolve_sqlite_url(settings.SQLITE_URL),
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_columns() -> None:
    """Tiny idempotent migration: create_all() never ALTERs existing tables, so
    columns added after a DB was first created must be patched in by hand. Keep
    this list append-only and column-additive (SQLite ALTER can't drop/alter).

    `email_verified` defaults to 1 so pre-existing users are grandfathered as
    verified rather than locked out (matches the legacy-grandfathering policy in
    routes/auth.py). New rows still default to False via the ORM model.
    """
    additions = {
        "users": [
            ("email_verified", "BOOLEAN NOT NULL DEFAULT 1"),
            ("password_changed_at", "DATETIME"),
        ],
        "child_chunks": [
            ("context_text", "TEXT"),
            ("embedding", "BLOB"),
        ],
        "parent_chunks": [
            ("marked_text", "TEXT"),
        ],
    }
    with engine.begin() as conn:
        for table, cols in additions.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for name, ddl in cols:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def init_db() -> None:
    from app.models import schema  # noqa: F401 — register models

    Base.metadata.create_all(bind=engine)
    _ensure_columns()
