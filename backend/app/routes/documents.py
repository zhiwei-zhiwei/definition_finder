import logging
import mimetypes
import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.models.db import SessionLocal, get_db
from app.models.schema import Document, User
from app.services.auth import get_current_user_optional, resolve_user_from_token

log = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


def _authorize_doc(doc: Document, user: User | None) -> None:
    """Owned docs require the matching signed-in user. Anonymous (unclaimed,
    user_id IS NULL) uploads stay readable by whoever holds the id until they're
    claimed on sign-in. Raises 403 otherwise."""
    if doc.user_id is not None and (user is None or doc.user_id != user.id):
        raise HTTPException(403, "not yours")


@router.post("")
async def upload_document(
    file: UploadFile = File(...),
    user: User | None = Depends(get_current_user_optional),
):
    doc_id = uuid.uuid4().hex
    ext = Path(file.filename or "").suffix
    stored_path = settings.upload_path / f"{doc_id}{ext}"

    size = 0
    async with aiofiles.open(stored_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            await f.write(chunk)

    mime = file.content_type or mimetypes.guess_type(str(stored_path))[0] or "application/octet-stream"

    with SessionLocal() as db:
        doc = Document(
            id=doc_id,
            filename=file.filename or stored_path.name,
            mime=mime,
            size_bytes=size,
            status="ready",
            user_id=user.id if user is not None else None,
        )
        db.add(doc)
        db.commit()
        uploaded_at = doc.uploaded_at.isoformat() if doc.uploaded_at else None

    return JSONResponse(
        {
            "id": doc_id,
            "filename": file.filename or stored_path.name,
            "mime": mime,
            "size_bytes": size,
            "pages": None,
            "uploaded_at": uploaded_at,
            "status": "ready",
            "file_url": f"/documents/{doc_id}/file",
            "html_url": f"/documents/{doc_id}/html",
        }
    )


@router.get("")
def list_documents(
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    if user is None:
        return []
    rows = (
        db.query(Document)
        .filter(Document.user_id == user.id)
        .order_by(Document.uploaded_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": r.id,
            "filename": r.filename,
            "mime": r.mime,
            "size_bytes": r.size_bytes,
            "pages": r.pages,
            "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
            "status": r.status,
        }
        for r in rows
    ]


@router.get("/{doc_id}")
def get_document(
    doc_id: str,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    r = db.get(Document, doc_id)
    if r is None:
        raise HTTPException(404, "not found")
    _authorize_doc(r, user)
    return {
        "id": r.id,
        "filename": r.filename,
        "mime": r.mime,
        "size_bytes": r.size_bytes,
        "pages": r.pages,
        "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
        "status": r.status,
        "file_url": f"/documents/{r.id}/file",
        "html_url": f"/documents/{r.id}/html",
    }


@router.get("/{doc_id}/file")
def download_file(
    doc_id: str,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
    token: str | None = None,
):
    r = db.get(Document, doc_id)
    if r is None:
        raise HTTPException(404, "not found")
    # The PDF viewer loads this URL in an <iframe>, which can't attach an
    # Authorization header — fall back to the JWT passed as ?token=.
    if user is None and token:
        user = resolve_user_from_token(token, db)
    _authorize_doc(r, user)
    ext = Path(r.filename).suffix
    path = settings.upload_path / f"{doc_id}{ext}"
    if not path.exists():
        raise HTTPException(404, "file missing")
    return FileResponse(
        path,
        media_type=r.mime,
        headers={"Content-Disposition": f'inline; filename="{r.filename}"'},
    )


_HTML_PLACEHOLDER = (
    "<div style='padding:3rem;color:#6b7280;font-family:system-ui;text-align:center'>"
    "<p style='font-size:0.95rem'>This document will be rendered after your first analysis.</p>"
    "<p style='font-size:0.8rem;opacity:0.7'>Ask a question in the Analyze panel to prepare it.</p>"
    "</div>"
)


@router.get("/{doc_id}/html", response_class=HTMLResponse)
def get_html_render(
    doc_id: str,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
    token: str | None = None,
):
    r = db.get(Document, doc_id)
    if r is None:
        raise HTTPException(404, "not found")
    if user is None and token:
        user = resolve_user_from_token(token, db)
    _authorize_doc(r, user)
    path = settings.cache_path / f"{doc_id}.html"
    if not path.exists():
        return HTMLResponse(_HTML_PLACEHOLDER)
    return HTMLResponse(path.read_text(encoding="utf-8"))


@router.delete("/{doc_id}")
def delete_document(
    doc_id: str,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    r = db.get(Document, doc_id)
    if r is None:
        raise HTTPException(404, "not found")
    _authorize_doc(r, user)
    ext = Path(r.filename).suffix
    file_path = settings.upload_path / f"{doc_id}{ext}"
    cache_path = settings.cache_path / f"{doc_id}.html"
    for p in (file_path, cache_path):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
    # Embeddings live on child_chunks; the cascade delete below removes them.
    db.delete(r)
    db.commit()
    return JSONResponse({"ok": True})
