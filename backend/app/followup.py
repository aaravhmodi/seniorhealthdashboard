"""Post-discharge follow-up, and teach-back.

The loop this closes: a senior is sent to the emergency department, is seen,
goes home with instructions, and then nobody hears from them again until the
next visit. Roughly a fifth of Medicare patients are back within thirty days,
and the window where that is still preventable is the first three days.

So: an ED-level evaluation schedules a check-in at 24 hours. It asks the senior
how they are, runs the answer through the same ladder as any other check-in,
and compares it against their own baseline. Worse than baseline is the signal
-- not "is this bad", which we cannot answer, but "is this worse than this
person's normal", which we can.

The cadence is `FOLLOWUP_HOURS`, a list, and the code does not care how long it
is. It ships as `[24]`.

**Teach-back** rides along with the 24-hour check. We ask the senior to say
their discharge instructions back in their own words, transcribe it, and check
whether each instruction survived. Asking "do you understand?" gets a yes from
everyone; asking someone to repeat it back is the only cheap way to find the
instruction that did not land. A miss alerts the caregiver -- framed as the
instruction failing, because after an ED visit, at eighty, on four medicines,
it usually did.

On stage nobody waits a day. `FOLLOWUP_TIME_SCALE` divides every interval, so
`FOLLOWUP_TIME_SCALE=2880` puts the 24-hour check thirty seconds out, through
exactly the same code path.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import unicodedata
from datetime import timedelta
from typing import Iterable, Optional

from . import caretone, circle, linq
from .config import get_settings
from .events import bus
from .schemas import (
    ActionLevel,
    CarePlan,
    EventType,
    Evaluation,
    FollowUpJob,
    Senior,
    TeachBackItem,
    TeachBackResult,
    TimelineEntry,
)
from .persona import TEACH_BACK
from .store import new_id, now, store

# Words that carry no meaning for whether an instruction was understood.
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "do", "for", "from",
    "go", "if", "in", "is", "it", "me", "my", "no", "not", "of", "on", "or",
    "so", "that", "the", "then", "this", "to", "up", "was", "we", "with", "you",
    "your", "i", "am", "have", "has", "will", "can", "get", "take", "day",
    "de", "la", "el", "los", "las", "un", "una", "y", "o", "que", "es", "en",
    "por", "para", "con", "se", "su", "mi", "le", "les", "du", "et", "je",
}

# An instruction counts as covered when this share of its meaningful words
# came back. Two thirds is deliberately lenient: we are checking whether the
# idea survived, not whether they recited it.
COVERAGE_THRESHOLD = 0.6
# And the whole teach-back passes when this share of instructions is covered.
PASS_THRESHOLD = 0.7

# Guards the scheduled -> sending transition. See claim_due().
claim_lock = threading.Lock()
# How long a claimed job may sit un-delivered before it is retried.
CLAIM_TIMEOUT_S = 60

log = logging.getLogger("carepath.followup")


# --------------------------------------------------------------------------
# Teach-back
# --------------------------------------------------------------------------
def _normalize(text: str) -> str:
    """Fold accents and case so 'medicina' and 'MEDICINA' are one word.

    Spoken Spanish and French arrive from Deepgram with accents; a senior
    typing the same words often drops them. Neither should count as a miss.
    """
    folded = unicodedata.normalize("NFKD", text or "")
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return folded.lower()


def _content_words(text: str) -> list[str]:
    words = re.findall(r"[\w']+", _normalize(text))
    return [w for w in words if len(w) > 2 and w not in _STOPWORDS]


def score_teach_back(spoken: str, instructions: Iterable[str]) -> list[TeachBackItem]:
    """How much of each instruction came back, instruction by instruction.

    Word overlap, not an LLM call. This runs on a kiosk while someone is
    standing there, it has to be instant, and a deterministic score is one a
    clinician can argue with -- which they should be able to.
    """
    said = set(_content_words(spoken))
    items: list[TeachBackItem] = []
    for instruction in instructions:
        wanted = _content_words(instruction)
        if not wanted:
            continue
        matched = [w for w in wanted if w in said]
        score = len(matched) / len(wanted)
        items.append(
            TeachBackItem(
                instruction=instruction,
                covered=score >= COVERAGE_THRESHOLD,
                matched_terms=matched,
                score=round(score, 3),
            )
        )
    return items


def care_plan_for(senior_id: str) -> CarePlan:
    return store.care_plans.get(senior_id) or CarePlan(senior_id=senior_id)


async def run_teach_back(
    senior: Senior,
    spoken: str,
    language: str = "en",
    instructions: Optional[list[str]] = None,
    followup_id: Optional[str] = None,
) -> TeachBackResult:
    """Grade the teach-back, and text the caregiver when something is missing."""
    plan = care_plan_for(senior.id)
    lines = instructions if instructions is not None else plan.instructions
    items = score_teach_back(spoken, lines)
    covered = [i for i in items if i.covered]
    score = len(covered) / len(items) if items else 0.0
    missed = [i.instruction for i in items if not i.covered]

    result = TeachBackResult(
        senior_id=senior.id,
        at=now(),
        language=language,
        spoken=spoken,
        items=items,
        score=round(score, 3),
        # No instructions on file is not a pass. It means we have nothing to
        # check against, and saying "passed" would be a lie the dashboard
        # would then render green.
        passed=bool(items) and score >= PASS_THRESHOLD,
        missed=missed,
        prompt=TEACH_BACK.get(language, TEACH_BACK["en"]),
    )

    if items and not result.passed:
        result.caregiver_alerted = await _alert_teach_back_miss(senior)

    store.teachbacks[senior.id].append(result)
    if followup_id and followup_id in store.followups:
        job = store.followups[followup_id]
        job.status = "answered"
        job.answered_at = now()
        job.note = f"teach-back {int(score * 100)}%"
        store.followups[followup_id] = job

    store.add_timeline(
        senior.id,
        TimelineEntry(
            at=result.at,
            type=EventType.TEACHBACK_COMPLETED,
            summary=(
                f"Teach-back {'passed' if result.passed else 'missed'} "
                f"({len(covered)}/{len(items)} instructions)"
            ),
            detail={"missed": missed, "score": result.score},
        ),
    )
    bus.publish(
        EventType.TEACHBACK_COMPLETED,
        senior.id,
        {"score": result.score, "passed": result.passed, "missed": len(missed)},
    )
    return result


async def _alert_teach_back_miss(senior: Senior) -> bool:
    """Tell the caregiver an instruction did not land. Quietly, and once."""
    body = caretone.teachback_miss_body(
        senior.display_name.split()[0], circle.detail_link(senior.id)
    )
    circle_state = store.circles.get(senior.id)
    if circle_state and circle_state.chat_id:
        result = await linq.send_to_chat(circle_state.chat_id, body)
    else:
        chain = circle.escalation_chain(senior, ActionLevel.CALL_CLINIC)
        if not chain:
            return False
        result = await linq.send_direct(chain[0].phone_e164, body)
    return result.ok


# --------------------------------------------------------------------------
# Scheduling
# --------------------------------------------------------------------------
def schedule_after_discharge(
    senior: Senior, evaluation: Evaluation
) -> list[FollowUpJob]:
    """Queue the post-discharge check(s) after an ED-level evaluation.

    Re-evaluating at ED level while jobs are already pending does not stack a
    second set; the family does not need two texts for one episode.
    """
    pending = [
        j for j in store.followups.values()
        if j.senior_id == senior.id and j.status == "scheduled"
    ]
    if pending:
        return pending

    settings = get_settings()
    scale = max(settings.followup_time_scale, 0.001)
    jobs: list[FollowUpJob] = []
    for hours in settings.followup_hours:
        job = FollowUpJob(
            id=new_id("fup"),
            senior_id=senior.id,
            # A check within the first day carries the teach-back, while the
            # instructions are still something the senior is trying to follow
            # rather than something they have already settled into or lost.
            kind="teachback" if hours <= 24 else "checkin",
            due_at=now() + timedelta(hours=hours / scale),
            hours_after=hours,
            source_evaluation_id=evaluation.id,
        )
        store.followups[job.id] = job
        jobs.append(job)

    store.add_timeline(
        senior.id,
        TimelineEntry(
            at=now(),
            type=EventType.FOLLOWUP_SCHEDULED,
            evaluation_id=evaluation.id,
            summary=f"Follow-up scheduled at {', '.join(f'{int(h)}h' for h in settings.followup_hours)}",
            detail={"job_ids": [j.id for j in jobs], "time_scale": scale},
        ),
    )
    bus.publish(
        EventType.FOLLOWUP_SCHEDULED,
        senior.id,
        {"jobs": [j.id for j in jobs], "hours": settings.followup_hours},
    )
    return jobs


async def send_followup(job: FollowUpJob) -> FollowUpJob:
    """Text the circle that a scheduled check is happening now."""
    senior = store.get_senior(job.senior_id)
    if not senior:
        job.status = "cancelled"
        store.followups[job.id] = job
        return job

    body = caretone.followup_body(
        senior.display_name.split()[0],
        int(job.hours_after),
        circle.detail_link(senior.id),
    )
    circle_state = store.circles.get(senior.id)
    if circle_state and circle_state.chat_id:
        result = await linq.send_to_chat(circle_state.chat_id, body)
    else:
        chain = circle.escalation_chain(senior, ActionLevel.CALL_CLINIC)
        result = (
            await linq.send_direct(chain[0].phone_e164, body)
            if chain else linq.LinqResult(ok=False, error="no caregiver to notify")
        )

    job.status = "sent" if result.ok else "failed"
    job.sent_at = now()
    store.followups[job.id] = job

    store.add_timeline(
        senior.id,
        TimelineEntry(
            at=job.sent_at,
            type=EventType.FOLLOWUP_SENT,
            summary=f"{int(job.hours_after)}-hour follow-up sent ({job.status})",
            detail={"job_id": job.id, "kind": job.kind, "error": result.error},
        ),
    )
    bus.publish(
        EventType.FOLLOWUP_SENT,
        senior.id,
        {"job_id": job.id, "hours": job.hours_after, "kind": job.kind, "status": job.status},
    )
    return job


def record_followup_answer(
    senior_id: str, evaluation: Evaluation, baseline_mean: float
) -> Optional[FollowUpJob]:
    """Tie a check-in back to the follow-up it was answering.

    `worsened` is the whole point of the follow-up: not whether the
    level is high, but whether it is higher than this person's own trailing
    mean. Someone who lives at level 2 sitting at level 2 is fine. Someone who
    lives at level 1 sitting at level 2 is the call we want to have made.
    """
    open_jobs = sorted(
        (
            j for j in store.followups.values()
            if j.senior_id == senior_id and j.status == "sent"
        ),
        key=lambda j: j.due_at,
    )
    if not open_jobs:
        return None
    job = open_jobs[0]
    job.status = "answered"
    job.answered_at = now()
    job.result_level = evaluation.level
    job.worsened = int(evaluation.level) > round(baseline_mean)
    store.followups[job.id] = job
    return job


def due_jobs() -> list[FollowUpJob]:
    return [
        j for j in store.followups.values()
        if j.status == "scheduled" and j.due_at <= now()
    ]


def claim_due() -> list[FollowUpJob]:
    """Take ownership of every due job, atomically.

    The background scheduler and a hand-fired `/demo/tick` can run at the same
    moment. Without a claim they both see the same due job and the family gets
    the same text twice -- which, for a system whose whole promise is "we will
    tell you what is happening", is worse than being late.
    """
    claimed: list[FollowUpJob] = []
    stale = now() - timedelta(seconds=CLAIM_TIMEOUT_S)
    with claim_lock:
        for job in list(store.followups.values()):
            # A claim whose send never finished -- the process died, or the
            # transport hung past its own timeout -- goes back in the queue.
            # A follow-up that is silently stuck is indistinguishable from one
            # we never scheduled, which is the failure this whole module
            # exists to prevent.
            if job.status == "sending" and job.due_at <= stale:
                job.status = "scheduled"
            if job.status == "scheduled" and job.due_at <= now():
                job.status = "sending"
                store.followups[job.id] = job
                claimed.append(job)
    return claimed


def jobs_for(senior_id: str) -> list[FollowUpJob]:
    return sorted(
        (j for j in store.followups.values() if j.senior_id == senior_id),
        key=lambda j: j.due_at,
    )


# --------------------------------------------------------------------------
# The tick
# --------------------------------------------------------------------------
async def tick() -> dict:
    """One pass: send what is due, escalate what went unanswered.

    Separated from the loop so a test (and the demo `/demo/tick` endpoint) can
    advance time by calling it directly instead of sleeping.
    """
    sent = [(await send_followup(job)).id for job in claim_due()]
    redelivered = await circle.retry_failed_deliveries()
    escalated = await circle.sweep_escalations()
    return {
        "followups_sent": sent,
        "alerts_redelivered": redelivered,
        "alerts_escalated": escalated,
    }


async def scheduler(interval_s: float = 5.0) -> None:
    """Background loop, started in the app lifespan.

    Every failure is swallowed and logged into the next tick: a scheduler that
    dies on one bad job takes every future follow-up with it.
    """
    failures = 0
    while True:
        try:
            result = await tick()
            if any(result.values()):
                log.info("tick: %s", result)
            failures = 0
        except asyncio.CancelledError:
            raise
        except Exception:
            # Keep the loop alive -- a scheduler that dies on one bad job takes
            # every future follow-up with it -- but never silently. A silent
            # `except: pass` here is how a demo becomes unexplainable.
            failures += 1
            log.exception("scheduler tick failed (%d in a row)", failures)
        await asyncio.sleep(interval_s)
