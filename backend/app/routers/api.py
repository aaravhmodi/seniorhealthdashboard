from __future__ import annotations

import hashlib
import hmac
from typing import Optional

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)

from ..config import get_settings
from ..evidence import baseline_cards, faers_cards, neiss_cards
from ..extraction import extract
from ..handoff import build_packet, should_build
from .. import auth, caretone, circle, followup, linq, linq_events, links, llm, persistence, retrieval, voice
from ..ladder import evaluate
from ..notify import notify_caregivers
from ..persona import TEACH_BACK, voice_output_available
from ..schemas import (
    ActionLevel,
    Alert,
    BaselineSummary,
    CarePlan,
    CareCircle,
    CheckIn,
    CheckInSource,
    CheckInCreate,
    CheckInResponse,
    CircleEnrollRequest,
    EventType,
    EvidenceCard,
    Evaluation,
    FollowUpJob,
    HandoffPacket,
    Health,
    LinqInbound,
    Medication,
    ReminderRequest,
    ReminderResponse,
    Senior,
    SeniorAnswer,
    SeniorQuestion,
    TeachBackRequest,
    TeachBackResult,
    Timeline,
    TimelineEntry,
    WSEvent,
)
from ..events import bus
from ..seed import seed
from ..store import new_id, now, store

router = APIRouter()


def _get_senior(senior_id: str) -> Senior:
    senior = store.get_senior(senior_id)
    if not senior:
        raise HTTPException(status_code=404, detail=f"unknown senior {senior_id}")
    return senior


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------
@router.get("/healthz", response_model=Health, tags=["meta"])
def healthz() -> Health:
    return Health(
        mock_mode=get_settings().mock_mode,
        seeded_seniors=len(store.seniors),
        auth_mode=auth.mode(),
    )


# --------------------------------------------------------------------------
# Seniors
# --------------------------------------------------------------------------
@router.get("/seniors", response_model=list[Senior], tags=["seniors"])
def list_seniors(_: auth.Principal = Depends(auth.require_user)) -> list[Senior]:
    return store.list_seniors()


@router.post("/seniors", response_model=Senior, status_code=201, tags=["seniors"])
def create_senior(senior: Senior, _: auth.Principal = Depends(auth.require_user)) -> Senior:
    """Create a patient profile after a user completes onboarding."""
    existing = store.get_senior(senior.id)
    if existing:
        # Signup retries are safe, e.g. if a prior browser request timed out.
        return existing
    return store.put_senior(senior)


@router.put("/seniors/{senior_id}", response_model=Senior, tags=["seniors"])
def update_senior(
    senior_id: str, senior: Senior, _: auth.Principal = Depends(auth.require_user)
) -> Senior:
    """Replace a patient's editable profile details."""
    _get_senior(senior_id)
    if senior.id != senior_id:
        raise HTTPException(status_code=400, detail="senior ID does not match the request path")
    return store.put_senior(senior)


@router.get("/seniors/{senior_id}", response_model=Senior, tags=["seniors"])
def get_senior(senior_id: str, _: auth.Principal = Depends(auth.require_user)) -> Senior:
    return _get_senior(senior_id)


@router.get("/seniors/{senior_id}/timeline", response_model=Timeline, tags=["seniors"])
def get_timeline(
    senior_id: str, window_days: int = Query(default=14, ge=1, le=90), _: auth.Principal = Depends(auth.require_user)) -> Timeline:
    _get_senior(senior_id)
    return Timeline(
        senior_id=senior_id,
        entries=store.timeline_for(senior_id),
        baseline=store.baseline(senior_id, window_days),
    )


@router.get(
    "/seniors/{senior_id}/baseline", response_model=BaselineSummary, tags=["seniors"]
)
def get_baseline(
    senior_id: str, window_days: int = Query(default=14, ge=1, le=90), _: auth.Principal = Depends(auth.require_user)) -> BaselineSummary:
    _get_senior(senior_id)
    return store.baseline(senior_id, window_days)


@router.get(
    "/seniors/{senior_id}/checkins", response_model=list[CheckIn], tags=["seniors"]
)
def list_checkins(
    senior_id: str, limit: int = Query(default=20, ge=1, le=200), _: auth.Principal = Depends(auth.require_user)) -> list[CheckIn]:
    _get_senior(senior_id)
    return store.checkins_for(senior_id, limit)


