"""Outbound email — single dispatch point so production swap-in is one file.

Today only the `console` backend exists, which logs the code through stdlib
logging at INFO. Future backends ("smtp", "ses", "resend") plug in here without
changing call sites in `routes/auth.py`.
"""
from __future__ import annotations

import logging

from app.config import settings

log = logging.getLogger(__name__)


def send_code(to: str, code: str, *, purpose: str = "login") -> None:
    backend = settings.EMAIL_BACKEND
    if backend == "console":
        # TODO: gate behind a DEV_MODE flag once a real backend is wired up so
        # we never accidentally log codes in prod.
        log.info("AUTH_CODE backend=console purpose=%s to=%s code=%s", purpose, to, code)
        return
    # When new backends land, branch here. Failing loud beats silently dropping
    # a code in dev.
    raise NotImplementedError(f"email backend not implemented: {backend}")
