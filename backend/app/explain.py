"""Plain-language explanation, in the senior's language.

Deterministic templates by default. When an OpenAI key is configured the LLM
rephrases these -- it never changes the level, and anything it returns must
pass `persona.lint()` or we fall back to the template. See llm.py.

Everything here obeys the style contract in persona.py: action first, reason
second, short sentences, no diagnosis, no cheerleading, no elderspeak.
"""
from __future__ import annotations

from .persona import TEACH_BACK
from .schemas import ActionLevel, EvidenceCard, RedFlag

# language -> level -> the action sentence, then the reason lead-in
TEMPLATES: dict[str, dict[int, str]] = {
    "en": {
        1: "Nothing here needs a call today. Keep to your usual routine.",
        2: "Please call your clinic or pharmacist today. This is not an emergency.",
        3: "Please go to the emergency department now. Do not drive yourself.",
        4: "Please call nine one one now. Stay where you are.",
    },
    "es": {
        1: "Hoy no hace falta llamar a nadie. Siga con su rutina de siempre.",
        2: "Llame hoy a su clinica o a su farmacia. No es una emergencia.",
        3: "Vaya ahora a la sala de urgencias. No maneje usted.",
        4: "Llame ahora al nueve uno uno. Quedese donde esta.",
    },
    "fr": {
        1: "Rien ici ne demande un appel aujourd'hui. Gardez vos habitudes.",
        2: "Appelez votre clinique ou votre pharmacien aujourd'hui. Ce n'est pas une urgence.",
        3: "Allez tout de suite aux urgences. Ne conduisez pas vous-meme.",
        4: "Appelez le neuf un un maintenant. Restez ou vous etes.",
    },
    "zh": {
        1: "今天不需要打电话。按平常的习惯就好。",
        2: "请今天给诊所或药剂师打个电话。这不是紧急情况。",
        3: "请现在就去急诊。不要自己开车。",
        4: "请现在拨打九一一。留在原地。",
    },
    "pt": {
        1: "Nada aqui precisa de uma ligacao hoje. Siga sua rotina normal.",
        2: "Ligue hoje para sua clinica ou farmacia. Nao e uma emergencia.",
        3: "Va agora ao pronto-socorro. Nao dirija voce mesmo.",
        4: "Ligue agora para nove um um. Fique onde voce esta.",
    },
    "hi": {
        1: "आज किसी को फ़ोन करने की ज़रूरत नहीं है। अपनी रोज़ की दिनचर्या रखिए।",
        2: "आज अपने क्लिनिक या फार्मासिस्ट को फ़ोन कीजिए। यह आपात स्थिति नहीं है।",
        3: "अभी इमरजेंसी विभाग जाइए। खुद गाड़ी मत चलाइए।",
        4: "अभी नौ एक एक पर फ़ोन कीजिए। जहाँ हैं वहीं रहिए।",
    },
}

REASON_LEAD: dict[str, str] = {
    "en": "This is because", "es": "Esto es porque", "fr": "C'est parce que",
    "zh": "原因是", "pt": "Isso e porque", "hi": "इसका कारण:",
}

# Red-flag code -> a reason a person can actually follow, per language.
# Never a diagnosis: what they told us, not what we think it is.
REASON_BY_CODE: dict[str, dict[str, str]] = {
    "stroke_fast": {
        "en": "the weakness on one side needs checking right away",
        "es": "la debilidad de un lado necesita revisarse de inmediato",
        "fr": "la faiblesse d'un cote doit etre vue tout de suite",
        "zh": "一侧无力需要马上检查",
        "pt": "a fraqueza de um lado precisa ser vista agora",
        "hi": "एक तरफ़ की कमजोरी तुरंत देखनी होगी",
    },
    "cardiac_acs": {
        "en": "trouble breathing with sickness or sweating can come from the heart",
        "es": "la falta de aire con nausea o sudor puede venir del corazon",
        "fr": "le souffle court avec nausee ou sueur peut venir du coeur",
        "zh": "呼吸困难加上恶心或出汗可能和心脏有关",
        "pt": "falta de ar com enjoo ou suor pode vir do coracao",
        "hi": "साँस फूलना और जी मिचलाना दिल से जुड़ा हो सकता है",
    },
    "altered_mental_status": {
        "en": "sudden confusion in a day or less needs a check today",
        "es": "la confusion de un dia para otro necesita revisarse hoy",
        "fr": "une confusion apparue en un jour doit etre vue aujourd'hui",
        "zh": "一两天内突然糊涂需要今天检查",
        "pt": "confusao que apareceu em um dia precisa ser vista hoje",
        "hi": "अचानक आई उलझन आज ही देखनी चाहिए",
    },
    "fall_head_injury": {
        "en": "a fall while you take a blood thinner needs a look today",
        "es": "una caida tomando anticoagulante necesita revisarse hoy",
        "fr": "une chute sous anticoagulant doit etre vue aujourd'hui",
        "zh": "服用抗凝药时摔倒需要今天检查",
        "pt": "uma queda tomando anticoagulante precisa ser vista hoje",
        "hi": "खून पतला करने की दवा के साथ गिरना आज देखना चाहिए",
    },
    "fall_reported": {
        "en": "a fall is worth telling your clinic about",
        "es": "vale la pena contarle la caida a su clinica",
        "fr": "une chute merite d'etre signalee a votre clinique",
        "zh": "摔倒的事值得告诉诊所",
        "pt": "vale a pena contar a queda para sua clinica",
        "hi": "गिरने की बात क्लिनिक को बतानी चाहिए",
    },
    "breathing_distress": {
        "en": "you are having trouble getting your breath",
        "es": "le cuesta trabajo respirar",
        "fr": "vous avez du mal a respirer",
        "zh": "您呼吸有困难",
        "pt": "voce esta com dificuldade para respirar",
        "hi": "आपको साँस लेने में तकलीफ़ है",
    },
    "severe_bleeding": {
        "en": "bleeding like this needs help right away",
        "es": "un sangrado asi necesita ayuda de inmediato",
        "fr": "un saignement comme celui-ci demande de l'aide tout de suite",
        "zh": "这样的出血需要马上处理",
        "pt": "um sangramento assim precisa de ajuda agora",
        "hi": "ऐसा खून बहना तुरंत देखना होगा",
    },
    "worst_headache": {
        "en": "a headache this sudden and this bad needs help now",
        "es": "un dolor de cabeza tan repentino y fuerte necesita ayuda ahora",
        "fr": "un mal de tete si soudain et si fort demande de l'aide maintenant",
        "zh": "这么突然又这么重的头痛需要马上处理",
        "pt": "uma dor de cabeca tao forte e repentina precisa de ajuda agora",
        "hi": "इतना अचानक और तेज़ सिरदर्द तुरंत देखना होगा",
    },
    "sepsis_screen": {
        "en": "two of your readings are outside your usual range",
        "es": "dos de sus medidas estan fuera de lo normal para usted",
        "fr": "deux de vos mesures sortent de votre normale",
        "zh": "您有两项数值超出平常范围",
        "pt": "duas das suas medidas estao fora do normal",
        "hi": "आपकी दो रीडिंग सामान्य से बाहर हैं",
    },
    "vitals_out_of_range": {
        "en": "one of your readings is outside the safe range",
        "es": "una de sus medidas esta fuera del rango seguro",
        "fr": "une de vos mesures sort de la zone sure",
        "zh": "您有一项数值超出安全范围",
        "pt": "uma das suas medidas esta fora da faixa segura",
        "hi": "आपकी एक रीडिंग सुरक्षित सीमा से बाहर है",
    },
}

