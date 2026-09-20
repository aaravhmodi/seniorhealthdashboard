"""Signed caregiver links.

The whole PHI design rests on one sentence: the text carries a status word and
a link, and the detail lives behind the link. That only holds if the link is
actually a credential. `/c/sen_rosa` was not -- it was a guess, and behind it
sat conditions, the medication list and the caregivers' phone numbers.

So the link carries a signed token instead of a senior id:

    https://carepath.app/c/<payload>.<signature>

The payload names one senior, optionally one evaluation, and an expiry. The
signature is HMAC-SHA256 over the payload with a server-side secret. Nothing
in it is secret -- it is base64, anyone can read the senior id out of it --
and that is fine: the point is that it cannot be *forged* or *guessed*, and it
stops working on its own.

Why not a session? Because the person opening it is a daughter on a sidewalk
who has never signed into anything, tapping a link in iMessage. Any design
that asks her to authenticate first is a design where she does not read the
alert. A capability URL is the honest shape for this: possession of the link
is the authorisation, exactly as it is for a password-reset email.

Consequences we accept, and should say out loud in the pitch:

* Anyone the caregiver forwards the link to can read it. That is the same
  property a forwarded email has, and the alternative is worse.
* A link in a text lives in the message history, so it expires -- `LINK_TTL_DAYS`,
  seven by default. An old alert stops opening, which is correct: the
  information behind it is stale by then anyway.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

from .config import get_settings

log = logging.getLogger("carepath.links")


class LinkError(ValueError):
    """A token that is malformed, forged, or expired."""


@dataclass(frozen=True)
class LinkClaims:
    senior_id: str
    evaluation_id: Optional[str] = None
    expires_at: int = 0

    @property
    def expired(self) -> bool:
        return bool(self.expires_at) and time.time() > self.expires_at


def _secret() -> bytes:
    settings = get_settings()
    secret = settings.caregiver_link_secret
    if not secret:
        # Never silently fall back to a well-known key: an unset secret in
        # production would mean every link is forgeable by anyone who reads
        # this file. Loud, and the caller turns it into a 503.
        raise LinkError("CAREGIVER_LINK_SECRET is not configured")
    return secret.encode()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def mint(senior_id: str, evaluation_id: str | None = None, ttl_days: float | None = None) -> str:
    """A token for one senior, good for a bounded window."""
    settings = get_settings()
    days = settings.link_ttl_days if ttl_days is None else ttl_days
    claims = {"sid": senior_id, "exp": int(time.time() + days * 86400)}
    if evaluation_id:
        claims["ev"] = evaluation_id
    payload = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signature = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def verify(token: str) -> LinkClaims:
    """Read a token, or raise. Signature first, then expiry.

    Checking the signature before anything else matters: an expiry read out of
    an unverified payload is an expiry the attacker chose.
    """
    if not token or token.count(".") != 1:
        raise LinkError("malformed link")
    payload, signature = token.split(".", 1)

    expected = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, signature):
        raise LinkError("this link is not valid")

    try:
        claims = json.loads(_unb64(payload))
    except Exception as exc:
        raise LinkError("malformed link") from exc
    if not isinstance(claims, dict) or not claims.get("sid"):
        raise LinkError("malformed link")

    parsed = LinkClaims(
        senior_id=str(claims["sid"]),
        evaluation_id=str(claims["ev"]) if claims.get("ev") else None,
        expires_at=int(claims.get("exp", 0)),
    )
    if parsed.expired:
        raise LinkError("this link has expired")
    return parsed


def configured() -> bool:
    return bool(get_settings().caregiver_link_secret)
