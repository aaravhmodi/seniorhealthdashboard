"""The care circle: one Linq group chat per senior, and the escalation clock.

Three things live here.

**Enrollment.** When someone puts their number into the sign-up form, we open a
group chat containing the patient, every caregiver who consented, and the care
team's own line. One thread, not four one-to-ones, because the daughter and the
son coordinating in a thread we can see is most of the value -- and because the
patient being in their own family's care thread is the difference between being
cared for and being managed.

**Alerts and acknowledgement.** An alert goes to the circle and starts a clock.
A tapback on it (or a word like "ok") is the acknowledgement. That costs the
caregiver one tap, which is the point: an acknowledgement that takes effort is
an acknowledgement you do not get at 2am.

**Escalation.** If nobody acknowledges before the clock runs out, the next
caregiver in `escalation_order` is texted directly -- not in the group, so the
second person is not competing with a thread the first one is ignoring. They
are told plainly that they are the backup and why they are hearing about it.

Consent gates all of it: a caregiver is only ever in a circle, and only ever
messaged, if `senior.consent["share_with:<caregiver_id>"]` is true.
"""
from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Optional

from . import caretone, linq, links
from .config import get_settings
from .events import bus
from .schemas import (
    ActionLevel,
    Alert,
    AlertAck,
    CareCircle,
    Caregiver,
    CaregiverEnroll,
    CircleMember,
    EventType,
    Evaluation,
    Senior,
    TimelineEntry,
)
from .store import new_id, now, store

# Guards claiming an alert for escalation. See sweep_escalations().
_escalation_lock = threading.Lock()
log = logging.getLogger("carepath.circle")


def detail_link(senior_id: str, evaluation_id: str | None = None) -> str:
    """Where the detail lives. The text carries this; never the detail itself.

    The path carries a signed, expiring token rather than the senior's id. The
    link is the credential -- the person opening it is a daughter on a
    sidewalk who has never signed into anything. See links.py.
    """
    base = get_settings().public_web_base.rstrip("/")
    try:
        return f"{base}/c/{links.mint(senior_id, evaluation_id)}"
    except links.LinkError:
        # No signing secret. Rather than mint a guessable link, point at the
        # app's front door: the family gets a working message and no promise
        # of detail we cannot protect.
        log.error("CAREGIVER_LINK_SECRET is unset; sending a link with no detail page")
        return base


def consented(senior: Senior, caregiver: Caregiver) -> bool:
    return bool(senior.consent.get(f"share_with:{caregiver.id}", False))


def escalation_chain(senior: Senior, level: ActionLevel) -> list[Caregiver]:
    """Who to reach, in order, for an alert at this level.

    Sorted by `escalation_order`, filtered to consent and to caregivers who
    asked to hear about something this urgent.
    """
    eligible = [
        cg for cg in senior.caregivers
        if consented(senior, cg) and int(level) >= int(cg.notify_at_level)
    ]
    return sorted(eligible, key=lambda cg: (cg.escalation_order, senior.caregivers.index(cg)))


# --------------------------------------------------------------------------
# Enrollment
# --------------------------------------------------------------------------
def _members(senior: Senior, include_patient: bool, patient_phone: str | None) -> list[CircleMember]:
    members: list[CircleMember] = []
    if include_patient and patient_phone:
        members.append(
            CircleMember(handle=patient_phone, name=senior.display_name, role="patient")
        )
    for cg in senior.caregivers:
        if consented(senior, cg):
            members.append(
                CircleMember(
                    handle=cg.phone_e164,
                    name=f"{cg.name} ({cg.relationship})",
                    role="caregiver",
                    caregiver_id=cg.id,
                )
            )
    care_team = get_settings().linq_care_team_number
    if care_team:
        members.append(
            CircleMember(handle=care_team, name="the care team", role="care_team")
        )
    return members


def add_caregivers(senior: Senior, enrollments: list[CaregiverEnroll]) -> list[Caregiver]:
    """Turn form input into caregivers on the senior, recording consent.

    Re-enrolling the same number updates that caregiver instead of creating a
    duplicate, because a family that fills the form twice should not get two
    texts for every alert.
    """
    added: list[Caregiver] = []
    for entry in enrollments:
        existing = next(
            (cg for cg in senior.caregivers if cg.phone_e164 == entry.phone_e164), None
        )
        if existing:
            existing.name = entry.name
            existing.relationship = entry.relationship
            existing.preferred_language = entry.preferred_language
            existing.notify_at_level = entry.notify_at_level
            existing.escalation_order = entry.escalation_order
            caregiver = existing
        else:
            caregiver = Caregiver(
                id=new_id("cg"),
                name=entry.name,
                relationship=entry.relationship,
                phone_e164=entry.phone_e164,
                preferred_language=entry.preferred_language,
                notify_at_level=entry.notify_at_level,
                escalation_order=entry.escalation_order,
            )
            senior.caregivers.append(caregiver)
        senior.consent[f"share_with:{caregiver.id}"] = bool(entry.consent)
        added.append(caregiver)
    store.put_senior(senior)
    return added


