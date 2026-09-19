"""The action ladder: check-in + senior -> Evaluation.

Order of operations, and it matters:
  1. Deterministic rules set a FLOOR. Nothing below can lower it.
  2. Evidence (NEISS / FAERS / baseline) can only push the level UP.
  3. When confidence is low, escalate one rung rather than guess low.

The LLM is not in this path. In Sprint 1 it writes the explanation text and
extracts the symptoms; it never picks the level.
"""
from __future__ import annotations

from .evidence import baseline_cards, faers_cards, neiss_cards
from .datasets import model as outcome_model
from .explain import render_explanation
from .schemas import (
    ActionLevel,
    BaselineSummary,
    CheckIn,
    EvidenceCard,
    Evaluation,
    LEVEL_LABELS,
    Senior,
)
from .rules import evaluate_rules, rule_floor
from .store import new_id, now

ENGINE_VERSION = "ladder-0.1-rules+mockevidence"

# How much summed evidence weight it takes to earn each rung on its own.
EVIDENCE_THRESHOLDS: list[tuple[float, ActionLevel]] = [
    (2.2, ActionLevel.GO_TO_ER),
    (0.9, ActionLevel.CALL_CLINIC),
]

RECOMMENDED_ACTIONS: dict[ActionLevel, list[str]] = {
    ActionLevel.LOG: [
        "Keep your usual routine and check in again tomorrow.",
        "Tell us right away if anything gets worse.",
    ],
    ActionLevel.CALL_CLINIC: [
        "Call your clinic or pharmacist today.",
        "Have your medicine list ready when you call.",
        "Your caregiver has been notified.",
    ],
    ActionLevel.GO_TO_ER: [
        "Go to the emergency department now.",
        "Bring your medicine list -- we have prepared a summary for the staff.",
        "Do not drive yourself.",
    ],
    ActionLevel.CALL_911: [
        "Call 911 now.",
        "Stay where you are and unlock the door if you can.",
        "Your caregiver is being contacted.",
    ],
}


def _confidence(checkin: CheckIn, flags, evidence: list[EvidenceCard]) -> float:
    """Low when we heard little, or heard it badly."""
    # A clear "nothing wrong today" is a confident answer, so the base is high
    # and the penalties are for not hearing well, not for hearing little.
    score = 0.6
    if checkin.symptoms:
        score += 0.15
    if checkin.vitals is not None:
        score += 0.1
    if evidence:
        score += 0.05
    if flags:
        score += 0.05
    if checkin.raw_text and len(checkin.raw_text.strip()) < 15:
        score -= 0.15
    if not checkin.raw_text:
        score -= 0.25
    if checkin.transcript_confidence is not None:
        score = score * (0.6 + 0.4 * checkin.transcript_confidence)
    return round(max(0.05, min(0.99, score)), 2)


def evaluate(
    checkin: CheckIn,
    senior: Senior,
    baseline: BaselineSummary,
    previous_level: ActionLevel | None = None,
    language: str | None = None,
) -> Evaluation:
    flags = evaluate_rules(checkin, senior)
    floor = rule_floor(flags)

    evidence = (
        neiss_cards(checkin, senior)
        + faers_cards(checkin, senior)
        + baseline_cards(baseline, checkin)
    )
    total_weight = sum(c.weight for c in evidence)

    level = floor
    for threshold, candidate in EVIDENCE_THRESHOLDS:
        if total_weight >= threshold and int(candidate) > int(level):
            level = candidate
            break

    # Something was reported, so "log it" is the minimum -- never return nothing.
    if checkin.symptoms and int(level) < int(ActionLevel.LOG):
        level = ActionLevel.LOG

    confidence = _confidence(checkin, flags, evidence)
    model_risk = outcome_model.predict(checkin, senior)
    under_triage = False
    model_result = outcome_model.card(checkin, senior, level)
    if model_result:
        model_card, model_risk = model_result
        evidence.append(model_card)
        under_triage = True
    escalated = False
    if confidence < 0.5 and int(level) < int(ActionLevel.GO_TO_ER):
        level = ActionLevel(int(level) + 1)
        escalated = True

    explanation, explanation_en = render_explanation(
        level=level,
        language=language or senior.preferred_language,
        symptoms=[s.label for s in checkin.symptoms],
        flags=flags,
        evidence=evidence,
        escalated=escalated,
    )

    return Evaluation(
        id=new_id("eval"),
        checkin_id=checkin.id,
        senior_id=senior.id,
        created_at=now(),
        level=level,
        level_label=LEVEL_LABELS[int(level)],
        previous_level=previous_level,
        red_flags=flags,
        evidence=evidence,
        explanation=explanation,
        explanation_en=explanation_en,
        recommended_actions=RECOMMENDED_ACTIONS[level],
        confidence=confidence,
        escalated_for_uncertainty=escalated,
        # A clinician confirms anything that sends someone to hospital, and
        # anything we escalated because we were unsure.
        requires_human_review=(
            int(level) >= int(ActionLevel.GO_TO_ER) or escalated or under_triage
        ),
        engine_version=ENGINE_VERSION,
        model_risk=model_risk,
        under_triage=under_triage,
    )
