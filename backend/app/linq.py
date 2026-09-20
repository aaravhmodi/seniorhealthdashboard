"""Thin client for the Linq partner API (iMessage / RCS / SMS).

Linq is how the family side of this product exists at all. The patient is at
the kiosk; their daughter is at work three states away. A group chat with the
patient, the caregivers and the care team is the only surface all of them
already have open.

Two rules this module exists to enforce:

1. **No PHI goes out over it.** iMessage and SMS are not HIPAA-grade. Every
   outbound body is composed in `notify.compose`, which asserts the ban, and
   this module refuses to send a body that fails the same check. Status word
   plus a link; the detail lives behind the link.
2. **A messaging outage never breaks a check-in.** Every call here returns a
   result object instead of raising, and the caller records the failure.

Mock mode (`MOCK_MODE=true` or no `LINQ_API_KEY`) returns deterministic fake
ids so the whole flow -- circles, escalation, follow-ups -- is testable and
demoable with the network unplugged.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import httpx

from .config import get_settings

# Linq's built-in tapback types. Anything else is sent as a `custom` tapback
# carrying the emoji itself, which is how a check mark becomes a tapback at all
# -- iMessage has no check mark of its own.
BUILTIN_REACTIONS = {"love", "like", "dislike", "laugh", "emphasize", "question"}

# The tapbacks we count as "I have seen this and I am on it". A dislike or a
# question mark is deliberately not here: those mean the caregiver saw it and
# is *not* satisfied, which is the opposite of an acknowledgement and must
# keep the escalation clock running.
ACK_REACTIONS = {
    "like", "love", "emphasize",
    "check", "checkmark", "✅", "✔", "\U0001f44d", "\U0001f44c",
}

# Mirrors notify._BANNED_SUBSTRINGS. Duplicated on purpose: this is the last
# gate before bytes leave the building, and it should not depend on the caller
# having used the right composer.
_BANNED_SUBSTRINGS = ("diagnos", "chest pain", "confusion", "medication", "mg")


class LinqError(RuntimeError):
    """Raised only by `assert_configured`; the send path never raises."""


@dataclass
class LinqResult:
    """What every call here returns. `ok` is the only field callers must check."""

    ok: bool
    mocked: bool = False
    chat_id: Optional[str] = None
    message_id: Optional[str] = None
    error: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


def is_enabled() -> bool:
    settings = get_settings()
    return bool(settings.linq_api_key) and not settings.mock_mode


def assert_configured() -> None:
    if not get_settings().linq_api_key:
        raise LinqError("LINQ_API_KEY is not configured")


def contains_phi(body: str) -> bool:
    return any(b in body.lower() for b in _BANNED_SUBSTRINGS)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_settings().linq_api_key}",
        "Content-Type": "application/json",
    }


def _url(path: str) -> str:
    base = get_settings().linq_api_base.rstrip("/")
    return f"{base}/v3/{path.lstrip('/')}"


def text_parts(body: str) -> list[dict[str, str]]:
    return [{"type": "text", "value": body}]


_URL_RE = re.compile(r"https?://\S+")


def split_link(body: str) -> tuple[str, Optional[str]]:
    """Separate a body from the link it carries.

    Linq rejects the *first* outbound message to a new chat if it contains a
    URL (400, error code 1005) -- an anti-spam rule, and a reasonable one. But
    every message we compose carries a link, because the link is where the
    detail that may not go in an SMS lives. So an opening send goes out in two
    parts: the words, then the link as an immediate follow-up into the chat we
    just created.
    """
    match = _URL_RE.search(body)
    if not match:
        return body.strip(), None
    url = match.group(0).rstrip(".,")
    stripped = _URL_RE.sub("", body)
    stripped = re.sub(r"\s+", " ", stripped).replace(" .", ".").strip()
    # "Details: ." reads worse than no trailing label at all.
    stripped = re.sub(r"\b(Details|The plan|Follow along here):\s*\.?", "", stripped).strip()
    return re.sub(r"\s+", " ", stripped).strip(), url


async def _post(path: str, payload: dict[str, Any]) -> LinqResult:
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(_url(path), headers=_headers(), json=payload)
            if resp.status_code >= 400:
                return LinqResult(ok=False, error=f"{resp.status_code}: {resp.text[:300]}")
            data = resp.json() if resp.content else {}
    except Exception as exc:  # network, DNS, timeout, bad JSON
        return LinqResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    # Two response shapes: a send returns {chat_id, message}, a chat creation
    # returns {chat: {id, ..., message}}. Both are flattened to the same thing.
    chat = data.get("chat") or {}
    message = data.get("message") or chat.get("message") or data.get("data") or {}
    if not isinstance(message, dict):
        message = {}
    return LinqResult(
        ok=True,
        chat_id=data.get("chat_id") or chat.get("id") or message.get("chat_id"),
        message_id=message.get("id") or data.get("id"),
        raw=data,
    )


async def _get(path: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=12.0) as client:
        resp = await client.get(_url(path), headers=_headers())
        resp.raise_for_status()
        return resp.json()


# --------------------------------------------------------------------------
# Deterministic mock ids
# --------------------------------------------------------------------------
def _mock_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]
    return f"{prefix}_{digest}"


# --------------------------------------------------------------------------
# Lines
# --------------------------------------------------------------------------
async def sending_number() -> Optional[str]:
    """The line the care circle is created from.

    Configured number wins; otherwise the first healthy number on the account.
    Returns None in mock mode so nothing downstream pretends to know a real one.
    """
    settings = get_settings()
    if settings.linq_from_number:
        return settings.linq_from_number
    if not is_enabled():
        return None
    try:
        data = await _get("phone_numbers")
    except Exception:
        return None
    numbers = data.get("phone_numbers") or []
    healthy = [
        n for n in numbers
        if str((n.get("reputation") or {}).get("status", "")).upper() == "HEALTHY"
    ]
    pick = (healthy or numbers or [None])[0]
    return pick.get("phone_number") if pick else None


# --------------------------------------------------------------------------
# Chats
# --------------------------------------------------------------------------
async def create_group_chat(
    to: Iterable[str], opening_message: str, name: Optional[str] = None
) -> LinqResult:
    """Open the care circle: patient + caregivers + care team, one thread.

    Linq treats "several recipients" as a group chat, so the caller simply
    passes every handle. The opening message doubles as the consent notice.
    """
    recipients = [h for h in dict.fromkeys(to) if h]
    if not recipients:
        return LinqResult(ok=False, error="no recipients")
    if contains_phi(opening_message):
        return LinqResult(ok=False, error="refused: body contains clinical detail")

    if not is_enabled():
        chat_id = _mock_id("chat", *recipients)
        return LinqResult(
            ok=True, mocked=True, chat_id=chat_id,
            message_id=_mock_id("msg", chat_id, opening_message),
        )

    # POST /v3/chats requires `from`, and a group chat takes at most 31
    # handles. Without a line we cannot open a group at all, and quietly
    # opening a one-to-one instead would silently lose the whole point.
    from_number = await sending_number()
    if not from_number:
        return LinqResult(ok=False, error="no Linq line available to send from")
    if len(recipients) > 31:
        return LinqResult(ok=False, error=f"{len(recipients)} recipients exceeds the 31 limit")

    opening, link = split_link(opening_message)
    result = await _post(
        "chats",
        {
            "from": from_number,
            "to": recipients,
            "message": {"parts": text_parts(opening)},
        },
    )
    if result.ok and result.chat_id:
        if name:
            await set_group_name(result.chat_id, name)
        if link:
            # The link could not ride on the opening message, so it follows it.
            await send_to_chat(result.chat_id, link)
    return result


async def set_group_name(chat_id: str, name: str) -> LinqResult:
    """Name the thread so it is findable in a crowded message list."""
    if not is_enabled():
        return LinqResult(ok=True, mocked=True, chat_id=chat_id)
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.put(
                _url(f"chats/{chat_id}"), headers=_headers(),
                json={"display_name": name},
            )
            if resp.status_code >= 400:
                return LinqResult(ok=False, error=f"{resp.status_code}: {resp.text[:200]}")
    except Exception as exc:
        return LinqResult(ok=False, error=f"{type(exc).__name__}: {exc}")
    return LinqResult(ok=True, chat_id=chat_id)


async def add_participant(chat_id: str, handle: str) -> LinqResult:
    """A second caregiver joining an existing circle."""
    if not is_enabled():
        return LinqResult(ok=True, mocked=True, chat_id=chat_id)
    return await _post(f"chats/{chat_id}/participants", {"handle": handle})


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------
async def send_to_chat(chat_id: str, body: str) -> LinqResult:
    if contains_phi(body):
        return LinqResult(ok=False, error="refused: body contains clinical detail")
    if not is_enabled():
        return LinqResult(
            ok=True, mocked=True, chat_id=chat_id,
            message_id=_mock_id("msg", chat_id, body, str(time.time())),
        )
    result = await _post(f"chats/{chat_id}/messages", {"message": {"parts": text_parts(body)}})
    result.chat_id = result.chat_id or chat_id
    return result


async def send_direct(to: str, body: str) -> LinqResult:
    """One-to-one, for an escalation that must not go to the whole circle.

    This may be the first message we have ever sent that person -- an
    escalation reaches the backup caregiver who was never in the group -- so
    it is subject to the same no-link-first rule as opening a chat.
    """
    if contains_phi(body):
        return LinqResult(ok=False, error="refused: body contains clinical detail")
    if not is_enabled():
        chat_id = _mock_id("chat", to)
        return LinqResult(
            ok=True, mocked=True, chat_id=chat_id,
            message_id=_mock_id("msg", chat_id, body, str(time.time())),
        )

    result = await _post("messages", {"to": [to], "message": {"parts": text_parts(body)}})
    if not result.ok and "1005" in (result.error or ""):
        # First message to this handle and it carried a link. Split and retry.
        opening, link = split_link(body)
        result = await _post(
            "messages", {"to": [to], "message": {"parts": text_parts(opening)}}
        )
        if result.ok and link and result.chat_id:
            await send_to_chat(result.chat_id, link)
    return result


async def react(
    message_id: str, reaction_type: str = "like", part_index: int = 0
) -> LinqResult:
    """Tapback our own acknowledgement back at the family.

    Confirming we have seen a caregiver's reply should not cost a whole
    message. `reaction_type` may be one of Linq's built-ins, or any emoji --
    an emoji is sent as a `custom` tapback, which is how the check mark the
    family taps at us is also the one we can tap back.
    """
    if not is_enabled():
        return LinqResult(ok=True, mocked=True, message_id=message_id)

    payload: dict[str, Any] = {"operation": "add", "part_index": part_index}
    if reaction_type in BUILTIN_REACTIONS:
        payload["type"] = reaction_type
    else:
        payload["type"] = "custom"
        payload["custom_emoji"] = reaction_type
    return await _post(f"messages/{message_id}/reactions", payload)


ACK_TAPBACK = "✅"  # what we tap back to say "seen, we have it"


# --------------------------------------------------------------------------
# Webhooks
# --------------------------------------------------------------------------
async def ensure_webhook(target_url: str, events: list[str]) -> LinqResult:
    """Register (once) the subscription that delivers inbound texts and tapbacks."""
    if not is_enabled():
        return LinqResult(ok=True, mocked=True)
    try:
        existing = await _get("webhook-subscriptions")
        for sub in existing.get("subscriptions", []):
            if sub.get("target_url") == target_url and sub.get("is_active"):
                return LinqResult(ok=True, raw=sub)
    except Exception:
        pass  # a listing failure should not stop us trying to create it
    return await _post(
        "webhook-subscriptions",
        {"target_url": target_url, "subscribed_events": events},
    )


def verify_signature(
    body: bytes,
    webhook_id: str | None,
    timestamp: str | None,
    signature: str | None,
    secret: str,
    tolerance_s: int = 300,
) -> bool:
    """Standard Webhooks verification, as Linq documents it.

    Signed content is `{id}.{timestamp}.{body}`; the secret is base64 behind a
    `whsec_` prefix; the header may carry several space-separated `v1,<sig>`
    values, and any one matching is enough.
    """
    if not (webhook_id and timestamp and signature):
        return False
    try:
        if abs(time.time() - int(timestamp)) > tolerance_s:
            return False
    except ValueError:
        return False

    raw_secret = secret[len("whsec_"):] if secret.startswith("whsec_") else secret
    try:
        key = base64.b64decode(raw_secret)
    except Exception:
        key = raw_secret.encode()

    signed = b".".join([webhook_id.encode(), timestamp.encode(), body])
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for candidate in signature.split():
        value = candidate.split(",", 1)[1] if "," in candidate else candidate
        if hmac.compare_digest(expected, value):
            return True
    return False
