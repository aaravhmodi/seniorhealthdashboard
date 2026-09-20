"""The pre-arrival packet the ED clinician sees before the patient arrives.

This is the payoff of every check-in that came before: the clinician opens a
structured summary in English while the patient is still in the car, including
what has been drifting for two weeks -- which the patient will not remember to
mention at triage.
"""
from __future__ import annotations

from .schemas import (
    ActionLevel,
    BaselineSummary,
    CheckIn,
    Evaluation,
    HandoffPacket,
    Senior,
)
from .store import new_id, now


def _presenting(checkin: CheckIn) -> str:
    if not checkin.symptoms:
        return "Unspecified concern reported at check-in"
    parts = []
    for s in checkin.symptoms[:3]:
        bit = s.label
        if s.severity is not None:
            bit += f" ({s.severity}/10)"
        if s.onset:
            bit += f", {s.onset}"
        parts.append(bit)
    return "; ".join(parts)


def _summary_en(
    senior: Senior, checkin: CheckIn, evaluation: Evaluation, baseline: BaselineSummary
) -> str:
    lines = [
        f"{senior.age}-year-old, preferred language {senior.preferred_language}. "
        f"Self-reported check-in routed at level {int(evaluation.level)} "
        f"({evaluation.level_label}).",
        f"Presenting: {_presenting(checkin)}.",
    ]
    if evaluation.red_flags:
        lines.append(
            "Red flags: "
            + "; ".join(f"{f.label} [{', '.join(f.matched_on[:3])}]"
                        for f in evaluation.red_flags)
            + "."
        )
    if senior.conditions:
        lines.append("History: " + ", ".join(senior.conditions) + ".")
    if senior.medications:
        lines.append(
            "Medications: "
            + ", ".join(
                f"{m.name}{' ' + m.dose if m.dose else ''}" for m in senior.medications
            )
            + "."
        )
    lines.append("Allergies: " + (", ".join(senior.allergies) or "none recorded") + ".")
    if baseline.trending_up:
        lines.append(
            f"Trend over {baseline.window_days} days ({baseline.checkin_count} "
            f"check-ins): increasing " + ", ".join(baseline.trending_up) + "."
        )
    if checkin.vitals:
        v = checkin.vitals.model_dump(exclude_none=True)
        if v:
            lines.append(
                "Patient-reported vitals: "
                + ", ".join(f"{k} {val}" for k, val in v.items())
                + "."
            )
    if checkin.transcript_confidence is not None:
        lines.append(
            f"Intake was by voice, transcript confidence "
            f"{checkin.transcript_confidence:.2f}."
        )
    return " ".join(lines)


def build_packet(
    senior: Senior,
    checkin: CheckIn,
    evaluation: Evaluation,
    baseline: BaselineSummary,
    recent: list[CheckIn],
) -> HandoffPacket:
    summary = _summary_en(senior, checkin, evaluation, baseline)
    caregiver = senior.caregivers[0] if senior.caregivers else None
    return HandoffPacket(
        id=new_id("handoff"),
        senior_id=senior.id,
        created_at=now(),
        language=senior.preferred_language,
        patient_summary_en=summary,
        # Sprint 1 runs this through the translation call; the clinician always
        # reads the English field, so a translation failure is never blocking.
        patient_summary_translated=summary,
        presenting_complaint=_presenting(checkin),
        level=evaluation.level,
        red_flags=evaluation.red_flags,
        medications=senior.medications,
        allergies=senior.allergies,
        conditions=senior.conditions,
        recent_checkins=recent[:5],
        evidence=evaluation.evidence,
        baseline=baseline,
        risk=evaluation.risk,
        caregiver_contact=caregiver,
    )


def should_build(evaluation: Evaluation) -> bool:
    return int(evaluation.level) >= int(ActionLevel.GO_TO_ER)
