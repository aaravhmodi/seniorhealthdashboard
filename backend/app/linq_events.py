"""Turning a Linq webhook into something the rest of the app understands.

Linq posts an envelope: an event name and a `data` object whose shape differs
per event. Sprint 0 accepted our own flat `LinqInbound` on this endpoint, and
the Postman collection and the tests still post that shape. Rather than break
either, everything is normalized to `LinqInbound` here, and the router only
ever sees the flat form.

Then the interesting half: deciding what an inbound text *means*. A caregiver
who types "where is dad??" at 11pm is asking one question, and answering it in
one text is the single most-used thing this system does.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Optional

from . import caretone, circle, linq, llm, retrieval
from .schemas import (
    ActionLevel,
    Alert,
    Caregiver,
    LinqInbound,
    Senior,
)
from .store import store

# The events worth subscribing to. Sends and delivery receipts are noise here;
# what we need is what the family says and what they tap.
SUBSCRIBED_EVENTS = [
    "message.received",
    "reaction.added",
]


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _text_of(parts: Any) -> Optional[str]:
    if not isinstance(parts, list):
        return None
    chunks = [
        str(p.get("value") or p.get("body") or "")
        for p in parts
        if isinstance(p, dict) and p.get("type") == "text"
    ]
    joined = " ".join(c for c in chunks if c).strip()
    return joined or None


def _media_of(parts: Any) -> list[str]:
    if not isinstance(parts, list):
        return []
    urls: list[str] = []
    for p in parts:
        if not isinstance(p, dict) or p.get("type") not in {"media", "attachment"}:
            continue
        url = p.get("url") or p.get("value") or (p.get("media") or {}).get("url")
        if url:
            urls.append(str(url))
    return urls


def normalize(payload: dict[str, Any]) -> Optional[LinqInbound]:
    """Flatten a Linq envelope, or pass through our own Sprint-0 shape.

    Returns None for an event we do not act on, which the router answers 200
    to -- a webhook we ignore is not an error, and a non-2xx would have Linq
    retrying it for twenty-five minutes.
    """
    if not isinstance(payload, dict):
        return None

    # Our own flat shape, posted by the tests and the Postman collection.
    if "thread_id" in payload and "data" not in payload:
        try:
            return LinqInbound(**payload)
        except Exception:
            return None

    # Linq's current webhook envelope calls this `event_type`; older payloads
    # and our flat test contract use `event` or `type`.
    event = str(payload.get("event") or payload.get("type") or payload.get("event_type") or "")
    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    if event.startswith("message.") or event.startswith("message_"):
        if event not in {"message.received", "message_received"} and data.get("direction") != "inbound":
            return None
        chat = data.get("chat") or {}
        sender = data.get("sender_handle") or {}
        # Never act on our own message coming back at us. Linq's rate-limit
        # docs name self-replying webhook handlers as the usual cause of a
        # 1007, and the failure mode is worse than a rate limit: two bots
        # texting a family in a loop at three in the morning.
        if sender.get("is_me") or data.get("direction") == "outbound":
            return None
        return LinqInbound(
            thread_id=str(chat.get("id") or data.get("chat_id") or ""),
            from_phone_e164=str(sender.get("handle") or ""),
            text=_text_of(data.get("parts")),
            media_urls=_media_of(data.get("parts")),
            received_at=_parse_time(data.get("sent_at") or data.get("received_at")),
            event=event,
        )

    if event in {"reaction.added", "reaction_added"}:
        sender = data.get("from_handle") or {}
        if sender.get("is_me"):
            return None  # our own tapback is not the family acknowledging
        return LinqInbound(
            thread_id=str(data.get("chat_id") or ""),
            from_phone_e164=str(sender.get("handle") or ""),
            reaction=str(data.get("reaction_type") or ""),
            reacted_to=str(data.get("message_id") or "") or None,
            received_at=_parse_time(data.get("reacted_at")),
            event=event,
        )

    return None


# --------------------------------------------------------------------------
# Who sent this
# --------------------------------------------------------------------------
def resolve(inbound: LinqInbound) -> tuple[Optional[Senior], Optional[Caregiver]]:
    """Match an inbound to a senior and, when we can, to one caregiver.

    Chat first, because it is stable; then the sender's handle, because a
    caregiver may text us from a number we have never put in a group.
    """
    senior = store.senior_by_chat(inbound.thread_id) if inbound.thread_id else None
    caregiver: Optional[Caregiver] = None

    if senior and inbound.from_phone_e164:
        caregiver = next(
            (cg for cg in senior.caregivers if cg.phone_e164 == inbound.from_phone_e164),
            None,
        )
    if not senior:
        senior, caregiver = store.caregiver_by_thread(inbound.thread_id)
    if not senior and inbound.from_phone_e164:
        senior, caregiver = store.caregiver_by_phone(inbound.from_phone_e164)
    if not senior and inbound.from_phone_e164:
        senior = next(
            (person for person in store.seniors.values()
             if person.phone_e164 == inbound.from_phone_e164
             or inbound.from_phone_e164 in person.linq_handles),
            None,
        )
    return senior, caregiver


def alert_for_reaction(senior: Senior, inbound: LinqInbound) -> Optional[Alert]:
    """Which alert a tapback was aimed at.

    By message id when Linq gives us one, because a family scrolling back and
    tapping an older alert means that older alert. Otherwise the newest open
    one, which is what a tapback in the moment almost always is.
    """
    if inbound.reacted_to:
        for alert in store.alerts.values():
            if alert.message_id == inbound.reacted_to:
                return alert
    open_alerts = circle.open_alerts_for(senior.id)
    return open_alerts[0] if open_alerts else None


# --------------------------------------------------------------------------
# What they want
# --------------------------------------------------------------------------
def _when_phrase(at: datetime) -> str:
    """Rough, human, and never wrong in a way that matters."""
    delta = datetime.now(timezone.utc) - at
    minutes = delta.total_seconds() / 60
    if minutes < 90:
        return "in the last hour"
    if minutes < 60 * 12:
        return "earlier today"
    if minutes < 60 * 36:
        return "yesterday"
    return f"{int(minutes // (60 * 24))} days ago"


async def answer_status(senior: Senior, chat_id: Optional[str], to: str) -> bool:
    """"Where is Dad?" -- answered in one text, with no PHI in it.

    Level word plus when we last heard from them plus the link. If there has
    been no check-in at all we say exactly that, because silence is itself the
    answer the caregiver needs.
    """
    first_name = senior.display_name.split()[0]
    link = circle.detail_link(senior.id)
    evaluation = store.latest_evaluation(senior.id)

    if evaluation:
        body = caretone.status_reply_body(
            first_name,
            int(evaluation.level),
            _when_phrase(evaluation.created_at),
            link,
        )
    else:
        body = (
            "No check-in has been recorded yet. Reply HELP if you need support. "
            f"Details: {link}."
        )

    result = (
        await linq.send_to_chat(chat_id, body) if chat_id
        else await linq.send_direct(to, body)
    )
    return result.ok


async def answer_call_request(senior: Senior, chat_id: Optional[str], to: str) -> bool:
    """They asked for a person without implying a call was already placed."""
    body = (
        "Your nurse-call request is recorded. A call has not been placed yet. "
        "If this cannot wait, call nine one one. Details: "
        f"{circle.detail_link(senior.id)}."
    )
    result = (
        await linq.send_to_chat(chat_id, body) if chat_id
        else await linq.send_direct(to, body)
    )
    return result.ok


async def answer_help(senior: Senior, chat_id: Optional[str], to: str) -> bool:
    body = (
        "Reply STATUS for the latest update. Reply CALL to request a nurse call. "
        "Tap back on an alert to mark it seen. "
        f"Details: {circle.detail_link(senior.id)}."
    )
    result = (
        await linq.send_to_chat(chat_id, body) if chat_id
        else await linq.send_direct(to, body)
    )
    return result.ok


async def answer_conversation(
    senior: Senior, chat_id: Optional[str], to: str,
    message: str = "", category: str = "caregiver concern",
) -> bool:
    """Answer an unrecognized caregiver message with bounded, contextual warmth."""
    evaluation = store.latest_evaluation(senior.id)
    level = int(evaluation.level) if evaluation else 1
    # Keep the live conversation on the same evidence path as dashboard Q&A:
    # history plus the loaded NEISS/FAERS corpus, scoped to this senior.
    context = retrieval.build_context(
        message,
        senior_id=senior.id,
        language=getattr(senior, "preferred_language", "en") or "en",
        k=5,
    )
    result_text = llm.caregiver_reply(
        senior.display_name.split()[0], level, category, circle.detail_link(senior.id), message,
        context=context,
        evidence=evaluation.evidence if evaluation else None,
    )
    result = await (linq.send_to_chat(chat_id, result_text.value) if chat_id
                    else linq.send_direct(to, result_text.value))
    return result.ok


def is_ack_reaction(reaction: str | None) -> bool:
    return bool(reaction) and reaction.strip().lower() in linq.ACK_REACTIONS


__all__ = [
    "SUBSCRIBED_EVENTS",
    "normalize",
    "resolve",
    "alert_for_reaction",
    "answer_status",
    "answer_call_request",
    "answer_help",
    "answer_conversation",
    "is_ack_reaction",
    "already_handled",
    "forget_deliveries",
    "ActionLevel",
]


# --------------------------------------------------------------------------
# Replay protection
# --------------------------------------------------------------------------
# Linq delivers at-least-once and retries a non-2xx for about twenty-five
# minutes. So the same event arrives more than once as a matter of course, not
# as a fault. Acknowledging twice is harmless -- it is idempotent -- but
# answering "where is Dad?" twice texts the family twice, and a system that
# double-texts an anxious caregiver is one they mute.
#
# Bounded, in-memory, and keyed on the webhook id. Losing it on restart costs
# at most one duplicate reply, which is the right trade against unbounded
# growth in a process that is meant to stay up for a weekend.
_SEEN_LIMIT = 2048
_seen_ids: OrderedDict[str, float] = OrderedDict()
_seen_lock = threading.Lock()


def already_handled(webhook_id: str | None) -> bool:
    """True when this exact delivery has been processed before.

    Records the id as a side effect, so the caller checks once and acts.
    """
    if not webhook_id:
        return False  # nothing to key on; better to act twice than never
    with _seen_lock:
        if webhook_id in _seen_ids:
            _seen_ids.move_to_end(webhook_id)
            return True
        _seen_ids[webhook_id] = time.time()
        while len(_seen_ids) > _SEEN_LIMIT:
            _seen_ids.popitem(last=False)
    return False


def forget_deliveries() -> None:
    """Test and demo-reset hook."""
    with _seen_lock:
        _seen_ids.clear()