# --------------------------------------------------------------------------
# Check-ins -- the one endpoint the whole demo runs through
# --------------------------------------------------------------------------
@router.post("/checkins", response_model=CheckInResponse, tags=["checkins"])
async def create_checkin(payload: CheckInCreate, _: auth.Principal = Depends(auth.require_user)) -> CheckInResponse:
    senior = _get_senior(payload.senior_id)

    language = payload.language or senior.preferred_language
    transcript = None

    if payload.audio_url and not payload.text:
        if not voice.is_enabled():
            raise HTTPException(
                status_code=503,
                detail="DEEPGRAM_API_KEY is not configured; send text instead",
            )
        try:
            transcript = voice.transcribe_url(payload.audio_url, language)
        except voice.DeepgramError as exc:
            # Loud, not silent. A dropped transcription must never look like a
            # patient who reported nothing.
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        payload = payload.model_copy(
            update={
                "text": transcript.text,
                "transcript_confidence": transcript.confidence,
                "source": CheckInSource.VOICE,
            }
        )

    # The lexicon always runs; the LLM only adds to it. See llm.extract_symptoms.
    extraction = llm.extract_symptoms(payload.text, language)

    checkin = CheckIn(
        id=new_id("chk"),
        senior_id=senior.id,
        created_at=now(),
        source=payload.source,
        language=language,
        raw_text=payload.text,
        transcript_confidence=payload.transcript_confidence,
        symptoms=extraction.value,
        vitals=payload.vitals,
        meds_taken_today=payload.meds_taken_today or [],
        extraction_model=("llm+lexicon" if extraction.used_model else "lexicon"),
        client_ref=payload.client_ref,
    )
    store.put_checkin(checkin)
    retrieval.ingest_patient_history(senior, [checkin])
    bus.publish(
        EventType.CHECKIN_CREATED,
        senior.id,
        {"checkin_id": checkin.id, "client_ref": checkin.client_ref},
    )

    previous = store.latest_evaluation(senior.id)
    previous_level = previous.level if previous else None
    evaluation = evaluate(
        checkin, senior, store.baseline(senior.id), previous_level=previous_level,
        language=language,
    )
    context = retrieval.build_context(
        query=checkin.raw_text or "daily check-in",
        senior_id=senior.id,
        language=language,
    )
    phrased = llm.explain(
        level=evaluation.level,
        language=language,
        senior_name=senior.display_name,
        flags=evaluation.red_flags,
        evidence=evaluation.evidence,
        template_fallback=evaluation.explanation,
        context=context,
        symptoms=[s.label for s in checkin.symptoms],
    )
    evaluation.explanation = phrased.value
    evaluation.llm_used = phrased.used_model
    evaluation.llm_fallback_reason = phrased.fallback_reason
    evaluation.context_citations = context.citations
    store.put_evaluation(evaluation)

    store.add_timeline(
        senior.id,
        TimelineEntry(
            at=evaluation.created_at,
            type=EventType.EVALUATION_COMPLETED,
            checkin_id=checkin.id,
            evaluation_id=evaluation.id,
            level=evaluation.level,
            summary=", ".join(s.label for s in checkin.symptoms) or "no symptoms reported",
            detail={"source": checkin.source.value},
        ),
    )
    bus.publish(
        EventType.EVALUATION_COMPLETED,
        senior.id,
        {
            "evaluation_id": evaluation.id,
            "checkin_id": checkin.id,
            "level": int(evaluation.level),
            "level_label": evaluation.level_label,
        },
    )
    if previous_level is not None and previous_level != evaluation.level:
        bus.publish(
            EventType.LEVEL_CHANGED,
            senior.id,
            {"from": int(previous_level), "to": int(evaluation.level)},
        )

    notifications = await notify_caregivers(senior, evaluation)
    for receipt in notifications:
        store.add_timeline(
            senior.id,
            TimelineEntry(
                at=receipt.sent_at or now(),
                type=EventType.CAREGIVER_MESSAGE,
                evaluation_id=evaluation.id,
                summary=f"Notified {receipt.to} ({receipt.status})",
                detail={"body": receipt.body},
            ),
        )

    # If this check-in was answering a follow-up we sent, close that loop and
    # record whether they are worse than their own normal -- which is the
    # question the 24/72-hour cadence exists to ask.
    followup.record_followup_answer(
        senior.id, evaluation, store.baseline(senior.id).mean_level
    )

    if should_build(evaluation):
        # ED level means a discharge is coming, so queue the 24h and 72h
        # checks now rather than hoping someone remembers to.
        followup.schedule_after_discharge(senior, evaluation)
        packet = build_packet(
            senior,
            checkin,
            evaluation,
            store.baseline(senior.id),
            store.checkins_for(senior.id, 5),
        )
        store.handoffs[senior.id] = packet
        store.add_timeline(
            senior.id,
            TimelineEntry(
                at=packet.created_at,
                type=EventType.HANDOFF_READY,
                evaluation_id=evaluation.id,
                level=evaluation.level,
                summary="ED handoff packet generated",
                detail={"handoff_id": packet.id},
            ),
        )
        bus.publish(
            EventType.HANDOFF_READY,
            senior.id,
            {"handoff_id": packet.id, "level": int(evaluation.level)},
        )

    return CheckInResponse(
        checkin=checkin, evaluation=evaluation, notifications=notifications
    )


@router.get("/checkins/{checkin_id}", response_model=CheckInResponse, tags=["checkins"])
def get_checkin(
    checkin_id: str,
    language: str | None = None,
    _: auth.Principal = Depends(auth.require_user),
) -> CheckInResponse:
    checkin = store.checkins.get(checkin_id)
    if not checkin:
        raise HTTPException(status_code=404, detail="unknown check-in")
    evaluation = store.evaluation_for_checkin(checkin_id)
    if not evaluation:
        raise HTTPException(status_code=409, detail="check-in has no evaluation yet")
    if language:
        senior = _get_senior(checkin.senior_id)
        evaluation = evaluate(
            checkin, senior, store.baseline(senior.id),
            previous_level=evaluation.previous_level, language=language,
        )
    return CheckInResponse(checkin=checkin, evaluation=evaluation)


@router.get(
    "/seniors/{senior_id}/latest-evaluation", response_model=Evaluation, tags=["checkins"]
)
def latest_evaluation(senior_id: str, _: auth.Principal = Depends(auth.require_user)) -> Evaluation:
    _get_senior(senior_id)
    ev = store.latest_evaluation(senior_id)
    if not ev:
        raise HTTPException(status_code=404, detail="no evaluations yet")
    return ev


