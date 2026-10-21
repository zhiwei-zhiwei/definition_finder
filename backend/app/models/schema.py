from datetime import datetime

from sqlalchemy import ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # Lowercase-on-write contract; SQLite has no citext.
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    # Null for users that have only ever logged in via email code.
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    # False until the user clicks the verification link emailed at signup.
    email_verified: Mapped[bool] = mapped_column(default=False, nullable=False)
    # Bumped on password reset — JWTs issued before this are rejected, so a
    # reset revokes every outstanding session (see auth.get_current_user_optional).
    password_changed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    mime: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String, nullable=False, default="ingesting")
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    parents: Mapped[list["ParentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    children: Mapped[list["ChildChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class ParentChunk(Base):
    __tablename__ = "parent_chunks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # text with "[page N]" at page breaks, sent to the summarizer; NULL for non-PDFs / older docs
    marked_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    document: Mapped[Document] = relationship(back_populates="parents")
    children: Mapped[list["ChildChunk"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )


class ChildChunk(Base):
    __tablename__ = "child_chunks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    parent_id: Mapped[str] = mapped_column(ForeignKey("parent_chunks.id", ondelete="CASCADE"), index=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # LLM-generated blurb situating this child's parent in the document. Prepended
    # to `text` for embedding + BM25 (contextual retrieval); never shown to the user.
    context_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Contextualized-text embedding, packed as little-endian float32 bytes. The
    # vector store: brute-force cosine over these per-doc replaces Chroma.
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    bboxes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_anchor: Mapped[str | None] = mapped_column(String, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)

    parent: Mapped[ParentChunk] = relationship(back_populates="children")
    document: Mapped[Document] = relationship(back_populates="children")


class EmailCode(Base):
    __tablename__ = "email_codes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # Keyed by email (not user_id) so the table works pre-account if signup
    # ever needs verification. Lowercase contract enforced at insert time.
    email: Mapped[str] = mapped_column(String, index=True, nullable=False)
    # For "login" this holds the 6-digit OTP hash; for "verify"/"reset" it
    # holds the bcrypt hash of a URL-safe link token.
    code_hash: Mapped[str] = mapped_column(String, nullable=False)
    purpose: Mapped[str] = mapped_column(String, nullable=False)  # "login" | "verify" | "reset"
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Query(Base):
    __tablename__ = "queries"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    doc_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False)
    snippets_json: Mapped[str] = mapped_column(Text, nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    key_takeaway: Mapped[str | None] = mapped_column(Text, nullable=True)
    # query_graph's verification result (status / issues / needs_review); NULL on rows cached before it
    verification_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "pending" while the run waits in query_graph's review node, then "accepted" or "rejected";
    # NULL if never flagged
    review_status: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