async def open_circle(
    senior: Senior, include_patient: bool = True, patient_phone: str | None = None
) -> CareCircle:
    """Create (or reuse) the group chat and send the consent notice into it."""
    existing = store.circles.get(senior.id)
    members = _members(senior, include_patient, patient_phone)
    first_name = senior.display_name.split()[0]
    group_name = f"{first_name} - care team"

    circle = CareCircle(
        senior_id=senior.id,
        group_name=group_name,
        members=members,
        chat_id=existing.chat_id if existing else None,
    )
    if not members:
        circle.status = "failed"
        circle.error = "nobody consented to be in the circle"
        store.circles[senior.id] = circle
        return circle

    if circle.chat_id:
        # Already open. Pull in anyone new rather than starting a second thread.
        known = {m.handle for m in existing.members} if existing else set()
        for member in members:
            if member.handle not in known:
                await linq.add_participant(circle.chat_id, member.handle)
        circle.status = "active"
        circle.created_at = existing.created_at if existing else now()
        circle.mocked = existing.mocked if existing else not linq.is_enabled()
        store.circles[senior.id] = circle
        return circle

    welcome = caretone.welcome_body(
        first_name,
        [m.name for m in members if m.role != "patient"],
        detail_link(senior.id),
    )
    result = await linq.create_group_chat(
        [m.handle for m in members], welcome, name=group_name
    )
    circle.chat_id = result.chat_id
    circle.mocked = result.mocked
    circle.status = "active" if result.ok else "failed"
    circle.error = result.error
    circle.created_at = now()
    store.circles[senior.id] = circle

    # Keep the per-caregiver thread id in sync, so the Sprint-0 webhook lookup
    # by thread still resolves.
    for cg in senior.caregivers:
        if consented(senior, cg):
            cg.linq_thread_id = circle.chat_id
    store.put_senior(senior)

    if result.ok:
        store.add_timeline(
            senior.id,
            TimelineEntry(
                at=circle.created_at,
                type=EventType.CIRCLE_CREATED,
                summary=f"Care circle opened with {len(members)} people",
                detail={"chat_id": circle.chat_id, "mocked": circle.mocked},
            ),
        )
        bus.publish(
            EventType.CIRCLE_CREATED,
            senior.id,
            {"chat_id": circle.chat_id, "members": len(members), "mocked": circle.mocked},
        )
    return circle


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------
async def raise_alert(
    senior: Senior, evaluation: Evaluation, is_repeat: bool = False
) -> Optional[Alert]:
    """Send the group-chat alert for an evaluation and start the clock.

    Returns None when nobody is eligible, which is a legitimate outcome: a
    level-1 day for a family who only asked to hear about level 2 and up.
    """
    chain = escalation_chain(senior, evaluation.level)
    if not chain:
        return None

    circle = store.circles.get(senior.id)
    first_name = senior.display_name.split()[0]
    link = detail_link(senior.id, evaluation.id)
    body = caretone.alert_body(
        first_name,
        int(evaluation.level),
        link,
        caregiver_name=chain[0].name,
        is_repeat=is_repeat,
    )

    if circle and circle.chat_id:
        result = await linq.send_to_chat(circle.chat_id, body)
    else:
        # No circle yet -- fall back to the first caregiver directly rather
        # than dropping an alert on the floor.
        result = await linq.send_direct(chain[0].phone_e164, body)

    settings = get_settings()
    scale = max(settings.followup_time_scale, 0.001)
    alert = Alert(
        id=new_id("alrt"),
        senior_id=senior.id,
        evaluation_id=evaluation.id,
        level=evaluation.level,
        created_at=now(),
        body=body,
        chat_id=result.chat_id,
        message_id=result.message_id,
        notified=[chain[0].id],
        pending_order=[cg.id for cg in chain[1:]],
        delivery="sent" if result.ok else "pending",
        last_error=result.error,
    )
    if not result.ok:
        # Nobody heard this. Try again shortly rather than letting a thirty
        # second outage swallow a level-3 alert entirely.
        alert.next_retry_at = alert.created_at + timedelta(
            seconds=settings.alert_retry_seconds
        )
        log.error(
            "alert %s for %s failed to send: %s", alert.id, senior.id, result.error
        )
    # Only rungs that need a human answer get an escalation clock. Level 1 is
    # information; nobody should be woken up for not having read it. And the
    # clock only starts once the message actually went: escalating for silence
    # on a text that never arrived blames the family for our outage.
    if result.ok and int(evaluation.level) >= int(ActionLevel.CALL_CLINIC):
        alert.escalate_after = alert.created_at + timedelta(
            minutes=settings.escalation_timeout_minutes / scale
        )
    store.alerts[alert.id] = alert

    store.add_timeline(
        senior.id,
        TimelineEntry(
            at=alert.created_at,
            type=EventType.CAREGIVER_MESSAGE,
            evaluation_id=evaluation.id,
            level=evaluation.level,
            summary=f"Alerted the care circle ({'sent' if result.ok else 'failed'})",
            detail={"body": body, "alert_id": alert.id, "error": result.error},
        ),
    )
    return alert


