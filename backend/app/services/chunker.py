import re
from dataclasses import dataclass, field

import tiktoken

from app.config import settings
from app.services.ingest import ExtractedItem


_ENC = tiktoken.get_encoding("cl100k_base")

# Split on sentence-ending punctuation followed by whitespace and an
# opening character. Avoids most abbreviation false-splits (Mr., U.S.).
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+(?=[A-Z"\'(\[])')


def _count_tokens(text: str) -> int:
    return len(_ENC.encode(text))


@dataclass
class ParentChunk:
    id: str
    doc_id: str
    text: str
    page_start: int | None = None
    page_end: int | None = None
    bboxes: list[dict] = field(default_factory=list)
    token_count: int = 0


@dataclass
class ChildChunk:
    id: str
    parent_id: str
    doc_id: str
    text: str
    page_start: int | None = None
    page_end: int | None = None
    bboxes: list[dict] = field(default_factory=list)
    html_anchor: str | None = None
    token_count: int = 0


def chunk_document(doc_id: str, items: list[ExtractedItem]) -> tuple[list[ParentChunk], list[ChildChunk]]:
    parents = _make_parents(doc_id, items)
    children: list[ChildChunk] = []
    for p in parents:
        children.extend(_make_children(p))
    return parents, children


def _doc_prefix(doc_id: str) -> str:
    return doc_id[:8]


def _make_parents(doc_id: str, items: list[ExtractedItem]) -> list[ParentChunk]:
    parents: list[ParentChunk] = []
    buf_items: list[ExtractedItem] = []
    buf_tokens = 0
    idx = 0
    prefix = _doc_prefix(doc_id)

    def flush():
        nonlocal buf_items, buf_tokens, idx
        if not buf_items:
            return
        idx += 1
        pid = f"{prefix}_Parent_{idx:05d}"
        text = "\n\n".join(i.text for i in buf_items)
        pages = [i.page for i in buf_items if i.page is not None]
        bboxes = [
            {"page": i.page, **i.bbox}
            for i in buf_items
            if i.bbox is not None and i.page is not None
        ]
        parents.append(
            ParentChunk(
                id=pid,
                doc_id=doc_id,
                text=text,
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                bboxes=bboxes,
                token_count=_count_tokens(text),
            )
        )
        buf_items = []
        buf_tokens = 0

    for item in items:
        t = _count_tokens(item.text)
        if buf_tokens + t > settings.PARENT_TOKENS and buf_items:
            flush()
        buf_items.append(item)
        buf_tokens += t
    flush()
    return parents


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_END.split(text)
    return [p.strip() for p in parts if p.strip()]


def _make_children(parent: ParentChunk) -> list[ChildChunk]:
    sentences = _split_sentences(parent.text)
    if not sentences:
        return []

    max_tokens = settings.CHILD_TOKENS
    children: list[ChildChunk] = []
    buf: list[str] = []
    buf_tokens = 0
    idx = 0

    def flush():
        nonlocal buf, buf_tokens, idx
        if not buf:
            return
        idx += 1
        # parent.id = "{prefix}_Parent_NNNNN"; child id reuses that body.
        cid = f"{parent.id.replace('Parent_', 'Child_')}_{idx:03d}"
        text = " ".join(buf)
        children.append(
            ChildChunk(
                id=cid,
                parent_id=parent.id,
                doc_id=parent.doc_id,
                text=text,
                page_start=parent.page_start,
                page_end=parent.page_end,
                bboxes=parent.bboxes,
                html_anchor=cid,
                token_count=buf_tokens,
            )
        )
        buf = []
        buf_tokens = 0

    for sent in sentences:
        t = _count_tokens(sent)
        # Oversized sentence: flush any pending buffer, emit the sentence
        # alone (even though it exceeds max_tokens).
        if t > max_tokens:
            flush()
            buf = [sent]
            buf_tokens = t
            flush()
            continue
        if buf_tokens + t > max_tokens and buf:
            flush()
        buf.append(sent)
        buf_tokens += t
    flush()
    return children
