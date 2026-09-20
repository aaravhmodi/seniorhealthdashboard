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
from .risk import assess as assess_risk
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

# The action rung is deterministic, but its patient-facing name and next steps
# must follow the language selected in the dashboard.  English remains the
# fallback for a language we do not yet support.
LOCALIZED_LEVEL_LABELS: dict[str, dict[ActionLevel, str]] = {
    "en": {ActionLevel.LOG: "Log and monitor", ActionLevel.CALL_CLINIC: "Call clinic today", ActionLevel.GO_TO_ER: "Go to emergency care", ActionLevel.CALL_911: "Call 911 now"},
    "es": {ActionLevel.LOG: "Registrar y vigilar", ActionLevel.CALL_CLINIC: "Llame hoy a la clínica", ActionLevel.GO_TO_ER: "Vaya a urgencias", ActionLevel.CALL_911: "Llame al 911 ahora"},
    "pt": {ActionLevel.LOG: "Registrar e acompanhar", ActionLevel.CALL_CLINIC: "Ligue hoje para a clínica", ActionLevel.GO_TO_ER: "Vá ao pronto-socorro", ActionLevel.CALL_911: "Ligue para 911 agora"},
    "zh": {ActionLevel.LOG: "记录并观察", ActionLevel.CALL_CLINIC: "今天联系诊所", ActionLevel.GO_TO_ER: "去急诊", ActionLevel.CALL_911: "立即拨打 911"},
    "hi": {ActionLevel.LOG: "लिखें और निगरानी रखें", ActionLevel.CALL_CLINIC: "आज क्लिनिक को फ़ोन करें", ActionLevel.GO_TO_ER: "आपात विभाग जाएँ", ActionLevel.CALL_911: "अभी 911 पर फ़ोन करें"},
}

LOCALIZED_ACTIONS: dict[str, dict[ActionLevel, list[str]]] = {
    "hi": {
        ActionLevel.LOG: ["अपनी रोज़ की दिनचर्या रखें और कल फिर चेक-इन करें।", "कुछ भी बिगड़े तो हमें तुरंत बताएं।"],
        ActionLevel.CALL_CLINIC: ["आज अपने क्लिनिक या फार्मासिस्ट को फ़ोन करें।", "फ़ोन करते समय अपनी दवाओं की सूची तैयार रखें।", "आपके देखभालकर्ता को सूचना दे दी गई है।"],
        ActionLevel.GO_TO_ER: ["अभी आपात विभाग जाएँ।", "दवाओं की सूची साथ रखें — स्टाफ के लिए एक सारांश तैयार है।", "खुद गाड़ी न चलाएँ।"],
        ActionLevel.CALL_911: ["अभी 911 पर फ़ोन करें।", "जहाँ हैं वहीं रहें और हो सके तो दरवाज़ा खोल दें।", "आपके देखभालकर्ता से संपर्क किया जा रहा है।"],
    },
    "es": {
        ActionLevel.LOG: ["Siga su rutina habitual y registre cómo se siente mañana.", "Avísenos de inmediato si algo empeora."],
        ActionLevel.CALL_CLINIC: ["Llame hoy a su clínica o farmacia.", "Tenga lista su lista de medicamentos cuando llame.", "Se notificó a su cuidador."],
        ActionLevel.GO_TO_ER: ["Vaya ahora al servicio de urgencias.", "Lleve su lista de medicamentos; preparamos un resumen para el personal.", "No conduzca usted."],
        ActionLevel.CALL_911: ["Llame al 911 ahora.", "Quédese donde está y, si puede, abra la puerta.", "Estamos contactando a su cuidador."],
    },
    "pt": {
        ActionLevel.LOG: ["Mantenha sua rotina e faça outro check-in amanhã.", "Avise-nos imediatamente se algo piorar."],
        ActionLevel.CALL_CLINIC: ["Ligue hoje para sua clínica ou farmácia.", "Tenha sua lista de medicamentos pronta ao ligar.", "Seu cuidador foi avisado."],
        ActionLevel.GO_TO_ER: ["Vá agora ao pronto-socorro.", "Leve sua lista de medicamentos; preparamos um resumo para a equipe.", "Não dirija."],
        ActionLevel.CALL_911: ["Ligue para 911 agora.", "Fique onde está e, se puder, destranque a porta.", "Estamos entrando em contato com seu cuidador."],
    },
    "zh": {
        ActionLevel.LOG: ["保持日常作息，明天再次记录。", "如果任何情况变差，请马上告诉我们。"],
        ActionLevel.CALL_CLINIC: ["今天联系您的诊所或药剂师。", "打电话时准备好您的用药清单。", "已通知您的照护者。"],
        ActionLevel.GO_TO_ER: ["现在去急诊。", "带上用药清单；我们已为工作人员准备摘要。", "请不要自己开车。"],
        ActionLevel.CALL_911: ["立即拨打 911。", "留在原地，如果可以请打开门。", "正在联系您的照护者。"],
    },
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
    output_language = language or senior.preferred_language
    output_language = output_language if output_language in LOCALIZED_LEVEL_LABELS else "en"
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

    # What this could be, with a number on each and the action that number
    # earns. Runs after the rung is settled: it explains and ranks, and the
    # action it shows can only ever be the more urgent of the two.
    risk = assess_risk(
        checkin,
        senior,
        baseline,
        flags,
        level,
        language=language or senior.preferred_language,
    )
    if risk.concerns:
        wanted = max(int(c.action_level) for c in risk.concerns)
        if wanted > int(level):
            level = ActionLevel(wanted)

    explanation, explanation_en = render_explanation(
        level=level,
        language=output_language,
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
        level_label=LOCALIZED_LEVEL_LABELS[output_language][level],
        previous_level=previous_level,
        red_flags=flags,
        evidence=evidence,
        explanation=explanation,
        explanation_en=explanation_en,
        recommended_actions=LOCALIZED_ACTIONS.get(output_language, RECOMMENDED_ACTIONS)[level],
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
        risk=risk,
    )
