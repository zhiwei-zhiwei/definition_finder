"""Per-user access control on document/query endpoints."""
import uuid

from app.models.db import SessionLocal
from app.models.schema import Document


def _insert_doc(user_id: str | None) -> str:
    doc_id = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(
            Document(
                id=doc_id,
                filename="x.pdf",
                mime="application/pdf",
                size_bytes=1,
                status="ready",
                user_id=user_id,
            )
        )
        db.commit()
    return doc_id


def test_owned_doc_blocks_other_users(client, make_user, bearer):
    _, a_jwt = make_user("doc-a@example.com", "docowner_a")
    _, b_jwt = make_user("doc-b@example.com", "docowner_b")
    a_id = client.get("/auth/me", headers=bearer(a_jwt)).json()["user_id"]
    doc_id = _insert_doc(a_id)

    # Owner can read metadata + HTML render.
    assert client.get(f"/documents/{doc_id}", headers=bearer(a_jwt)).status_code == 200
    assert client.get(f"/documents/{doc_id}/html", headers=bearer(a_jwt)).status_code == 200

    # A different signed-in user is forbidden everywhere.
    assert client.get(f"/documents/{doc_id}", headers=bearer(b_jwt)).status_code == 403
    assert client.get(f"/documents/{doc_id}/html", headers=bearer(b_jwt)).status_code == 403
    assert client.get(f"/documents/{doc_id}/file", headers=bearer(b_jwt)).status_code == 403

    # Anonymous (no token) is forbidden for an owned doc.
    assert client.get(f"/documents/{doc_id}").status_code == 403


def test_query_rejects_non_owner(client, make_user, bearer):
    _, a_jwt = make_user("q-a@example.com", "queryowner_a")
    _, b_jwt = make_user("q-b@example.com", "queryowner_b")
    a_id = client.get("/auth/me", headers=bearer(a_jwt)).json()["user_id"]
    doc_id = _insert_doc(a_id)

    # The stream emits a 'forbidden' error event before any retrieval/LLM work.
    r = client.post(
        "/query", json={"doc_id": doc_id, "query": "hello"}, headers=bearer(b_jwt)
    )
    assert "forbidden" in r.text


def test_anonymous_doc_is_publicly_readable(client):
    # Unclaimed (user_id NULL) uploads stay readable by whoever holds the id.
    doc_id = _insert_doc(None)
    assert client.get(f"/documents/{doc_id}").status_code == 200
