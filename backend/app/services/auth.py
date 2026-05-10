"""Auth helpers — single swap point for production hardening.

The dependencies returned by `get_current_user_optional` / `get_current_user`
hand out `User` objects, so callers never inspect tokens or claims directly.
That keeps `routes/documents.py`, `routes/query.py`, etc. unchanged when this
file evolves (e.g. revocation, refresh tokens, OAuth).
"""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.models.db import get_db
from app.models.schema import User

log = logging.getLogger(__name__)

_PWD_ROUNDS = 12
_CODE_ROUNDS = 10  # codes are short-lived — high rounds aren't worth the latency


def _hash(plain: str, rounds: int) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=rounds)).decode("utf-8")


def _verify(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# Used to flatten timing on unknown-email paths so an attacker can't distinguish
# "user exists, wrong password" from "user does not exist" by response time.
_DUMMY_PASSWORD_HASH = _hash("not-a-real-password", _PWD_ROUNDS)


def normalize_email(s: str) -> str:
    return s.strip().lower()


def hash_password(plain: str) -> str:
    return _hash(plain, _PWD_ROUNDS)


def verify_password(plain: str, hashed: str | None) -> bool:
    # Always run bcrypt against a dummy hash if `hashed` is None — this keeps
    # response time roughly constant whether or not the user exists.
    target = hashed or _DUMMY_PASSWORD_HASH
    ok = _verify(plain, target)
    return ok and hashed is not None


def hash_code(plain: str) -> str:
    return _hash(plain, _CODE_ROUNDS)


def verify_code(plain: str, hashed: str) -> bool:
    return _verify(plain, hashed)


def generate_code() -> str:
    n = secrets.randbelow(10 ** settings.CODE_LENGTH)
    return str(n).zfill(settings.CODE_LENGTH)


def create_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.JWT_EXP_SECONDS)).timestamp()),
        "jti": uuid.uuid4().hex,  # ready for a future revoked_jtis table
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)


def decode_token(tok: str) -> str | None:
    try:
        payload = jwt.decode(tok, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None


def get_current_user_optional(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    uid = decode_token(authorization[7:])
    if not uid:
        return None
    return db.get(User, uid)


def get_current_user(user: User | None = Depends(get_current_user_optional)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="auth required")
    return user