def acknowledge(
    alert: Alert, caregiver: Caregiver, via: str = "tapback", detail: str | None = None
) -> Alert:
    """Stop the clock. Idempotent -- a caregiver who taps twice is not a bug."""
    if not any(a.caregiver_id == caregiver.id for a in alert.acks):
        alert.acks.append(
            AlertAck(caregiver_id=caregiver.id, at=now(), via=via, detail=detail)
        )
    alert.escalate_after = None
    alert.resolved = True
    store.alerts[alert.id] = alert

    store.add_timeline(
        alert.senior_id,
        TimelineEntry(
            at=now(),
            type=EventType.ALERT_ACKNOWLEDGED,
            evaluation_id=alert.evaluation_id,
            summary=f"{caregiver.name} acknowledged the alert ({via})",
            detail={"alert_id": alert.id, "via": via},
        ),
    )
    bus.publish(
        EventType.ALERT_ACKNOWLEDGED,
        alert.senior_id,
        {"alert_id": alert.id, "caregiver_id": caregiver.id, "via": via},
    )
    return alert


def open_alerts_for(senior_id: str) -> list[Alert]:
    """Unacknowledged alerts, newest first."""
    return sorted(
        (a for a in store.alerts.values() if a.senior_id == senior_id and not a.resolved),
        key=lambda a: a.created_at,
        reverse=True,
    )


def latest_alert_for(senior_id: str) -> Optional[Alert]:
    rows = sorted(
        (a for a in store.alerts.values() if a.senior_id == senior_id),
        key=lambda a: a.created_at,
    )
    return rows[-1] if rows else None


async def escalate(alert: Alert) -> Optional[Caregiver]:
    """Nobody answered. Go to the next caregiver, one-to-one.

    Returns the caregiver we reached, or None when the chain is exhausted --
    at which point the alert is left open and the dashboard shows it red,
    because pretending an unanswered level-3 is resolved is the worst thing
    this system could do.
    """
    senior = store.get_senior(alert.senior_id)
    if not senior or alert.resolved:
        return None
    if not alert.pending_order:
        alert.escalate_after = None
        store.alerts[alert.id] = alert
        store.add_timeline(
            senior.id,
            TimelineEntry(
                at=now(),
                type=EventType.ALERT_ESCALATED,
                evaluation_id=alert.evaluation_id,
                summary="No caregiver answered and the escalation list is exhausted",
                detail={"alert_id": alert.id, "exhausted": True},
            ),
        )
        bus.publish(
            EventType.ALERT_ESCALATED,
            senior.id,
            {"alert_id": alert.id, "exhausted": True},
        )
        return None

    next_id = alert.pending_order.pop(0)
    nxt = next((cg for cg in senior.caregivers if cg.id == next_id), None)
    if not nxt:
        return await escalate(alert)

    missed = next(
        (cg for cg in senior.caregivers if cg.id == alert.notified[-1]), None
    )
    body = caretone.escalation_body(
        senior.display_name.split()[0],
        missed.name if missed else "the first contact",
        detail_link(senior.id, alert.evaluation_id),
    )
    result = await linq.send_direct(nxt.phone_e164, body)

    settings = get_settings()
    scale = max(settings.followup_time_scale, 0.001)
    alert.notified.append(nxt.id)
    alert.escalations += 1
    alert.escalate_after = now() + timedelta(
        minutes=settings.escalation_timeout_minutes / scale
    )
    store.alerts[alert.id] = alert

    store.add_timeline(
        senior.id,
        TimelineEntry(
            at=now(),
            type=EventType.ALERT_ESCALATED,
            evaluation_id=alert.evaluation_id,
            level=alert.level,
            summary=f"No answer, escalated to {nxt.name}",
            detail={"alert_id": alert.id, "to": nxt.id, "error": result.error},
        ),
    )
    bus.publish(
        EventType.ALERT_ESCALATED,
        senior.id,
        {"alert_id": alert.id, "to": nxt.id, "escalations": alert.escalations},
    )
    return nxt


