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
from app.models.schema import Document
from app.services import vectorstore

log = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("")
async def upload_document(file: UploadFile = File(...)):
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
def list_documents(db: Session = Depends(get_db)):
    rows = db.query(Document).order_by(Document.uploaded_at.desc()).limit(50).all()
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
def get_document(doc_id: str, db: Session = Depends(get_db)):
    r = db.get(Document, doc_id)
    if r is None:
        raise HTTPException(404, "not found")
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
def download_file(doc_id: str, db: Session = Depends(get_db)):
    r = db.get(Document, doc_id)
    if r is None:
        raise HTTPException(404, "not found")
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
def get_html_render(doc_id: str):
    path = settings.cache_path / f"{doc_id}.html"
    if not path.exists():
        return HTMLResponse(_HTML_PLACEHOLDER)
    return HTMLResponse(path.read_text(encoding="utf-8"))


@router.delete("/{doc_id}")
def delete_document(doc_id: str, db: Session = Depends(get_db)):
    r = db.get(Document, doc_id)
    if r is None:
        raise HTTPException(404, "not found")
    ext = Path(r.filename).suffix
    file_path = settings.upload_path / f"{doc_id}{ext}"
    cache_path = settings.cache_path / f"{doc_id}.html"
    for p in (file_path, cache_path):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
    vectorstore.delete_collection(doc_id)
    db.delete(r)
    db.commit()
    return JSONResponse({"ok": True})