# --------------------------------------------------------------------------
# Handoff
# --------------------------------------------------------------------------
@router.get("/handoff/{senior_id}", response_model=HandoffPacket, tags=["handoff"])
def get_handoff(senior_id: str, force: bool = False, _: auth.Principal = Depends(auth.require_user)) -> HandoffPacket:
    senior = _get_senior(senior_id)
    packet = store.handoffs.get(senior_id)
    if packet and not force:
        return packet

    evaluation = store.latest_evaluation(senior_id)
    checkins = store.checkins_for(senior_id, 5)
    if not evaluation or not checkins:
        raise HTTPException(status_code=404, detail="nothing to hand off yet")
    if not should_build(evaluation) and not force:
        # Don't manufacture an ED packet for someone who is fine; ?force=true
        # exists so a clinician can pull one anyway.
        raise HTTPException(
            status_code=404,
            detail="latest evaluation is below ED level; pass ?force=true to build anyway",
        )
    packet = build_packet(
        senior, checkins[0], evaluation, store.baseline(senior_id), checkins
    )
    store.handoffs[senior_id] = packet
    return packet


# --------------------------------------------------------------------------
# Internal evidence surface (the data track builds against these)
# --------------------------------------------------------------------------
@router.get("/evidence/preview", response_model=list[EvidenceCard], tags=["evidence"])
def evidence_preview(senior_id: str, text: str, _: auth.Principal = Depends(auth.require_user)) -> list[EvidenceCard]:
    """Run the evidence lookups against ad-hoc text without storing a check-in."""
    senior = _get_senior(senior_id)
    probe = CheckIn(
        id="chk_preview",
        senior_id=senior.id,
        created_at=now(),
        source="text",
        language=senior.preferred_language,
        raw_text=text,
        symptoms=extract(text, senior.preferred_language),
    )
    baseline = store.baseline(senior.id)
    return (
        neiss_cards(probe, senior)
        + faers_cards(probe, senior)
        + baseline_cards(baseline, probe)
    )


# --------------------------------------------------------------------------
# Care circle -- the Linq group chat, created when people put their numbers in
# --------------------------------------------------------------------------
@router.post("/circle/enroll", response_model=CareCircle, tags=["circle"])
async def circle_enroll(payload: CircleEnrollRequest, _: auth.Principal = Depends(auth.require_user)) -> CareCircle:
    """Sign-up posts here: caregivers go on the senior, the group chat opens.

    This is the moment the family side starts existing. Consent is recorded per
    caregiver, and a caregiver who did not consent is neither added to the chat
    nor ever messaged.
    """
    senior = _get_senior(payload.senior_id)
    circle.add_caregivers(senior, payload.caregivers)
    return await circle.open_circle(
        senior,
        include_patient=payload.include_patient,
        patient_phone=payload.patient_phone_e164,
    )


@router.get("/circle/{senior_id}", response_model=CareCircle, tags=["circle"])
def circle_get(senior_id: str, _: auth.Principal = Depends(auth.require_user)) -> CareCircle:
    _get_senior(senior_id)
    state = store.circles.get(senior_id)
    if not state:
        raise HTTPException(status_code=404, detail="no care circle for this senior")
    return state


@router.get("/circle/{senior_id}/alerts", response_model=list[Alert], tags=["circle"])
def circle_alerts(senior_id: str, open_only: bool = False, _: auth.Principal = Depends(auth.require_user)) -> list[Alert]:
    """What we sent the family, and who has answered.

    The dashboard's red state comes from here: an alert with no acknowledgement
    and an exhausted escalation chain is the one a nurse needs to see.
    """
    _get_senior(senior_id)
    if open_only:
        return circle.open_alerts_for(senior_id)
    return sorted(
        (a for a in store.alerts.values() if a.senior_id == senior_id),
        key=lambda a: a.created_at,
        reverse=True,
    )


@router.post("/circle/alerts/{alert_id}/ack", response_model=Alert, tags=["circle"])
def circle_ack(alert_id: str, caregiver_id: str = Body(..., embed=True), _: auth.Principal = Depends(auth.require_user)) -> Alert:
    """Acknowledge from the dashboard, for a caregiver who called instead."""
    alert = store.alerts.get(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="unknown alert")
    senior = _get_senior(alert.senior_id)
    caregiver = next((cg for cg in senior.caregivers if cg.id == caregiver_id), None)
    if not caregiver:
        raise HTTPException(status_code=404, detail="unknown caregiver")
    return circle.acknowledge(alert, caregiver, via="dashboard")


@router.post("/circle/alerts/{alert_id}/escalate", response_model=Alert, tags=["circle"])
async def circle_escalate(alert_id: str, _: auth.Principal = Depends(auth.require_user)) -> Alert:
    """Escalate now rather than waiting out the clock. Used on stage, and by a
    nurse who already knows the first caregiver is unreachable."""
    alert = store.alerts.get(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="unknown alert")
    await circle.escalate(alert)
    return store.alerts[alert_id]


# --------------------------------------------------------------------------
# Post-discharge follow-up and teach-back
# --------------------------------------------------------------------------
@router.get(
    "/seniors/{senior_id}/followups", response_model=list[FollowUpJob], tags=["followup"]
)
def list_followups(senior_id: str, _: auth.Principal = Depends(auth.require_user)) -> list[FollowUpJob]:
    _get_senior(senior_id)
    return followup.jobs_for(senior_id)


@router.post(
    "/seniors/{senior_id}/followups", response_model=list[FollowUpJob], tags=["followup"]
)
def schedule_followups(senior_id: str, _: auth.Principal = Depends(auth.require_user)) -> list[FollowUpJob]:
    """Queue the 24h and 72h checks by hand, for a discharge we did not see."""
    senior = _get_senior(senior_id)
    evaluation = store.latest_evaluation(senior_id)
    if not evaluation:
        raise HTTPException(status_code=409, detail="no evaluation to follow up on")
    return followup.schedule_after_discharge(senior, evaluation)


