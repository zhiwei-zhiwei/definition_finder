"""Auth lifecycle: email verification, login gating, and password reset."""
import time

from app.services import email as email_svc


def test_signup_sends_verification_and_no_token(client):
    r = client.post(
        "/auth/signup",
        json={"email": "v1@example.com", "username": "verifyone", "password": "Sup3r!secret"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "verification_sent"
    assert "token" not in body  # account is NOT logged in at signup
    sent = email_svc.last_sent()
    assert sent is not None and "Verify" in sent["subject"]


def test_login_blocked_until_verified(client, link_token):
    client.post(
        "/auth/signup",
        json={"email": "gate@example.com", "username": "gatekeeper", "password": "Sup3r!secret"},
    )
    # Correct password, but unverified -> blocked with a distinct code.
    r = client.post("/auth/login", json={"email": "gate@example.com", "password": "Sup3r!secret"})
    assert r.status_code == 403
    assert r.json()["detail"] == "email_unverified"

    # After clicking the link, the same credentials work.
    client.post("/auth/verify", json={"token": link_token()})
    r2 = client.post("/auth/login", json={"email": "gate@example.com", "password": "Sup3r!secret"})
    assert r2.status_code == 200
    assert "token" in r2.json()


def test_verify_token_is_single_use(client, link_token):
    client.post(
        "/auth/signup",
        json={"email": "once@example.com", "username": "onlyonce", "password": "Sup3r!secret"},
    )
    tok = link_token()
    assert client.post("/auth/verify", json={"token": tok}).status_code == 200
    # Replaying the consumed token fails.
    assert client.post("/auth/verify", json={"token": tok}).status_code == 400


def test_password_reset_revokes_old_sessions(client, make_user, link_token, bearer):
    email, old_jwt = make_user("reset@example.com", "resetuser")
    assert client.get("/auth/me", headers=bearer(old_jwt)).json()["email"] == email

    # iat is whole-second; sleep so the reset lands in a later second and the
    # old token is unambiguously stale.
    time.sleep(1.1)

    assert client.post("/auth/password/forgot", json={"email": email}).status_code == 202
    rtok = link_token()
    rr = client.post("/auth/password/reset", json={"token": rtok, "password": "N3w!password"})
    assert rr.status_code == 200
    new_jwt = rr.json()["token"]

    # Old session is dead; new session lives.
    assert client.get("/auth/me", headers=bearer(old_jwt)).json() is None
    assert client.get("/auth/me", headers=bearer(new_jwt)).json()["email"] == email

    # Reset token is single-use.
    assert client.post(
        "/auth/password/reset", json={"token": rtok, "password": "An0ther!pw"}
    ).status_code == 400

    # New password authenticates.
    assert client.post(
        "/auth/login", json={"email": email, "password": "N3w!password"}
    ).status_code == 200


def test_reset_token_expiry(client, make_user, link_token, monkeypatch):
    email, _ = make_user("expire@example.com", "expireuser")
    import app.routes.auth as auth_routes

    # Mint an already-expired reset token.
    monkeypatch.setattr(auth_routes.settings, "RESET_TTL_SECONDS", -1)
    client.post("/auth/password/forgot", json={"email": email})
    rtok = link_token()
    assert client.post(
        "/auth/password/reset", json={"token": rtok, "password": "N3w!password"}
    ).status_code == 400


def test_forgot_is_generic_for_unknown_email(client):
    # Anti-enumeration: unknown email still returns 202 with no signal.
    r = client.post("/auth/password/forgot", json={"email": "nobody@example.com"})
    assert r.status_code == 202
