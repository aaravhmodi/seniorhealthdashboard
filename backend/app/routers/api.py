from __future__ import annotations

import hashlib
import hmac
from typing import Optional

from fastapi import (
    APIRouter,
    Body,
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
from .. import llm, retrieval, voice
from ..ladder import evaluate
from ..notify import notify_caregivers
from ..persona import voice_output_available
from ..schemas import (
    BaselineSummary,
    CheckIn,
    CheckInSource,
    CheckInCreate,
    CheckInResponse,
    EventType,
    EvidenceCard,
    Evaluation,
    HandoffPacket,
    Health,
    LinqInbound,
    Medication,
    Senior,
    SeniorAnswer,
    SeniorQuestion,
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
        mock_mode=get_settings().mock_mode, seeded_seniors=len(store.seniors)
    )


# --------------------------------------------------------------------------
# Seniors
# --------------------------------------------------------------------------
@router.get("/seniors", response_model=list[Senior], tags=["seniors"])
def list_seniors() -> list[Senior]:
    return store.list_seniors()


@router.get("/seniors/{senior_id}", response_model=Senior, tags=["seniors"])
def get_senior(senior_id: str) -> Senior:
    return _get_senior(senior_id)


@router.get("/seniors/{senior_id}/timeline", response_model=Timeline, tags=["seniors"])
def get_timeline(
    senior_id: str, window_days: int = Query(default=14, ge=1, le=90)
) -> Timeline:
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
    senior_id: str, window_days: int = Query(default=14, ge=1, le=90)
) -> BaselineSummary:
    _get_senior(senior_id)
    return store.baseline(senior_id, window_days)


@router.get(
    "/seniors/{senior_id}/checkins", response_model=list[CheckIn], tags=["seniors"]
)
def list_checkins(
    senior_id: str, limit: int = Query(default=20, ge=1, le=200)
) -> list[CheckIn]:
    _get_senior(senior_id)
    return store.checkins_for(senior_id, limit)


# --------------------------------------------------------------------------
# Check-ins -- the one endpoint the whole demo runs through
# --------------------------------------------------------------------------
@router.post("/checkins", response_model=CheckInResponse, tags=["checkins"])
async def create_checkin(payload: CheckInCreate) -> CheckInResponse:
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
        checkin, senior, store.baseline(senior.id), previous_level=previous_level
    )
    context = retrieval.build_context(
        query=checkin.raw_text or "daily check-in",
        senior_id=senior.id,
        language=language,
    )
    phrased = llm.explain(
        level=evaluation.level,
        language=senior.preferred_language,
        senior_name=senior.display_name,
        flags=evaluation.red_flags,
        evidence=evaluation.evidence,
        template_fallback=evaluation.explanation,
        context=context,
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

    if should_build(evaluation):
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
def get_checkin(checkin_id: str) -> CheckInResponse:
    checkin = store.checkins.get(checkin_id)
    if not checkin:
        raise HTTPException(status_code=404, detail="unknown check-in")
    evaluation = store.evaluation_for_checkin(checkin_id)
    if not evaluation:
        raise HTTPException(status_code=409, detail="check-in has no evaluation yet")
    return CheckInResponse(checkin=checkin, evaluation=evaluation)


@router.get(
    "/seniors/{senior_id}/latest-evaluation", response_model=Evaluation, tags=["checkins"]
)
def latest_evaluation(senior_id: str) -> Evaluation:
    _get_senior(senior_id)
    ev = store.latest_evaluation(senior_id)
    if not ev:
        raise HTTPException(status_code=404, detail="no evaluations yet")
    return ev


# --------------------------------------------------------------------------
# Handoff
# --------------------------------------------------------------------------
@router.get("/handoff/{senior_id}", response_model=HandoffPacket, tags=["handoff"])
def get_handoff(senior_id: str, force: bool = False) -> HandoffPacket:
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
def evidence_preview(senior_id: str, text: str) -> list[EvidenceCard]:
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
# Linq inbound webhook (backend-only)
# --------------------------------------------------------------------------
@router.post("/webhooks/linq", tags=["webhooks"])
async def linq_webhook(
    request: Request,
    payload: LinqInbound,
    x_linq_signature: Optional[str] = Header(default=None),
) -> dict:
    settings = get_settings()
    if not settings.mock_mode:
        raw = await request.body()
        expected = hmac.new(
            settings.linq_webhook_secret.encode(), raw, hashlib.sha256
        ).hexdigest()
        if not x_linq_signature or not hmac.compare_digest(expected, x_linq_signature):
            raise HTTPException(status_code=401, detail="bad signature")

    senior, caregiver = store.caregiver_by_thread(payload.thread_id)
    if not senior:
        senior, caregiver = store.caregiver_by_phone(payload.from_phone_e164)
    if not senior:
        raise HTTPException(status_code=404, detail="no senior for that thread")

    # Sprint 3: a photo of the pill bottles goes to a vision model, gets
    # normalized against the ingredient dictionary and re-scored by FAERS.
    # Sprint 0 records the intent so the UI can render the pending state.
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

    if payload.text:
        store.add_timeline(
            senior.id,
            TimelineEntry(
                at=payload.received_at or now(),
                type=EventType.CAREGIVER_MESSAGE,
                summary=f"Inbound from {caregiver.name if caregiver else 'caregiver'}",
                detail={"text": payload.text},
            ),
        )
        bus.publish(
            EventType.CAREGIVER_MESSAGE,
            senior.id,
            {"direction": "inbound", "text": payload.text},
        )

    return {"ok": True, "senior_id": senior.id, "medications_added": len(added)}


# --------------------------------------------------------------------------
# Demo controls -- handy on stage, and the reset the UI needs between runs
# --------------------------------------------------------------------------
@router.post("/demo/reset", tags=["demo"])
def demo_reset() -> Health:
    store.seniors.clear()
    store.checkins.clear()
    store.evaluations.clear()
    store.handoffs.clear()
    store.timeline.clear()
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
def voice_agent_config(senior_id: str) -> dict:
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
def answer_question(payload: SeniorQuestion) -> SeniorAnswer:
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
    }


@router.get("/events/recent", response_model=list[WSEvent], tags=["events"])
def recent_events(limit: int = Query(default=25, ge=1, le=200)) -> list[WSEvent]:
    """Polling fallback, for when the WebSocket is inconvenient (or on stage)."""
    return bus.recent(limit)