@router.get("/seniors/{senior_id}/care-plan", response_model=CarePlan, tags=["followup"])
def get_care_plan(senior_id: str, _: auth.Principal = Depends(auth.require_user)) -> CarePlan:
    _get_senior(senior_id)
    return followup.care_plan_for(senior_id)


@router.put("/seniors/{senior_id}/care-plan", response_model=CarePlan, tags=["followup"])
def put_care_plan(senior_id: str, payload: CarePlan, _: auth.Principal = Depends(auth.require_user)) -> CarePlan:
    """The discharge instructions the teach-back is scored against.

    Stored in the words the senior was actually given them in -- scoring a
    teach-back against a paraphrase we invented would fail people for our own
    rewording.
    """
    _get_senior(senior_id)
    plan = payload.model_copy(update={"senior_id": senior_id, "updated_at": now()})
    store.care_plans[senior_id] = plan
    return plan


@router.post("/teachback", response_model=TeachBackResult, tags=["followup"])
async def teach_back(payload: TeachBackRequest, _: auth.Principal = Depends(auth.require_user)) -> TeachBackResult:
    """The senior says their instructions back; we check what survived.

    A miss texts the caregiver. We never tell the senior they got it wrong --
    the kiosk repeats the part that did not come back, which is what a nurse
    does, and is the whole reason teach-back beats "do you understand?".
    """
    senior = _get_senior(payload.senior_id)
    return await followup.run_teach_back(
        senior,
        payload.spoken,
        language=payload.language or senior.preferred_language,
        instructions=payload.instructions,
        followup_id=payload.followup_id,
    )


@router.get(
    "/seniors/{senior_id}/teachbacks", response_model=list[TeachBackResult],
    tags=["followup"],
)
def list_teachbacks(senior_id: str, _: auth.Principal = Depends(auth.require_user)) -> list[TeachBackResult]:
    _get_senior(senior_id)
    return list(reversed(store.teachbacks[senior_id]))


@router.get("/teachback/prompt/{senior_id}", tags=["followup"])
def teach_back_prompt(senior_id: str, language: Optional[str] = None, _: auth.Principal = Depends(auth.require_user)) -> dict:
    """What to ask, and what we will score it against.

    The kiosk needs both: the question in their language, and the instructions
    so it can read them aloud first.
    """
    senior = _get_senior(senior_id)
    lang = language or senior.preferred_language
    plan = followup.care_plan_for(senior_id)
    return {
        "prompt": TEACH_BACK.get(lang, TEACH_BACK["en"]),
        "language": lang,
        "instructions": plan.instructions,
        "voice_output": voice_output_available(lang),
    }


# --------------------------------------------------------------------------
# The caregiver view -- authenticated by the signed link, not by a session
# --------------------------------------------------------------------------
def _claims(token: str):
    """Read the link, or turn the failure into the right HTTP answer.

    401 for a forged or expired link and 503 for an unconfigured server are
    different problems and the family should be told which -- but neither
    message says anything about whether that senior exists.
    """
    if not links.configured():
        raise HTTPException(
            status_code=503, detail="caregiver links are not configured on this server"
        )
    try:
        return links.verify(token)
    except links.LinkError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/caregiver/{token}", tags=["caregiver"])
def caregiver_view(token: str) -> dict:
    """Everything the page behind a Linq text needs, in one call.

    One endpoint rather than four, because each separate call would be another
    surface to get the authorisation wrong on. The token names exactly one
    senior and nothing here takes an id from the caller.

    What is deliberately *not* returned: the caregivers' phone numbers. The
    page has no use for them, and a forwarded link should not hand out the
    family's contact details.
    """
    claims = _claims(token)
    senior = store.get_senior(claims.senior_id)
    if not senior:
        raise HTTPException(status_code=404, detail="unknown senior")

    evaluation = None
    if claims.evaluation_id:
        evaluation = store.evaluations.get(claims.evaluation_id)
        # A token for an evaluation that belongs to somebody else is a forged
        # token, however good its signature looks.
        if evaluation and evaluation.senior_id != senior.id:
            evaluation = None
    evaluation = evaluation or store.latest_evaluation(senior.id)

    circle_state = store.circles.get(senior.id)
    open_alerts = circle.open_alerts_for(senior.id)

    return {
        "senior": {
            "id": senior.id,
            "display_name": senior.display_name,
            "age": senior.age,
            "preferred_language": senior.preferred_language,
        },
        "evaluation": evaluation.model_dump(mode="json") if evaluation else None,
        "baseline": store.baseline(senior.id).model_dump(mode="json"),
        "timeline": [
            e.model_dump(mode="json") for e in store.timeline_for(senior.id)[:8]
        ],
        "circle": {
            "members": [
                # Names and roles only. No handles.
                {"name": m.name, "role": m.role}
                for m in (circle_state.members if circle_state else [])
            ],
        },
        "open_alert": (
            {
                "id": open_alerts[0].id,
                "level": int(open_alerts[0].level),
                "created_at": open_alerts[0].created_at.isoformat(),
                "escalations": open_alerts[0].escalations,
                "acknowledged": bool(open_alerts[0].acks),
            }
            if open_alerts else None
        ),
        "acknowledged": bool(store.alerts and not open_alerts),
    }


