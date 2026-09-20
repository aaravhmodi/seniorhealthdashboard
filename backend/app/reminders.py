"""Scheduled patient reminders delivered through the backend's Linq client."""
from __future__ import annotations

import logging
import threading
from datetime import timedelta

from . import caretone, circle, linq
from .schemas import ActionLevel, ReminderJob, ReminderResponse, Senior
from .store import new_id, now, store

log = logging.getLogger("carepath.reminders")

claim_lock = threading.Lock()
CLAIM_TIMEOUT_S = 60


def recipient_phones(senior: Senior, recipient: str) -> list[str]:
    caregivers = circle.escalation_chain(senior, ActionLevel.CALL_CLINIC)
    caregiver = caregivers[0] if caregivers else None
    phones: list[str] = []
    if recipient in {"self", "both"} and senior.phone_e164:
        phones.append(senior.phone_e164)
    if recipient in {"caregiver", "both"} and caregiver and caregiver.phone_e164:
        phones.append(caregiver.phone_e164)
    # Preserve the old care-circle fallback for records without a patient line.
    if recipient == "self" and not phones and caregiver and caregiver.phone_e164:
        phones.append(caregiver.phone_e164)
    return list(dict.fromkeys(phones))


def _sms_consent_allows(senior: Senior) -> bool:
    # Older seeded/enrolled records predate the explicit patient SMS checkbox.
    # New profiles always write the key, so an explicit false remains a real
    # opt-out while legacy records keep their existing behavior.
    return senior.consent.get("sms_reminders", True)


async def send_now(
    senior: Senior, kind: str, language: str, recipient: str
) -> ReminderResponse:
    if not _sms_consent_allows(senior):
        return ReminderResponse(
            ok=False, kind=kind, body="", recipient=None, recipients=[],
            error="text reminders are not enabled for this profile",
        )
    phones = recipient_phones(senior, recipient)
    if not phones:
        target = "emergency-contact caregiver" if recipient == "caregiver" else "selected recipient"
        return ReminderResponse(
            ok=False, kind=kind, body="", recipient=None, recipients=[],
            error=f"no phone is enrolled for the {target}",
        )
    body = caretone.reminder_body(
        senior.display_name.split()[0], kind, circle.detail_link(senior.id), language
    )
    results = [await linq.send_direct(phone, body) for phone in phones]
    for phone, result in zip(phones, results):
        if result.message_id:
            store.reminders[result.message_id] = {
                "senior_id": senior.id,
                "kind": kind,
                "recipient": phone,
                "status": "sent" if result.ok else "failed",
                "body": body,
                "message_id": result.message_id,
            }
    errors = [result.error for result in results if result.error]
    message_ids = [result.message_id for result in results if result.message_id]
    return ReminderResponse(
        ok=all(result.ok for result in results),
        mocked=all(result.mocked for result in results),
        kind=kind,
        recipient=", ".join(phones),
        recipients=phones,
        message_id=message_ids[0] if message_ids else None,
        message_ids=message_ids,
        body=body,
        error="; ".join(errors) if errors else None,
    )


def create_job(
    senior: Senior,
    kind: str,
    language: str,
    recipient: str,
    scheduled_for,
) -> ReminderJob:
    if not _sms_consent_allows(senior):
        raise ValueError("text reminders are not enabled for this profile")
    if not recipient_phones(senior, recipient):
        raise ValueError("no phone is enrolled for the selected recipient")
    job = ReminderJob(
        id=new_id("rem"),
        senior_id=senior.id,
        kind=kind,
        language=language,
        recipient=recipient,
        scheduled_for=scheduled_for,
        created_at=now(),
    )
    store.reminder_jobs[job.id] = job
    return job


def due_jobs() -> list[ReminderJob]:
    return [
        job for job in store.reminder_jobs.values()
        if job.status == "scheduled" and job.scheduled_for <= now()
    ]


def claim_due() -> list[ReminderJob]:
    claimed: list[ReminderJob] = []
    stale = now() - timedelta(seconds=CLAIM_TIMEOUT_S)
    with claim_lock:
        for job in list(store.reminder_jobs.values()):
            if job.status == "sending" and job.scheduled_for <= stale:
                job.status = "scheduled"
            if job.status == "scheduled" and job.scheduled_for <= now():
                job.status = "sending"
                store.reminder_jobs[job.id] = job
                claimed.append(job)
    return claimed


async def deliver(job: ReminderJob) -> ReminderJob:
    senior = store.get_senior(job.senior_id)
    if not senior:
        job.status = "failed"
        job.error = "unknown senior"
        store.reminder_jobs[job.id] = job
        return job
    result = await send_now(senior, job.kind, job.language, job.recipient)
    job.status = "sent" if result.ok else "failed"
    job.sent_at = now()
    job.message_ids = result.message_ids if result.ok else []
    job.error = result.error
    store.reminder_jobs[job.id] = job
    log.info("reminder %s %s", job.id, job.status)
    return job


async def tick() -> list[str]:
    sent: list[str] = []
    for job in claim_due():
        completed = await deliver(job)
        if completed.status == "sent":
            sent.append(completed.id)
    return sent
