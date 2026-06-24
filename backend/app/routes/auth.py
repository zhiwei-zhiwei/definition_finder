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
    generate_link_token,
    get_current_user,
    get_current_user_optional,
    hash_code,
    hash_password,
    hash_token,
    normalize_email,
    verify_code,
    verify_password,
)
from app.services.email import (
    send_code as email_send_code,
)
from app.services.email import (
    send_reset_link,
    send_verification_link,
)

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


class TokenRequest(BaseModel):
    token: str = Field(min_length=10, max_length=400)


class ResetRequest(BaseModel):
    token: str = Field(min_length=10, max_length=400)
    password: str = Field(min_length=8, max_length=200)

    _v_password = field_validator("password")(_validate_signup_password)


def _issue_token(user: User) -> dict:
    return {
        "user_id": user.id,
        "email": user.email,
        "username": user.username,
        "token": create_token(user.id),
    }


def _issue_link_token(db: Session, email: str, purpose: str, ttl_seconds: int) -> str:
    """Mint a one-time link token for verify/reset, store only its hash, and
    invalidate any prior outstanding token of the same purpose for this email."""
    now = datetime.utcnow()
    db.query(EmailCode).filter(
        EmailCode.email == email,
        EmailCode.purpose == purpose,
        EmailCode.consumed_at.is_(None),
    ).update({"consumed_at": now}, synchronize_session=False)

    token = generate_link_token()
    row = EmailCode(
        id=uuid.uuid4().hex,
        email=email,
        code_hash=hash_token(token),
        purpose=purpose,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
        attempts=0,
    )
    db.add(row)
    db.commit()
    return token


def _recently_sent(db: Session, email: str, purpose: str) -> bool:
    """True if a token of this purpose was minted within the resend window —
    used to throttle email sends silently (no enumeration signal)."""
    last = (
        db.query(EmailCode)
        .filter(EmailCode.email == email, EmailCode.purpose == purpose)
        .order_by(EmailCode.created_at.desc())
        .first()
    )
    if last is None:
        return False
    return (datetime.utcnow() - last.created_at).total_seconds() < settings.CODE_RESEND_SECONDS


def _consume_link_token(db: Session, token: str, purpose: str) -> User | None:
    """Validate a verify/reset token and return its user, or None if the token
    is unknown/expired/already used. Burns the row on any terminal outcome."""
    now = datetime.utcnow()
    row = (
        db.query(EmailCode)
        .filter(
            EmailCode.purpose == purpose,
            EmailCode.code_hash == hash_token(token),
            EmailCode.consumed_at.is_(None),
        )
        .first()
    )
    if row is None:
        return None
    if row.expires_at < now:
        row.consumed_at = now
        db.commit()
        return None
    user = db.query(User).filter(User.email == row.email).one_or_none()
    row.consumed_at = now
    db.commit()
    return user


def _send_verification(db: Session, email: str) -> None:
    token = _issue_link_token(db, email, "verify", settings.VERIFY_TTL_SECONDS)
    link = f"{settings.FRONTEND_URL}/verify?token={token}"
    send_verification_link(email, link)


@router.post("/signup", status_code=201)
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    if not _USERNAME_RE.match(req.username):
        raise HTTPException(422, "username must be 3-20 chars, [A-Za-z0-9_-]")
    email = normalize_email(req.email)

    existing = db.query(User).filter(User.email == email).one_or_none()
    if existing is not None and existing.email_verified:
        raise HTTPException(409, "email_taken")

    # Username must be free (ignoring a same-email pending row we may reuse).
    clash = db.query(User).filter(User.username == req.username).one_or_none()
    if clash is not None and (existing is None or clash.id != existing.id):
        raise HTTPException(409, "username_taken")

    if existing is not None:
        # Pending (unverified) account — let the user fix a typo'd username or
        # password and resend the link instead of bouncing them with a 409.
        existing.username = req.username
        existing.password_hash = hash_password(req.password)
        db.commit()
    else:
        user = User(
            id=uuid.uuid4().hex,
            email=email,
            username=req.username,
            password_hash=hash_password(req.password),
            email_verified=False,
        )
        db.add(user)
        db.commit()

    _send_verification(db, email)
    # No token: the account is inactive until the emailed link is clicked.
    return {"status": "verification_sent", "email": email}


@router.post("/verify")
def verify_email(req: TokenRequest, db: Session = Depends(get_db)):
    user = _consume_link_token(db, req.token, "verify")
    if user is None:
        raise HTTPException(400, "invalid_token")
    if not user.email_verified:
        user.email_verified = True
        db.commit()
    db.refresh(user)
    # Clicking the link proves email ownership — sign them in.
    return _issue_token(user)


@router.post("/verify/resend", status_code=202)
def resend_verification(req: CodeRequest, db: Session = Depends(get_db)):
    email = normalize_email(req.email)
    user = db.query(User).filter(User.email == email).one_or_none()
    # Generic response regardless of account state — no enumeration signal.
    if user is not None and not user.email_verified and not _recently_sent(db, email, "verify"):
        _send_verification(db, email)
    return {"ok": True}


@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    email = normalize_email(req.email)
    user = db.query(User).filter(User.email == email).one_or_none()
    # verify_password runs a dummy hash compare on missing user/null hash to
    # flatten timing — don't short-circuit before the verify call.
    ok = verify_password(req.password, user.password_hash if user else None)
    if not ok or user is None:
        raise HTTPException(401, "invalid_credentials")
    if not user.email_verified:
        raise HTTPException(403, "email_unverified")
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
    if not user.email_verified:
        raise HTTPException(403, "email_unverified")

    now = datetime.utcnow()
    # Throttle resend by checking the most-recent outstanding login code.
    last = (
        db.query(EmailCode)
        .filter(
            EmailCode.email == email,
            EmailCode.purpose == "login",
            EmailCode.consumed_at.is_(None),
        )
        .order_by(EmailCode.created_at.desc())
        .first()
    )
    if last is not None and (now - last.created_at).total_seconds() < settings.CODE_RESEND_SECONDS:
        wait = settings.CODE_RESEND_SECONDS - int((now - last.created_at).total_seconds())
        raise HTTPException(429, f"resend_throttle:{wait}")

    # Invalidate any prior outstanding login codes — only the freshest one is valid.
    db.query(EmailCode).filter(
        EmailCode.email == email,
        EmailCode.purpose == "login",
        EmailCode.consumed_at.is_(None),
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
            EmailCode.purpose == "login",
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
    if not user.email_verified:
        raise HTTPException(403, "email_unverified")
    return _issue_token(user)


@router.post("/password/forgot", status_code=202)
def forgot_password(req: CodeRequest, db: Session = Depends(get_db)):
    email = normalize_email(req.email)
    user = db.query(User).filter(User.email == email).one_or_none()
    # Anti-enumeration: always 202, send a link only for a real verified account
    # and only if we haven't just sent one.
    if user is not None and user.email_verified and not _recently_sent(db, email, "reset"):
        token = _issue_link_token(db, email, "reset", settings.RESET_TTL_SECONDS)
        link = f"{settings.FRONTEND_URL}/reset?token={token}"
        send_reset_link(email, link)
    return {"ok": True}


@router.post("/password/reset")
def reset_password(req: ResetRequest, db: Session = Depends(get_db)):
    user = _consume_link_token(db, req.token, "reset")
    if user is None:
        raise HTTPException(400, "invalid_token")
    now = datetime.utcnow()
    user.password_hash = hash_password(req.password)
    user.password_changed_at = now  # revokes every pre-existing session
    user.email_verified = True  # completing reset also proves email ownership
    db.commit()
    db.refresh(user)
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
