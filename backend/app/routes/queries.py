import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models.db import get_db
from app.models.schema import Query, User
from app.services.auth import get_current_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/queries", tags=["queries"])


@router.get("")
def list_queries(
    doc_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = (
        db.query(Query)
        .filter(Query.user_id == user.id, Query.doc_id == doc_id)
        .order_by(Query.created_at.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "query_text": r.query_text,
            "key_takeaway": r.key_takeaway,
            "top_k": r.top_k,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/{query_id}")
def get_query(
    query_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    r = db.get(Query, query_id)
    if r is None or r.user_id != user.id:
        raise HTTPException(404, "not found")
    try:
        snippets = json.loads(r.snippets_json)
    except Exception:
        snippets = []
    return {
        "id": r.id,
        "doc_id": r.doc_id,
        "query_text": r.query_text,
        "top_k": r.top_k,
        "snippets": snippets,
        "summary_text": r.summary_text,
        "key_takeaway": r.key_takeaway,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