# When evidence, not a rule, drove the level.
REASON_TREND: dict[str, str] = {
    "en": "what you have been telling me is changing",
    "es": "lo que me ha contado esta cambiando",
    "fr": "ce que vous me dites est en train de changer",
    "zh": "您最近告诉我的情况在变化",
    "pt": "o que voce vem me contando esta mudando",
    "hi": "आप जो बता रहे हैं वह बदल रहा है",
}

REASON_GENERIC: dict[str, str] = {
    "en": "of what you told me today",
    "es": "de lo que me conto hoy",
    "fr": "de ce que vous m'avez dit aujourd'hui",
    "zh": "您今天告诉我的情况",
    "pt": "do que voce me contou hoje",
    "hi": "आज आपने जो बताया",
}

REPORTED_ACK: dict[str, str] = {
    "en": "I heard you mention",
    "es": "Le escuche mencionar",
    "fr": "J'ai entendu que vous avez mentionne",
    "zh": "我听到您提到",
    "pt": "Ouvi voce mencionar",
    "hi": "मैंने आपको बताते हुए सुना",
}

UNSURE_NOTE: dict[str, str] = {
    "en": "I did not hear enough to be sure, so I am being careful.",
    "es": "No escuche lo suficiente para estar segura. Prefiero ir con cuidado.",
    "fr": "Je n'ai pas assez entendu pour etre sure. Je prefere etre prudente.",
    "zh": "我了解得还不够，所以我选择谨慎一些。",
    "pt": "Nao ouvi o suficiente para ter certeza. Prefiro ser cuidadosa.",
    "hi": "पक्का कहने लायक जानकारी नहीं मिली। इसलिए सावधानी रख रही हूँ।",
}


def _reason(
    language: str, flags: list[RedFlag], evidence: list[EvidenceCard]
) -> str:
    lang = language if language in REASON_GENERIC else "en"
    if flags:
        by_code = REASON_BY_CODE.get(flags[0].code)
        if by_code:
            return by_code.get(lang, by_code["en"])
    if any(c.kind == "baseline" for c in evidence):
        return REASON_TREND[lang]
    return REASON_GENERIC[lang]


def render_explanation(
    level: ActionLevel,
    language: str,
    symptoms: list[str],
    flags: list[RedFlag],
    evidence: list[EvidenceCard],
    escalated: bool = False,
    teach_back: bool = True,
) -> tuple[str, str]:
    """Returns (explanation in the senior's language, English explanation).

    The English string is what the clinician reads, so it is always produced
    even when the patient-facing one is in another language.
    """
    lang = language if language in TEMPLATES else "en"

    def build(code: str) -> str:
        parts = [TEMPLATES[code][int(level)]]
        if symptoms and int(level) == int(ActionLevel.LOG):
            ack = REPORTED_ACK.get(code, REPORTED_ACK["en"])
            parts.insert(0, f"{ack}: {', '.join(symptoms)}.")
        parts.append(f"{REASON_LEAD[code]} {_reason(code, flags, evidence)}.")
        if escalated:
            parts.append(UNSURE_NOTE[code])
        elif teach_back and int(level) >= int(ActionLevel.CALL_CLINIC):
            parts.append(TEACH_BACK[code])
        return " ".join(parts)

    return build(lang), build("en")
