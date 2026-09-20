"""Caregiver messaging over Linq.

PHI rule, and we say this out loud in the pitch: SMS and iMessage are not
HIPAA-grade, so an outbound text carries a status word and a link. No symptoms,
no medicines, no diagnosis. The detail lives behind the link, which requires the
caregiver's consented session.

This module is now the seam between the check-in pipeline and the family side.
The composition lives in `caretone.py` (how we say bad news in a text), the
transport in `linq.py`, and the group chat plus escalation clock in
`circle.py`. What stays here is `compose()` -- pure, unit-tested, and the place
the PHI assertion fires -- and the one call the router makes.
"""
from __future__ import annotations

from . import caretone, circle, linq
from .config import get_settings
from .schemas import (
    ActionLevel,
    Caregiver,
    Evaluation,
    NotificationReceipt,
    Senior,
)
from .store import now, store

STATUS_WORD: dict[int, str] = {
    1: "checked in, all steady",
    2: "checked in, a call to the clinic is recommended today",
    3: "needs to be seen at the emergency department",
    4: "emergency help has been recommended",
}

# Anything that looks like clinical detail must not reach a text message.
_BANNED_SUBSTRINGS = ("diagnos", "chest pain", "confusion", "medication", "mg")


def compose(senior: Senior, evaluation: Evaluation, caregiver: Caregiver) -> str:
    """The body that goes to the family.

    Delegates the wording to `caretone.alert_body` -- SPIKES compressed into
    one text -- and keeps the PHI assertion here, where it has always been,
    so no change to the voice can quietly smuggle a symptom into an SMS.
    """
    # The signed, expiring link -- not the senior's id in a path, which is
    # what this used to build and what made the link a guess.
    link = circle.detail_link(senior.id, evaluation.id)
    first_name = senior.display_name.split()[0]
    body = caretone.alert_body(
        first_name, int(evaluation.level), link, caregiver_name=caregiver.name
    )
    # Over the prose, not the link: the signed token in the URL is base64 and
    # will sometimes contain "mg" by pure chance. See linq.contains_phi.
    assert not linq.contains_phi(body), "PHI leaked into SMS"
    return body


def recipients(senior: Senior, evaluation: Evaluation) -> list[Caregiver]:
    """Who hears about this evaluation, in escalation order.

    Consent and each caregiver's own threshold both apply. The order matters:
    the first name in this list is the one the alert is addressed to, and the
    rest are the escalation chain behind it.
    """
    return circle.escalation_chain(senior, evaluation.level)


async def send(
    senior: Senior, evaluation: Evaluation, caregiver: Caregiver,
    recipient: str | None = None,
) -> NotificationReceipt:
    """One-to-one send. Used for escalations and for a senior with no circle yet."""
    body = compose(senior, evaluation, caregiver)
    receipt = NotificationReceipt(
        to=caregiver.id, thread_id=caregiver.linq_thread_id, body=body
    )
    result = await linq.send_direct(recipient or caregiver.phone_e164, body)
    receipt.status = "mocked" if result.mocked else ("sent" if result.ok else "failed")
    receipt.thread_id = result.chat_id or caregiver.linq_thread_id
    receipt.sent_at = now()
    receipt.error = result.error
    return receipt


def manual_recipient(senior: Senior, caregiver: Caregiver) -> str | None:
    """Return the safe destination for the dashboard's manual update.

    Manual updates are intentionally directed to the configured account email
    when present. A caregiver phone is still used by the automatic escalation
    path, but it must not be used here when it is a local-format number that
    Linq will reject.
    """
    configured = get_settings().linq_default_recipient.strip()
    if configured:
        return configured
    for handle in senior.linq_handles:
        if "@" in handle:
            return handle
    if "@" in caregiver.phone_e164:
        return caregiver.phone_e164
    return None


async def notify_caregivers(
    senior: Senior, evaluation: Evaluation
) -> list[NotificationReceipt]:
    """Alert the family about an evaluation.

    One message into the group chat, not one per caregiver -- the daughter and
    the son seeing each other's replies is the point. The returned receipts are
    still per caregiver, because that is what the dashboard and the contract
    expect, and because the escalation chain is exactly who was covered.
    """
    chain = recipients(senior, evaluation)
    if not chain:
        return []

    alert = await circle.raise_alert(senior, evaluation)
    if alert is None:
        return []

    circle_state = store.circles.get(senior.id)
    delivered = bool(alert.message_id)
    status = "sent" if delivered else "failed"
    if not linq.is_enabled() and delivered:
        status = "mocked"

    receipts: list[NotificationReceipt] = []
    for index, cg in enumerate(chain):
        receipts.append(
            NotificationReceipt(
                to=cg.id,
                thread_id=alert.chat_id or (circle_state.chat_id if circle_state else None),
                body=alert.body,
                # Everyone in the circle receives the group message; anyone
                # further down the chain is covered but not yet asked, which
                # is what "skipped" means on the dashboard.
                status=status if index == 0 or circle_state else "skipped",
                sent_at=alert.created_at,
            )
        )
    return receipts


__all__ = [
    "STATUS_WORD",
    "ActionLevel",
    "compose",
    "manual_recipient",
    "recipients",
    "send",
    "notify_caregivers",
]
