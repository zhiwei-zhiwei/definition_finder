from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.db import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    mime: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String, nullable=False, default="ingesting")

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
    bboxes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_anchor: Mapped[str | None] = mapped_column(String, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)

    parent: Mapped[ParentChunk] = relationship(back_populates="children")
    document: Mapped[Document] = relationship(back_populates="children")
