"""Caregiver messaging over Linq.

PHI rule, and we say this out loud in the pitch: SMS and iMessage are not
HIPAA-grade, so an outbound text carries a status word and a link. No symptoms,
no medicines, no diagnosis. The detail lives behind the link, which requires the
caregiver's consented session.

compose() is pure and unit-tested; send() is the side effect.
"""
from __future__ import annotations

import httpx

from .config import get_settings
from .schemas import (
    ActionLevel,
    Caregiver,
    Evaluation,
    NotificationReceipt,
    Senior,
)
from .store import now

STATUS_WORD: dict[int, str] = {
    1: "checked in, all steady",
    2: "checked in, a call to the clinic is recommended today",
    3: "needs to be seen at the emergency department",
    4: "emergency help has been recommended",
}

# Anything that looks like clinical detail must not reach a text message.
_BANNED_SUBSTRINGS = ("diagnos", "chest pain", "confusion", "medication", "mg")


def compose(senior: Senior, evaluation: Evaluation, caregiver: Caregiver) -> str:
    settings = get_settings()
    link = f"{settings.public_web_base}/c/{senior.id}?ev={evaluation.id}"
    first_name = senior.display_name.split()[0]
    body = (
        f"{first_name} {STATUS_WORD[int(evaluation.level)]}. "
        f"Open the details: {link}"
    )
    if int(evaluation.level) >= int(ActionLevel.GO_TO_ER):
        body += " Reply HELP to reach the care team."
    assert not any(b in body.lower() for b in _BANNED_SUBSTRINGS), "PHI leaked into SMS"
    return body


def recipients(senior: Senior, evaluation: Evaluation) -> list[Caregiver]:
    return [
        cg for cg in senior.caregivers
        if int(evaluation.level) >= int(cg.notify_at_level)
        and senior.consent.get(f"share_with:{cg.id}", False)
    ]


async def send(
    senior: Senior, evaluation: Evaluation, caregiver: Caregiver
) -> NotificationReceipt:
    settings = get_settings()
    body = compose(senior, evaluation, caregiver)
    receipt = NotificationReceipt(
        to=caregiver.id, thread_id=caregiver.linq_thread_id, body=body
    )

    if settings.mock_mode or not settings.linq_api_key:
        receipt.status = "mocked"
        receipt.sent_at = now()
        return receipt

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{settings.linq_api_base}/v1/messages",
                headers={"Authorization": f"Bearer {settings.linq_api_key}"},
                json={
                    "thread_id": caregiver.linq_thread_id,
                    "to": [caregiver.phone_e164],
                    "text": body,
                },
            )
            resp.raise_for_status()
            receipt.status = "sent"
            receipt.thread_id = resp.json().get("thread_id", caregiver.linq_thread_id)
    except Exception:
        # A messaging outage must never break a check-in.
        receipt.status = "failed"
    receipt.sent_at = now()
    return receipt


async def notify_caregivers(
    senior: Senior, evaluation: Evaluation
) -> list[NotificationReceipt]:
    return [await send(senior, evaluation, cg) for cg in recipients(senior, evaluation)]
