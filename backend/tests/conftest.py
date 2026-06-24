"""Test harness — binds the app to a throwaway SQLite DB and the console email
backend BEFORE any `app.*` import, so settings/engine pick up the test config.
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="lexisai-test-")
os.environ["SQLITE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["EMAIL_BACKEND"] = "console"
os.environ["JWT_SECRET"] = "test-secret-test-secret-test-secret-0123"
os.environ["OPENAI_API_KEY"] = "test-key-not-used"
os.environ["FRONTEND_URL"] = "http://localhost:3000"

import re  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import email as email_svc  # noqa: E402


@pytest.fixture(scope="session")
def client():
    # Context-manager form fires startup events (init_db -> tables + migration).
    with TestClient(app) as c:
        yield c


def _extract_token() -> str:
    """Pull the one-time token out of the most recently 'sent' email."""
    sent = email_svc.last_sent()
    assert sent is not None, "no email was captured"
    m = re.search(r"[?&]token=([A-Za-z0-9_\-]+)", sent["text"])
    assert m, f"no link token found in email: {sent}"
    return m.group(1)


@pytest.fixture
def link_token():
    return _extract_token


@pytest.fixture
def make_user(client):
    """Sign up + verify a user; returns (email, jwt) for an active account."""

    def _make(email: str, username: str, password: str = "Sup3r!secret"):
        r = client.post(
            "/auth/signup",
            json={"email": email, "username": username, "password": password},
        )
        assert r.status_code == 201, r.text
        r2 = client.post("/auth/verify", json={"token": _extract_token()})
        assert r2.status_code == 200, r2.text
        return email, r2.json()["token"]

    return _make


@pytest.fixture
def bearer():
    return lambda jwt: {"Authorization": f"Bearer {jwt}"}