async def sweep_escalations() -> list[str]:
    """Fire every alert whose clock has run out. Called by the scheduler tick."""
    fired: list[str] = []
    with _escalation_lock:
        due = [
            a for a in list(store.alerts.values())
            if not a.resolved and a.escalate_after and a.escalate_after <= now()
        ]
        # Claim before awaiting: the background scheduler and a hand-fired
        # tick must not both escalate the same alert and text the backup
        # caregiver twice. `escalate` sets a fresh deadline if it sends.
        for alert in due:
            alert.escalate_after = None
            store.alerts[alert.id] = alert

    for alert in due:
        await escalate(alert)
        fired.append(alert.id)
    return fired


async def retry_failed_deliveries() -> list[str]:
    """Re-send alerts that never made it out.

    Acknowledgement and delivery are different failures and are tracked
    separately: an alert nobody answered is a family that is not responding,
    but an alert that never sent is us failing silently, which is worse. This
    gives up after `alert_max_delivery_attempts` and marks the alert failed so
    the dashboard can show it red rather than pretending it went.
    """
    settings = get_settings()
    now_ts = now()
    due = [
        a for a in list(store.alerts.values())
        if a.delivery == "pending" and a.next_retry_at and a.next_retry_at <= now_ts
    ]
    retried: list[str] = []

    for alert in due:
        senior = store.get_senior(alert.senior_id)
        if not senior or alert.resolved:
            alert.delivery = "failed"
            store.alerts[alert.id] = alert
            continue

        # Claim before awaiting, so the background tick and a manual one do
        # not both re-send the same alert.
        alert.next_retry_at = None
        alert.delivery_attempts += 1
        store.alerts[alert.id] = alert

        circle_state = store.circles.get(senior.id)
        if circle_state and circle_state.chat_id:
            result = await linq.send_to_chat(circle_state.chat_id, alert.body)
        else:
            first = next((c for c in senior.caregivers if c.id in alert.notified), None)
            result = (
                await linq.send_direct(first.phone_e164, alert.body)
                if first else linq.LinqResult(ok=False, error="no caregiver to reach")
            )

        alert.last_error = result.error
        if result.ok:
            alert.delivery = "sent"
            alert.message_id = result.message_id or alert.message_id
            alert.chat_id = result.chat_id or alert.chat_id
            # The escalation clock only starts once someone has actually been
            # told; escalating for silence on a message that never arrived
            # would blame the family for our outage.
            if int(alert.level) >= int(ActionLevel.CALL_CLINIC) and not alert.acks:
                scale = max(settings.followup_time_scale, 0.001)
                alert.escalate_after = now() + timedelta(
                    minutes=settings.escalation_timeout_minutes / scale
                )
            log.info("alert %s delivered on attempt %d", alert.id, alert.delivery_attempts)
        elif alert.delivery_attempts >= settings.alert_max_delivery_attempts:
            alert.delivery = "failed"
            log.error(
                "alert %s undeliverable after %d attempts: %s",
                alert.id, alert.delivery_attempts, result.error,
            )
            store.add_timeline(
                senior.id,
                TimelineEntry(
                    at=now(), type=EventType.ALERT_ESCALATED,
                    evaluation_id=alert.evaluation_id, level=alert.level,
                    summary="Alert could not be delivered to the family",
                    detail={"alert_id": alert.id, "error": result.error},
                ),
            )
            bus.publish(
                EventType.ALERT_ESCALATED, senior.id,
                {"alert_id": alert.id, "undeliverable": True},
            )
        else:
            alert.next_retry_at = now() + timedelta(
                seconds=settings.alert_retry_seconds * alert.delivery_attempts
            )

        store.alerts[alert.id] = alert
        retried.append(alert.id)

    return retried
