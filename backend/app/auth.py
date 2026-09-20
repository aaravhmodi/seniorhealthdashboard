"""Who is allowed to call the clinician-facing API.

The frontend has been attaching a Supabase JWT since signup landed. The
backend has been ignoring it, so every endpoint was open: `GET /seniors/sen_rosa`
returned the medication list and the caregivers' phone numbers to anyone who
asked. Signing the caregiver link (see `links.py`) is pointless on its own
while that is true -- an attacker skips the link and calls the endpoint.

Three modes, chosen by what is configured:

1. `SUPABASE_JWT_SECRET` set -- verify the Supabase access token (HS256).
   This is the real one.
2. `API_SHARED_SECRET` set -- accept a bearer equal to it. For the Postman
   collection, the smoke script, and a teammate's terminal, none of which
   have a Supabase session.
3. Neither set -- open, with a loud warning at boot.

Mode 3 exists because a hackathon is a sequence of half-finished things and
locking out the team at 2am to satisfy a linter is a worse outcome than an
open dev server. It is not a default anyone should reach production with,
which is why it shouts on every boot and `/healthz` reports it.

The caregiver view does not come through here. It authenticates with the
signed link itself, because the person opening it has never signed into
anything and never will.
"""
from __future__ import annotations

import hmac
import logging
from typing import Any, Optional

from fastapi import Header, HTTPException

from .config import get_settings

log = logging.getLogger("carepath.auth")


class Principal:
    """Who is calling. `subject` is the Supabase user id when we know it."""

    def __init__(self, mode: str, subject: Optional[str] = None, claims: Optional[dict] = None):
        self.mode = mode
        self.subject = subject
        self.claims = claims or {}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Principal(mode={self.mode!r}, subject={self.subject!r})"


def mode() -> str:
    settings = get_settings()
    if settings.supabase_jwt_secret:
        return "supabase"
    if settings.api_shared_secret:
        return "shared-secret"
    return "open"


def enabled() -> bool:
    return mode() != "open"


def _bearer(header: Optional[str]) -> Optional[str]:
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _verify_supabase(token: str) -> Principal:
    import jwt  # PyJWT; imported here so the open mode needs no dependency

    settings = get_settings()
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            # Pin the algorithm. Accepting whatever the token asks for is the
            # classic JWT mistake: a token claiming alg=none, or alg=HS256
            # against a public key, verifies against nothing.
            algorithms=["HS256"],
            audience=settings.supabase_jwt_audience or None,
            options={"require": ["exp", "sub"]},
        )
    except Exception as exc:
        # The reason is deliberately not echoed back to the caller; it tells an
        # attacker which half of the token to fix next.
        log.info("rejected a token: %s", type(exc).__name__)
        raise HTTPException(status_code=401, detail="not authenticated") from exc
    return Principal("supabase", subject=str(claims.get("sub")), claims=claims)


def _verify_shared(token: str) -> Principal:
    expected = get_settings().api_shared_secret
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="not authenticated")
    return Principal("shared-secret")


def require_user(authorization: Optional[str] = Header(default=None)) -> Principal:
    """FastAPI dependency for every endpoint that returns patient data."""
    current = mode()
    if current == "open":
        return Principal("open")

    token = _bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")

    if current == "supabase":
        try:
            return _verify_supabase(token)
        except HTTPException:
            # A shared secret still works alongside Supabase, so the smoke
            # script does not need a browser session to run.
            if get_settings().api_shared_secret:
                return _verify_shared(token)
            raise
    return _verify_shared(token)


def warn_if_open() -> None:
    """Called once at boot. Says the quiet part loudly."""
    if enabled():
        log.info("API auth: %s", mode())
        return
    log.warning(
        "API AUTH IS OFF: every endpoint serves patient data to anyone. "
        "Set SUPABASE_JWT_SECRET (or API_SHARED_SECRET) before this is public."
    )