@router.post("/caregiver/{token}/ack", tags=["caregiver"])
def caregiver_ack(token: str) -> dict:
    """"I have seen this", from the page instead of from a tapback.

    The caregiver may well open the link rather than tap back, and making her
    do both would be a worse system. Credited to the first caregiver in the
    escalation chain, since the link does not identify which of them opened
    it -- what stops the clock is that *somebody* responded.
    """
    claims = _claims(token)
    senior = store.get_senior(claims.senior_id)
    if not senior:
        raise HTTPException(status_code=404, detail="unknown senior")

    open_alerts = circle.open_alerts_for(senior.id)
    if not open_alerts:
        return {"ok": True, "acknowledged": True, "note": "nothing was waiting"}

    chain = circle.escalation_chain(senior, open_alerts[0].level)
    if not chain:
        raise HTTPException(status_code=409, detail="no caregiver to credit")
    alert = circle.acknowledge(open_alerts[0], chain[0], via="dashboard")
    return {"ok": True, "acknowledged": True, "alert_id": alert.id}


@router.get("/caregiver/{token}/care-plan", tags=["caregiver"])
def caregiver_care_plan(token: str) -> dict:
    """The discharge instructions, for the caregiver going over them."""
    claims = _claims(token)
    plan = followup.care_plan_for(claims.senior_id)
    return {"instructions": plan.instructions, "routines": plan.routines}


# --------------------------------------------------------------------------
# Linq inbound webhook (backend-only)
# --------------------------------------------------------------------------
@router.post("/webhooks/linq", tags=["webhooks"])
async def linq_webhook(
    request: Request,
    x_linq_signature: Optional[str] = Header(default=None),
    webhook_id: Optional[str] = Header(default=None, alias="webhook-id"),
    webhook_timestamp: Optional[str] = Header(default=None, alias="webhook-timestamp"),
    webhook_signature: Optional[str] = Header(default=None, alias="webhook-signature"),
) -> dict:
    """Everything the family sends back: tapbacks, replies, photos.

    Linq retries a non-2xx for twenty-five minutes, so an event we simply do
    not act on is answered 200 with `handled: false` rather than a 4xx.
    """
    settings = get_settings()
    raw = await request.body()

    if not settings.mock_mode:
        # Standard Webhooks first, since that is what Linq actually sends; the
        # Sprint-0 X-Linq-Signature HMAC stays valid so the Postman collection
        # and any existing integration keep working.
        ok = linq.verify_signature(
            raw, webhook_id, webhook_timestamp, webhook_signature,
            linq.webhook_secret(),
        )
        if not ok:
            expected = hmac.new(
                linq.webhook_secret().encode(), raw, hashlib.sha256
            ).hexdigest()
            ok = bool(x_linq_signature) and hmac.compare_digest(expected, x_linq_signature)
        if not ok:
            raise HTTPException(status_code=401, detail="bad signature")

    try:
        envelope = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body is not JSON")

    # Linq delivers at-least-once and retries a non-2xx for ~25 minutes, so the
    # same event arriving twice is normal. Acknowledging twice is harmless;
    # answering "where is Dad?" twice texts an anxious family twice.
    if linq_events.already_handled(webhook_id):
        return {"ok": True, "handled": False, "reason": "duplicate delivery"}

    payload = linq_events.normalize(envelope)
    if payload is None:
        return {"ok": True, "handled": False, "reason": "event not actionable"}

    senior, caregiver = linq_events.resolve(payload)
    if not senior:
        raise HTTPException(status_code=404, detail="no senior for that thread")

    chat_id = (store.circles.get(senior.id).chat_id if store.circles.get(senior.id) else None)
    actions: list[str] = []

    # -- a tapback: the cheapest acknowledgement there is ------------------
    if payload.reaction:
        if caregiver and linq_events.is_ack_reaction(payload.reaction):
            alert = linq_events.alert_for_reaction(senior, payload)
            if alert:
                circle.acknowledge(alert, caregiver, via="tapback", detail=payload.reaction)
                actions.append("acknowledged")
        return {
            "ok": True, "handled": bool(actions), "senior_id": senior.id,
            "actions": actions, "reaction": payload.reaction,
        }

    # -- a photo of the pill bottles --------------------------------------
    added: list[Medication] = []
    if payload.media_urls:
        store.add_timeline(
            senior.id,
            TimelineEntry(
                at=payload.received_at or now(),
                type=EventType.MEDS_UPDATED,
                summary=f"{caregiver.name if caregiver else 'Caregiver'} sent a photo "
                        f"of the medicine list (parsing lands in Sprint 3)",
                detail={"media_count": len(payload.media_urls), "pending": True},
            ),
        )
        bus.publish(
            EventType.MEDS_UPDATED,
            senior.id,
            {"pending": True, "media_count": len(payload.media_urls)},
        )
        actions.append("media_recorded")

    # -- a reply --------------------------------------------------------
    intent = caretone.detect_intent(payload.text) if payload.text else None
    if payload.text:
        store.add_timeline(
            senior.id,
            TimelineEntry(
                at=payload.received_at or now(),
                type=EventType.CAREGIVER_MESSAGE,
                summary=f"Inbound from {caregiver.name if caregiver else 'caregiver'}",
                detail={"text": payload.text, "intent": intent},
            ),
        )
        bus.publish(
            EventType.CAREGIVER_MESSAGE,
            senior.id,
            {"direction": "inbound", "text": payload.text, "intent": intent},
        )

    if intent == "ack" and caregiver:
        open_alerts = circle.open_alerts_for(senior.id)
        if open_alerts:
            circle.acknowledge(open_alerts[0], caregiver, via="text", detail=payload.text)
            actions.append("acknowledged")
        else:
            reminder = next(
                (item for item in store.reminders.values()
                 if item.get("senior_id") == senior.id
                 and item.get("recipient") == payload.from_phone_e164
                 and item.get("status") == "sent"),
                None,
            )
            if reminder:
                reminder["status"] = "acknowledged"
                reminder["ack_text"] = payload.text
                reminder["ack_at"] = (payload.received_at or now()).isoformat()
                actions.append("reminder_acknowledged")
    elif intent == "ack":
        reminder = next(
            (item for item in store.reminders.values()
             if item.get("senior_id") == senior.id
             and item.get("recipient") == payload.from_phone_e164
             and item.get("status") == "sent"),
            None,
        )
        if reminder:
            reminder["status"] = "acknowledged"
            reminder["ack_text"] = payload.text
            reminder["ack_at"] = (payload.received_at or now()).isoformat()
            actions.append("reminder_acknowledged")
    elif intent == "status":
        # "Where is Dad?" -- the most-used feature on the family side.
        if await linq_events.answer_status(senior, chat_id, payload.from_phone_e164):
            actions.append("status_sent")
    elif intent == "call":
        if await linq_events.answer_call_request(senior, chat_id, payload.from_phone_e164):
            actions.append("call_requested")
    elif intent == "help":
        if await linq_events.answer_help(senior, chat_id, payload.from_phone_e164):
            actions.append("help_sent")
    elif payload.text and not intent:
        if await linq_events.answer_conversation(senior, chat_id, payload.from_phone_e164):
            actions.append("conversation_replied")
    elif intent == "stop" and caregiver:
        # Consent withdrawn in the only place they can withdraw it: the thread.
        senior.consent[f"share_with:{caregiver.id}"] = False
        store.put_senior(senior)
        actions.append("opted_out")

    return {
        "ok": True,
        "handled": bool(actions),
        "senior_id": senior.id,
        "medications_added": len(added),
        "intent": intent,
        "actions": actions,
    }



