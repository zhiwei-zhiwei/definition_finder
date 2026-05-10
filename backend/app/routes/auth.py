import logging
import re
import string
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.models.db import get_db
from app.models.schema import Document, EmailCode, User
from app.services.auth import (
    create_token,
    generate_code,
    get_current_user,
    get_current_user_optional,
    hash_code,
    hash_password,
    normalize_email,
    verify_code,
    verify_password,
)
from app.services.email import send_code as email_send_code

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")
# Pragmatic email check — full RFC 5322 isn't worth the dependency for a
# local-first app. Server normalizes to lowercase before storing.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SYMBOLS = set(string.punctuation)


def _validate_email(v: str) -> str:
    v = v.strip()
    if not _EMAIL_RE.match(v):
        raise ValueError("invalid email")
    return v


def _validate_signup_password(v: str) -> str:
    # Server is the source of truth — clients can be backdoored. Login routes
    # don't call this so existing users with legacy passwords keep signing in.
    if not any(c.isupper() for c in v):
        raise ValueError("password_weak")
    if not any(c in _SYMBOLS for c in v):
        raise ValueError("password_weak")
    return v


class SignupRequest(BaseModel):
    email: str
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=8, max_length=200)

    _v_email = field_validator("email")(_validate_email)
    _v_password = field_validator("password")(_validate_signup_password)


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=200)

    _v_email = field_validator("email")(_validate_email)


class ClaimRequest(BaseModel):
    doc_ids: list[str]


class CodeRequest(BaseModel):
    email: str
    _v_email = field_validator("email")(_validate_email)


class CodeVerifyRequest(BaseModel):
    email: str
    code: str = Field(min_length=1, max_length=12)
    _v_email = field_validator("email")(_validate_email)


def _issue_token(user: User) -> dict:
    return {
        "user_id": user.id,
        "email": user.email,
        "username": user.username,
        "token": create_token(user.id),
    }


@router.post("/signup", status_code=201)
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    if not _USERNAME_RE.match(req.username):
        raise HTTPException(422, "username must be 3-20 chars, [A-Za-z0-9_-]")
    email = normalize_email(req.email)

    if db.query(User).filter(User.email == email).one_or_none() is not None:
        raise HTTPException(409, "email_taken")
    if db.query(User).filter(User.username == req.username).one_or_none() is not None:
        raise HTTPException(409, "username_taken")

    user = User(
        id=uuid.uuid4().hex,
        email=email,
        username=req.username,
        password_hash=hash_password(req.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _issue_token(user)


@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    email = normalize_email(req.email)
    user = db.query(User).filter(User.email == email).one_or_none()
    # verify_password runs a dummy hash compare on missing user/null hash to
    # flatten timing — don't short-circuit before the verify call.
    ok = verify_password(req.password, user.password_hash if user else None)
    if not ok or user is None:
        raise HTTPException(401, "invalid_credentials")
    return _issue_token(user)


@router.get("/me")
def me(user: User | None = Depends(get_current_user_optional)):
    if user is None:
        return None
    return {"user_id": user.id, "email": user.email, "username": user.username}


@router.post("/logout")
def logout():
    # Stateless JWT — client drops the token. Endpoint exists for symmetry.
    return {"ok": True}


@router.post("/email-code/request", status_code=202)
def request_code(req: CodeRequest, db: Session = Depends(get_db)):
    email = normalize_email(req.email)
    user = db.query(User).filter(User.email == email).one_or_none()
    # Pragmatic UX over enumeration paranoia for this single-user local app:
    # if the email isn't registered, surface it so the modal can offer signup.
    if user is None:
        raise HTTPException(404, "no_account")

    now = datetime.utcnow()
    # Throttle resend by checking the most-recent outstanding code for this email.
    last = (
        db.query(EmailCode)
        .filter(
            EmailCode.email == email,
            EmailCode.consumed_at.is_(None),
        )
        .order_by(EmailCode.created_at.desc())
        .first()
    )
    if last is not None and (now - last.created_at).total_seconds() < settings.CODE_RESEND_SECONDS:
        wait = settings.CODE_RESEND_SECONDS - int((now - last.created_at).total_seconds())
        raise HTTPException(429, f"resend_throttle:{wait}")

    # Invalidate any prior outstanding codes — only the freshest one is valid.
    db.query(EmailCode).filter(
        EmailCode.email == email, EmailCode.consumed_at.is_(None)
    ).update({"consumed_at": now}, synchronize_session=False)

    code_plain = generate_code()
    row = EmailCode(
        id=uuid.uuid4().hex,
        email=email,
        code_hash=hash_code(code_plain),
        purpose="login",
        created_at=now,
        expires_at=now + timedelta(seconds=settings.CODE_TTL_SECONDS),
        attempts=0,
    )
    db.add(row)
    db.commit()
    email_send_code(email, code_plain, purpose="login")
    return {"ok": True}


@router.post("/email-code/verify")
def verify_code_route(req: CodeVerifyRequest, db: Session = Depends(get_db)):
    email = normalize_email(req.email)
    code = req.code.strip()

    now = datetime.utcnow()
    row = (
        db.query(EmailCode)
        .filter(
            EmailCode.email == email,
            EmailCode.consumed_at.is_(None),
        )
        .order_by(EmailCode.created_at.desc())
        .first()
    )
    if row is None or row.expires_at < now:
        # Mark expired rows consumed so a stale code can't be retried later.
        if row is not None:
            row.consumed_at = now
            db.commit()
        raise HTTPException(401, "invalid_code")

    if not verify_code(code, row.code_hash):
        row.attempts += 1
        if row.attempts >= settings.CODE_MAX_ATTEMPTS:
            row.consumed_at = now
        db.commit()
        raise HTTPException(401, "invalid_code")

    # Success — burn the code and issue a token.
    row.consumed_at = now
    db.commit()

    user = db.query(User).filter(User.email == email).one_or_none()
    if user is None:
        # User deleted their account between request and verify. Edge case;
        # surface as a generic invalid_code to keep the response shape.
        raise HTTPException(401, "invalid_code")
    return _issue_token(user)


@router.post("/claim")
def claim(
    req: ClaimRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # TODO: harden against doc-id squatting by verifying a server-issued
    # claim token at upload time. Realistic risk is low (uuid4 hex doc ids
    # in sessionStorage), so deferred.
    if not req.doc_ids:
        return {"claimed": []}
    rows = (
        db.query(Document)
        .filter(Document.id.in_(req.doc_ids), Document.user_id.is_(None))
        .all()
    )
    claimed: list[str] = []
    for r in rows:
        r.user_id = user.id
        claimed.append(r.id)
    db.commit()
    return {"claimed": claimed}
