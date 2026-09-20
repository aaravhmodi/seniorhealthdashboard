"""The contract. Every field here is frozen for Sprint 0 -- the UI builds against this.

Additive changes (new optional fields) are fine mid-hack. Renames are not:
if you must rename, announce it in the team channel and bump CONTRACT_VERSION.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

CONTRACT_VERSION = "0.1.0"


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------
class ActionLevel(int, Enum):
    """The action ladder. Higher is more urgent. We never step a rung downward."""

    LOG = 1          # log it, keep watching
    CALL_CLINIC = 2  # call clinic or pharmacist today
    GO_TO_ER = 3     # go to the emergency department
    CALL_911 = 4     # call emergency services now


LEVEL_LABELS: dict[int, str] = {
    1: "Log and monitor",
    2: "Call your clinic or pharmacist today",
    3: "Go to the emergency department",
    4: "Call 911 now",
}


class CheckInSource(str, Enum):
    VOICE = "voice"
    TEXT = "text"
    CAREGIVER = "caregiver"
    SCHEDULED = "scheduled"


class EvidenceKind(str, Enum):
    NEISS = "neiss"        # similar-case outcome rates (injury surveillance)
    FAERS = "faers"        # drug adverse-event signal
    BASELINE = "baseline"  # change vs. this senior's own history
    RULE = "rule"          # deterministic red-flag rule fired
    MODEL = "model"        # leakage-safe outcome model review flag


class EventType(str, Enum):
    CHECKIN_CREATED = "checkin.created"
    EVALUATION_COMPLETED = "evaluation.completed"
    LEVEL_CHANGED = "level.changed"
    MEDS_UPDATED = "meds.updated"
    HANDOFF_READY = "handoff.ready"
    CAREGIVER_MESSAGE = "caregiver.message"
    CIRCLE_CREATED = "circle.created"
    ALERT_ACKNOWLEDGED = "alert.acknowledged"
    ALERT_ESCALATED = "alert.escalated"
    FOLLOWUP_SCHEDULED = "followup.scheduled"
    FOLLOWUP_SENT = "followup.sent"
    TEACHBACK_COMPLETED = "teachback.completed"


# --------------------------------------------------------------------------
# People
# --------------------------------------------------------------------------
class Caregiver(BaseModel):
    id: str
    name: str
    relationship: str = Field(examples=["daughter"])
    phone_e164: str
    preferred_language: str = "en"
    linq_thread_id: Optional[str] = None
    notify_at_level: ActionLevel = ActionLevel.CALL_CLINIC
    # Who we reach first, second, third. Ties break on list order. The backup
    # caregiver only ever hears from us when the one before them went quiet.
    escalation_order: int = 0


class Medication(BaseModel):
    id: str
    name: str                         # as the senior says it
    ingredient: Optional[str] = None  # normalized; drives the FAERS lookup
    dose: Optional[str] = None
    schedule: Optional[str] = None
    source: Literal["profile", "photo", "voice", "clinician"] = "profile"


class Senior(BaseModel):
    id: str
    display_name: str
    date_of_birth: date
    age: int
    preferred_language: str = "en"        # BCP-47-ish: en, es, zh, pt, hi...
    voice_output_supported: bool = True   # Deepgram TTS covers ~7 languages
    conditions: list[str] = []
    allergies: list[str] = []
    medications: list[Medication] = []
    caregivers: list[Caregiver] = []
    consent: dict[str, bool] = {}         # {"share_with:cg_priya": true}


# --------------------------------------------------------------------------
# Check-in
# --------------------------------------------------------------------------
class Symptom(BaseModel):
    label: str                        # "dizziness"
    body_site: Optional[str] = None
    severity: Optional[int] = Field(default=None, ge=0, le=10)
    onset: Optional[str] = None       # free text: "since yesterday morning"
    is_new: Optional[bool] = None


class Vitals(BaseModel):
    systolic: Optional[int] = None
    diastolic: Optional[int] = None
    heart_rate: Optional[int] = None
    temp_c: Optional[float] = None
    spo2: Optional[int] = None
    glucose_mgdl: Optional[int] = None


class CheckInCreate(BaseModel):
    """What the UI (or the voice pipeline) POSTs."""

    senior_id: str
    source: CheckInSource = CheckInSource.TEXT
    language: str = "en"
    text: Optional[str] = None          # typed text OR the Deepgram transcript
    transcript_confidence: Optional[float] = Field(default=None, ge=0, le=1)
    audio_url: Optional[str] = None     # Sprint 1: backend pulls and transcribes
    vitals: Optional[Vitals] = None
    meds_taken_today: Optional[list[str]] = None
    client_ref: Optional[str] = None    # UI-generated id, for optimistic render


class CheckIn(BaseModel):
    id: str
    senior_id: str
    created_at: datetime
    source: CheckInSource
    language: str
    raw_text: Optional[str] = None
    transcript_confidence: Optional[float] = None
    symptoms: list[Symptom] = []
    vitals: Optional[Vitals] = None
    meds_taken_today: list[str] = []
    extraction_model: str = "mock-extractor-0"
    client_ref: Optional[str] = None


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------
class Stat(BaseModel):
    value: float
    unit: Literal["percent", "ratio", "count", "days"] = "percent"
    n: Optional[int] = None
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None


class EvidenceCard(BaseModel):
    kind: EvidenceKind
    title: str
    detail: str
    stat: Optional[Stat] = None
    source: str                       # "NEISS 2019-2023, ages 65+"
    source_url: Optional[str] = None
    weight: float = Field(default=0.0, ge=-1, le=1)  # -1 reassuring .. +1 alarming


class RedFlag(BaseModel):
    code: str                         # "stroke_fast", "chest_pain_cardiac"
    label: str
    matched_on: list[str] = []
    forces_level: ActionLevel


class Evaluation(BaseModel):
    id: str
    checkin_id: str
    senior_id: str
    created_at: datetime
    level: ActionLevel
    level_label: str
    previous_level: Optional[ActionLevel] = None
    red_flags: list[RedFlag] = []
    evidence: list[EvidenceCard] = []
    explanation: str                  # in the senior's language
    explanation_en: str               # always English, for the clinician view
    recommended_actions: list[str] = []
    confidence: float = Field(ge=0, le=1)
    escalated_for_uncertainty: bool = False
    requires_human_review: bool = False
    engine_version: str = "mock-0"
    # Provenance, so the UI can show when a model was involved and the tests
    # can assert the deterministic fallback actually fired.
    llm_used: bool = False
    llm_fallback_reason: Optional[str] = None
    context_citations: list[str] = []
    model_risk: Optional[float] = None
    under_triage: bool = False


class NotificationReceipt(BaseModel):
    channel: Literal["linq", "none"] = "linq"
    to: str                           # caregiver id
    thread_id: Optional[str] = None
    body: str                         # status plus a link only. No PHI.
    status: Literal["sent", "mocked", "failed", "skipped"] = "mocked"
    sent_at: Optional[datetime] = None


class CheckInResponse(BaseModel):
    checkin: CheckIn
    evaluation: Evaluation
    notifications: list[NotificationReceipt] = []


class SeniorQuestion(BaseModel):
    """A grounded question about older-adult health or one seeded senior."""

    question: str = Field(min_length=3, max_length=1000)
    senior_id: Optional[str] = None
    language: str = "en"


class SeniorAnswer(BaseModel):
    answer: str
    citations: list[str] = []
    used_model: bool = False
    fallback_reason: Optional[str] = None


# --------------------------------------------------------------------------
# Timeline and events
# --------------------------------------------------------------------------
class TimelineEntry(BaseModel):
    at: datetime
    type: EventType
    checkin_id: Optional[str] = None
    evaluation_id: Optional[str] = None
    level: Optional[ActionLevel] = None
    summary: str
    detail: Optional[dict[str, Any]] = None


class BaselineSummary(BaseModel):
    """What normal looks like for this senior, over the trailing window."""

    window_days: int = 14
    checkin_count: int = 0
    symptom_frequency: dict[str, int] = {}
    mean_level: float = 1.0
    trending_up: list[str] = []       # symptoms getting worse
    trending_down: list[str] = []


class Timeline(BaseModel):
    senior_id: str
    entries: list[TimelineEntry]
    baseline: BaselineSummary


class WSEvent(BaseModel):
    type: EventType
    at: datetime
    senior_id: str
    payload: dict[str, Any] = {}


# --------------------------------------------------------------------------
# Handoff packet (level >= 3)
# --------------------------------------------------------------------------
class HandoffPacket(BaseModel):
    id: str
    senior_id: str
    created_at: datetime
    language: str
    patient_summary_en: str
    patient_summary_translated: str
    presenting_complaint: str
    level: ActionLevel
    red_flags: list[RedFlag] = []
    medications: list[Medication] = []
    allergies: list[str] = []
    conditions: list[str] = []
    recent_checkins: list[CheckIn] = []
    evidence: list[EvidenceCard] = []
    baseline: BaselineSummary
    caregiver_contact: Optional[Caregiver] = None
    disclaimer: str = (
        "Decision support only. Generated from patient-reported check-ins. "
        "Acuity assignment remains with the triage nurse."
    )


# --------------------------------------------------------------------------
# Care circle -- the Linq group chat the family actually lives in
# --------------------------------------------------------------------------
class CircleMember(BaseModel):
    """One handle in the group chat, and what they are there as."""

    handle: str                       # E.164 phone or an iMessage email
    name: str
    role: Literal["patient", "caregiver", "care_team"] = "caregiver"
    caregiver_id: Optional[str] = None


class CareCircle(BaseModel):
    """The group thread: patient + caregivers + care team, one place.

    `chat_id` is Linq's. It is the only handle we need to send anything, and
    it is what an inbound webhook is matched back to a senior on.
    """

    senior_id: str
    chat_id: Optional[str] = None
    group_name: str
    members: list[CircleMember] = []
    created_at: Optional[datetime] = None
    status: Literal["active", "pending", "failed"] = "pending"
    mocked: bool = False
    error: Optional[str] = None


class CaregiverEnroll(BaseModel):
    """What the sign-up form posts when someone puts their number in."""

    name: str = Field(min_length=1, max_length=120)
    phone_e164: str = Field(min_length=7, max_length=20)
    relationship: str = "family"
    preferred_language: str = "en"
    notify_at_level: ActionLevel = ActionLevel.CALL_CLINIC
    escalation_order: int = 0
    consent: bool = True               # they agreed to be texted about this person


class CircleEnrollRequest(BaseModel):
    senior_id: str
    caregivers: list[CaregiverEnroll] = Field(min_length=1)
    include_patient: bool = True       # the patient is in their own group chat
    patient_phone_e164: Optional[str] = None


# --------------------------------------------------------------------------
# Escalation -- an alert nobody answered
# --------------------------------------------------------------------------
class AlertAck(BaseModel):
    caregiver_id: str
    at: datetime
    via: Literal["tapback", "text", "dashboard"] = "tapback"
    detail: Optional[str] = None


class Alert(BaseModel):
    """One outbound alert and its acknowledgement state.

    The escalation clock starts when this is created. If no caregiver has
    acknowledged by `escalate_after`, the next one in `pending_order` is texted
    directly and the clock restarts.
    """

    id: str
    senior_id: str
    evaluation_id: Optional[str] = None
    level: ActionLevel
    created_at: datetime
    body: str
    chat_id: Optional[str] = None
    message_id: Optional[str] = None
    notified: list[str] = []          # caregiver ids already reached
    pending_order: list[str] = []     # caregiver ids still to try, in order
    acks: list[AlertAck] = []
    escalate_after: Optional[datetime] = None
    escalations: int = 0
    resolved: bool = False

    @property
    def acknowledged(self) -> bool:
        return bool(self.acks)


# --------------------------------------------------------------------------
# Post-discharge follow-up and teach-back
# --------------------------------------------------------------------------
class FollowUpJob(BaseModel):
    """A scheduled check-in after an ED visit. 24h, then 72h."""

    id: str
    senior_id: str
    kind: Literal["checkin", "teachback"] = "checkin"
    due_at: datetime
    hours_after: float
    source_evaluation_id: Optional[str] = None
    status: Literal["scheduled", "sent", "answered", "failed", "cancelled"] = "scheduled"
    sent_at: Optional[datetime] = None
    answered_at: Optional[datetime] = None
    result_level: Optional[ActionLevel] = None
    worsened: bool = False
    note: Optional[str] = None


class TeachBackRequest(BaseModel):
    """The senior saying their discharge instructions back, in their own words.

    `spoken` is the Deepgram transcript. We never grade the person -- we grade
    whether the instruction survived the handoff.
    """

    senior_id: str
    spoken: str = Field(min_length=1, max_length=4000)
    language: str = "en"
    instructions: Optional[list[str]] = None   # defaults to the stored care plan
    followup_id: Optional[str] = None


class TeachBackItem(BaseModel):
    instruction: str
    covered: bool
    matched_terms: list[str] = []
    score: float = Field(default=0.0, ge=0, le=1)


class TeachBackResult(BaseModel):
    senior_id: str
    at: datetime
    language: str
    spoken: str
    items: list[TeachBackItem] = []
    score: float = Field(default=0.0, ge=0, le=1)
    passed: bool = False
    missed: list[str] = []
    caregiver_alerted: bool = False
    prompt: str = ""                  # what we asked, in their language


class CarePlan(BaseModel):
    """Discharge instructions, in the words the senior was given them in."""

    senior_id: str
    instructions: list[str] = []
    routines: list[str] = []
    updated_at: Optional[datetime] = None


# --------------------------------------------------------------------------
# Linq webhook (backend-only surface)
# --------------------------------------------------------------------------
class LinqInbound(BaseModel):
    """Our own flattened shape. Sprint 0 posted this directly; the real Linq
    webhook is normalized into it by `linq_events.normalize`."""

    thread_id: str
    from_phone_e164: str
    text: Optional[str] = None
    media_urls: list[str] = []
    received_at: Optional[datetime] = None
    # Set when the inbound was a tapback rather than a message. `reaction` is
    # Linq's type string ("like", "love", an emoji); `reacted_to` is the
    # message it landed on, which is how an ack is tied back to an alert.
    reaction: Optional[str] = None
    reacted_to: Optional[str] = None
    event: Optional[str] = None       # "message.received", "reaction.added", ...


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    contract_version: str = CONTRACT_VERSION
    mock_mode: bool = True
    seeded_seniors: int = 0