# --------------------------------------------------------------------------
# Demo controls -- handy on stage, and the reset the UI needs between runs
# --------------------------------------------------------------------------
@router.post("/demo/tick", tags=["demo"])
async def demo_tick() -> dict:
    """Advance the scheduler by hand: send what is due, escalate what is late.

    The background loop does this every few seconds anyway. On stage you want
    it to happen on a keystroke, and in a test you want it without a sleep.
    """
    return await followup.tick()


@router.get("/linq/status", tags=["demo"])
async def linq_status() -> dict:
    """Is the family side real right now, and from which number.

    The pitch slide claims Linq is live; this is the endpoint that proves it
    rather than asserting it.
    """
    settings = get_settings()
    return {
        "enabled": linq.is_enabled(),
        "mock_mode": settings.mock_mode,
        "api_key_configured": bool(settings.linq_api_key),
        "sending_number": await linq.sending_number(),
        "care_team_number": settings.linq_care_team_number or None,
        "subscribed_events": linq_events.SUBSCRIBED_EVENTS,
        "circles": len(store.circles),
        "open_alerts": sum(1 for a in store.alerts.values() if not a.resolved),
        "scheduled_followups": sum(
            1 for j in store.followups.values() if j.status == "scheduled"
        ),
        "followup_time_scale": settings.followup_time_scale,
        "escalation_timeout_minutes": settings.escalation_timeout_minutes,
    }


@router.post("/reminders/send", response_model=ReminderResponse, tags=["demo"])
async def send_reminder(
    payload: ReminderRequest, _: auth.Principal = Depends(auth.require_user)
) -> ReminderResponse:
    """Send an answerable reminder through the local mock or Linq transport.

    The local Docker demo uses Linq's deterministic mock path; production uses
    the configured API key. The body never contains diagnoses or medication
    names, and always tells the recipient how to answer.
    """
    senior = _get_senior(payload.senior_id)
    caregivers = circle.escalation_chain(senior, ActionLevel.CALL_CLINIC)
    caregiver = caregivers[0] if caregivers else None
    recipient_phones: list[str] = []
    if payload.recipient in {"self", "both"} and senior.phone_e164:
        recipient_phones.append(senior.phone_e164)
    if payload.recipient in {"caregiver", "both"} and caregiver and caregiver.phone_e164:
        recipient_phones.append(caregiver.phone_e164)
    # Preserve the original reminder behavior for older enrolled records that
    # have no patient phone but do have a care-circle phone.
    if payload.recipient == "self" and not recipient_phones and caregiver and caregiver.phone_e164:
        recipient_phones.append(caregiver.phone_e164)
    if not recipient_phones:
        target = "emergency-contact caregiver" if payload.recipient == "caregiver" else "selected recipient"
        raise HTTPException(status_code=409, detail=f"no phone is enrolled for the {target}")
    body = caretone.reminder_body(
        senior.display_name.split()[0], payload.kind, circle.detail_link(senior.id), payload.language
    )
    circle_state = store.circles.get(senior.id)
    results = [await linq.send_direct(phone, body) for phone in dict.fromkeys(recipient_phones)]
    for phone, result in zip(dict.fromkeys(recipient_phones), results):
        if result.message_id:
            store.reminders[result.message_id] = {
                "senior_id": senior.id, "kind": payload.kind, "recipient": phone,
                "status": "sent" if result.ok else "failed", "body": body,
            }
    ok = all(result.ok for result in results)
    message_ids = [result.message_id for result in results if result.message_id]
    errors = [result.error for result in results if result.error]
    return ReminderResponse(
        ok=ok,
        mocked=all(result.mocked for result in results),
        kind=payload.kind,
        recipient=", ".join(dict.fromkeys(recipient_phones)),
        recipients=list(dict.fromkeys(recipient_phones)),
        message_id=message_ids[0] if message_ids else None,
        body=body,
        error="; ".join(errors) if errors else None,
    )


