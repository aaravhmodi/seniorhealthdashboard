"""Plain-language explanation, in the senior's language.

Sprint 0 uses templates so the demo runs offline and says exactly the same
thing every time. Sprint 1 replaces render_explanation() with an LLM call that
takes the SAME inputs (level, flags, evidence) -- the model phrases the reason,
it never chooses the level.

Reading level target: short sentences, no clinical jargon, no diagnosis.
"""
from __future__ import annotations

from .schemas import ActionLevel, EvidenceCard, RedFlag

# language -> level -> sentence
TEMPLATES: dict[str, dict[int, str]] = {
    "en": {
        1: "Thank you for checking in. Nothing here needs a call today. Keep an eye on it and tell me if it changes.",
        2: "Some of what you told me is worth a call to your clinic or pharmacist today. This is not an emergency.",
        3: "What you described should be looked at in a hospital today. Please go to the emergency department now.",
        4: "This needs help right away. Please call 911 now.",
    },
    "es": {
        1: "Gracias por avisarme. Hoy no hace falta llamar a nadie. Seguimos vigilando y me avisa si cambia.",
        2: "Algo de lo que me conto merece una llamada a su clinica o farmacia hoy. No es una emergencia.",
        3: "Lo que me describio debe revisarse hoy en un hospital. Por favor vaya a urgencias ahora.",
        4: "Esto necesita ayuda inmediata. Por favor llame al 911 ahora.",
    },
    "zh": {
        1: "谢谢您的反馈。今天不需要打电话就医。我们继续观察，有变化请告诉我。",
        2: "您说的情况今天值得给诊所或药剂师打个电话。这不是紧急情况。",
        3: "您描述的情况今天需要到医院检查。请现在去急诊。",
        4: "这需要立即处理。请马上拨打 911。",
    },
    "pt": {
        1: "Obrigado por avisar. Hoje nao e preciso ligar para ninguem. Vamos acompanhar e me avise se mudar.",
        2: "Parte do que voce contou merece uma ligacao para a clinica ou farmacia hoje. Nao e uma emergencia.",
        3: "O que voce descreveu precisa ser avaliado hoje no hospital. Por favor va ao pronto-socorro agora.",
        4: "Isso precisa de ajuda imediata. Por favor ligue para o 911 agora.",
    },
    "hi": {
        1: "बताने के लिए धन्यवाद। आज किसी को फ़ोन करने की ज़रूरत नहीं है। हम ध्यान रखेंगे, बदलाव हो तो बताइए।",
        2: "आपने जो बताया उसमें से कुछ के लिए आज क्लिनिक या फार्मासिस्ट को फ़ोन करना ठीक रहेगा। यह आपात स्थिति नहीं है।",
        3: "आपने जो बताया उसे आज अस्पताल में दिखाना चाहिए। कृपया अभी इमरजेंसी में जाइए।",
        4: "इसमें तुरंत मदद चाहिए। कृपया अभी 911 पर कॉल कीजिए।",
    },
}

REASON_LEAD: dict[str, str] = {
    "en": "Why: ",
    "es": "Por que: ",
    "zh": "原因：",
    "pt": "Por que: ",
    "hi": "कारण: ",
}

UNSURE_NOTE: dict[str, str] = {
    "en": "I did not hear enough to be sure, so I am being careful.",
    "es": "No escuche lo suficiente para estar segura, asi que voy con cuidado.",
    "zh": "我了解的信息还不够，所以采取谨慎的建议。",
    "pt": "Nao ouvi o suficiente para ter certeza, entao estou sendo cuidadosa.",
    "hi": "पक्का कहने के लिए पूरी जानकारी नहीं मिली, इसलिए सावधानी बरत रही हूँ।",
}


def _reason(flags: list[RedFlag], evidence: list[EvidenceCard], symptoms: list[str]) -> str:
    if flags:
        return flags[0].label
    if evidence:
        return evidence[0].title
    if symptoms:
        return "you reported " + ", ".join(symptoms)
    return "no specific symptoms were reported"


def render_explanation(
    level: ActionLevel,
    language: str,
    symptoms: list[str],
    flags: list[RedFlag],
    evidence: list[EvidenceCard],
    escalated: bool = False,
) -> tuple[str, str]:
    """Returns (explanation in the senior's language, English explanation)."""
    lang = language if language in TEMPLATES else "en"
    reason = _reason(flags, evidence, symptoms)

    def build(code: str) -> str:
        parts = [TEMPLATES[code][int(level)], f"{REASON_LEAD[code]}{reason}."]
        if escalated:
            parts.append(UNSURE_NOTE[code])
        return " ".join(parts)

    # The reason clause stays in English for now; the LLM translates it in
    # Sprint 1. The clinician-facing string is always English by contract.
    return build(lang), build("en")