@router.post("/demo/reset", tags=["demo"])
async def demo_reset() -> Health:
    store.seniors.clear()
    store.checkins.clear()
    store.evaluations.clear()
    store.handoffs.clear()
    store.timeline.clear()
    store.circles.clear()
    store.alerts.clear()
    store.followups.clear()
    store.care_plans.clear()
    store.teachbacks.clear()
    store.reminders.clear()
    # A reset that leaves yesterday's rows in Postgres is not a reset: the
    # next boot would hydrate them straight back.
    await persistence.wipe()
    seed(store)
    retrieval_reindex()
    return Health(mock_mode=get_settings().mock_mode, seeded_seniors=len(store.seniors))


@router.get("/demo/scenarios", tags=["demo"])
def demo_scenarios() -> list[dict]:
    """Canned inputs for the demo, so nobody improvises into a level-1 answer."""
    return [
        {
            "name": "Rosa -- atypical cardiac (the wow moment)",
            "senior_id": "sen_rosa",
            "language": "es",
            "source": "voice",
            "text": "Me falta el aire y tengo nausea, y estoy sudando frio.",
            "expect_level": 3,
        },
        {
            "name": "Rosa -- routine day",
            "senior_id": "sen_rosa",
            "language": "es",
            "source": "voice",
            "text": "Hoy me siento bien, solo un poco de dolor de espalda leve.",
            "expect_level": 1,
        },
        {
            "name": "Rosa -- dizziness on furosemide + lisinopril",
            "senior_id": "sen_rosa",
            "language": "es",
            "source": "text",
            "text": "Tengo mareo fuerte cuando me levanto y no tengo hambre.",
            "expect_level": 2,
        },
        {
            "name": "Wei -- fall on warfarin",
            "senior_id": "sen_chen",
            "language": "en",
            "source": "text",
            "text": "I fell in the bathroom this morning and hit my head.",
            "expect_level": 3,
        },
        {
            "name": "Henriette -- French intake, fall with head strike",
            "senior_id": "sen_henriette",
            "language": "fr",
            "source": "voice",
            "text": "Je suis tombee dans la cuisine et je me suis cogne la tete.",
            "expect_level": 3,
        },
        {
            "name": "Henriette -- quiet French day",
            "senior_id": "sen_henriette",
            "language": "fr",
            "source": "voice",
            "text": "Tout va bien aujourd'hui, juste un peu mal au dos.",
            "expect_level": 1,
        },
        {
            "name": "Wei -- new confusion with urinary symptoms (UTI delirium)",
            "senior_id": "sen_chen",
            "language": "en",
            "source": "voice",
            "text": "He is suddenly confused today and says burning when i pee.",
            "expect_level": 3,
        },
        {
            "name": "Walter -- stroke signs",
            "senior_id": "sen_walter",
            "language": "en",
            "source": "voice",
            "text": "My face is drooping and my arm is weak on one side, slurred speech.",
            "expect_level": 4,
        },
    ]


# --------------------------------------------------------------------------
# Retrieval and voice-agent wiring
# --------------------------------------------------------------------------
@router.get("/voice/coverage", tags=["voice"])
def voice_coverage() -> dict:
    """Which languages we can hear, and which we can speak back.

    Sourced from Deepgram's models endpoint, not from a marketing page. The UI
    uses `text_only` to decide when to show the "you can also type" path.
    """
    return voice.coverage()


@router.post("/voice/transcribe", tags=["voice"])
async def voice_transcribe(
    file: UploadFile = File(...),
    language: str = Form("en"),
    detect_language: bool = Form(False),
) -> dict:
    """Transcribe an uploaded recording without creating a check-in.

    The UI uses this to show the senior what we heard before anything is
    submitted -- confirming what we understood is part of the persona, not a
    debugging aid.
    """
    if not voice.is_enabled():
        raise HTTPException(status_code=503, detail="DEEPGRAM_API_KEY is not configured")
    audio = await file.read()
    try:
        result = voice.transcribe_bytes(
            audio,
            content_type=file.content_type or "audio/wav",
            language=language,
            detect=detect_language,
        )
    except voice.DeepgramError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "text": result.text,
        "confidence": round(result.confidence, 3),
        "language": result.language,
        "detected_language": result.detected_language,
        "duration_s": result.duration_s,
        "model": result.model,
        "low_confidence": result.is_low_confidence,
    }


@router.post("/voice/speak", tags=["voice"])
def voice_speak(text: str = Body(..., embed=True), language: str = Body("en", embed=True)):
    """Speak a line back. 409 when the language has no voice, so the UI can
    render text rather than play silence."""
    if not voice.is_enabled():
        raise HTTPException(status_code=503, detail="DEEPGRAM_API_KEY is not configured")
    if not voice_output_available(language):
        raise HTTPException(
            status_code=409,
            detail=f"no Deepgram voice for {language!r}; render this as text",
        )
    try:
        audio = voice.speak(text, language)
    except voice.DeepgramError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=audio, media_type="audio/wav")


@router.get("/voice/agent-config/{senior_id}", tags=["voice"])
def voice_agent_config(senior_id: str, _: auth.Principal = Depends(auth.require_user)) -> dict:
    """The Settings frame the client sends Deepgram to open a voice session.

    Built per senior: their language, their retrieved history, our system
    prompt, and senior-tuned endpointing. `_meta.voice_output` is false when
    Deepgram has no voice for that language -- render text instead.
    """
    senior = _get_senior(senior_id)
    return llm.voice_agent_config(senior, store.checkins_for(senior_id, 10))


@router.get("/retrieval/search", tags=["retrieval"])
def retrieval_search(
    q: str,
    senior_id: Optional[str] = None,
    k: int = Query(default=5, ge=1, le=20),
    language: str = "en",
) -> dict:
    """Inspect what the model would be given.

    Invaluable when an answer looks wrong: nine times out of ten the retrieval
    is the problem, not the prompt.
    """
    context = retrieval.build_context(q, senior_id=senior_id, language=language, k=k)
    return {
        "query": q,
        "embedder": type(retrieval.store.embedder).__name__,
        "indexed_chunks": len(retrieval.store.chunks),
        "results": [
            {
                "score": round(score, 4),
                "kind": chunk.kind,
                "source": chunk.source,
                "text": chunk.text,
            }
            for chunk, score in zip(context.chunks, context.scores)
        ],
    }


@router.post("/questions/answer", response_model=SeniorAnswer, tags=["retrieval"])
def answer_question(payload: SeniorQuestion, _: auth.Principal = Depends(auth.require_user)) -> SeniorAnswer:
    """Grounded Q&A over senior history, medicines, and loaded datasets."""
    if payload.senior_id:
        _get_senior(payload.senior_id)
    context = retrieval.build_context(
        payload.question,
        senior_id=payload.senior_id,
        language=payload.language,
        k=5,
    )
    result = llm.answer_senior_question(
        payload.question, context, language=payload.language
    )
    return SeniorAnswer(
        answer=str(result.value),
        citations=context.citations,
        used_model=result.used_model,
        fallback_reason=result.fallback_reason,
    )


@router.post("/retrieval/reindex", tags=["retrieval"])
def retrieval_reindex() -> dict:
    """Rebuild the index from whatever is in the store. Cheap; run it freely."""
    retrieval.store.clear()
    total = 0
    for senior in store.list_seniors():
        total += retrieval.ingest_medications(senior)
        total += retrieval.ingest_patient_history(senior, store.checkins_for(senior.id))
    total += retrieval.ingest_guidelines(demo_guidelines())

    # Real NEISS cases, when the warehouse has them. Silently zero otherwise,
    # which is the same degradation story as the evidence cards.
    from ..datasets.lookup import cohort_rows, narrative_rows
    from ..datasets import model as outcome_model

    cases = retrieval.ingest_neiss_narratives(narrative_rows())
    total += cases
    stats = retrieval.ingest_cohort_stats(cohort_rows())
    total += stats
    model_metrics = outcome_model.refresh()

    return {
        "indexed_chunks": total,
        "neiss_cases": cases,
        "cohort_stats": stats,
        "outcome_model": model_metrics,
        "embedder": type(retrieval.store.embedder).__name__,
    }


def demo_guidelines() -> list[dict]:
    """Hand-written, citable guidance chunks.

    Real guideline ingestion is a Sprint 2 pipeline. These exist so the
    retrieval path is exercised end to end from day one, and each carries a
    source the agent can quote.
    """
    return [
        {
            "id": "fall_anticoag",
            "text": ("An older adult who falls while taking a blood thinner should be "
                     "assessed the same day even if they feel fine, because bleeding "
                     "inside the head can appear hours later."),
            "source": "hand-written demo guideline (replace with a real source)",
        },
        {
            "id": "delirium_uti",
            "text": ("Sudden confusion in an older adult is often the first and only "
                     "sign of an infection such as a urinary tract infection, rather "
                     "than a change in their memory."),
            "source": "hand-written demo guideline (replace with a real source)",
        },
        {
            "id": "atypical_mi",
            "text": ("Older adults having a heart attack often have no chest pain. "
                     "Breathlessness, nausea, sweating or sudden tiredness can be the "
                     "only signs."),
            "source": "hand-written demo guideline (replace with a real source)",
        },
        {
            "id": "orthostatic",
            "text": ("Dizziness on standing in an older adult taking a water pill and "
                     "a blood pressure medicine is often a blood pressure drop, and is "
                     "worth a medication review."),
            "source": "hand-written demo guideline (replace with a real source)",
        },
    ]


@router.get("/evidence/similar-cases", tags=["evidence"])
def evidence_similar_cases(
    text: str,
    k: int = Query(default=5, ge=1, le=20),
) -> dict:
    """Real injury cases that read like this one, and how they ended.

    Empty until the NEISS loader has run and the index has been rebuilt. The
    admitted share is over the retrieved set, not a cohort rate -- quote
    `/datasets/status` cohort numbers for that.
    """
    return retrieval.similar_cases(text, k=k)


@router.get("/datasets/status", tags=["datasets"])
def datasets_status() -> dict:
    """Which real tables are loaded, and therefore which cards are real.

    The UI shows a mock badge for any evidence whose source starts with MOCK;
    this endpoint is how the pitch slide states what is actually behind the
    numbers.
    """
    from ..datasets.lookup import available
    from ..datasets.warehouse import status

    from ..datasets import model as outcome_model

    return {
        "warehouse": status(),
        "tables_available": available(),
        "outcome_model": outcome_model.status(),
        "persistence": persistence.status(),
    }


@router.get("/events/recent", response_model=list[WSEvent], tags=["events"])
def recent_events(limit: int = Query(default=25, ge=1, le=200), _: auth.Principal = Depends(auth.require_user)) -> list[WSEvent]:
    """Polling fallback, for when the WebSocket is inconvenient (or on stage)."""
    return bus.recent(limit)
