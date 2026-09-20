"""Turn free text into a structured check-in.

Sprint 0 is a deterministic lexicon matcher, not an LLM. That keeps the mock
offline, fast and repeatable for the demo, and it doubles as the regression
fixture once the real extractor lands in Sprint 1 (same in, same shape out).

Sprint 1 swaps extract() for an LLM call behind the same signature.
"""
from __future__ import annotations

import re
import unicodedata

from .schemas import Symptom

# label -> trigger phrases, across the demo languages.
# Deliberately small: these are the symptoms the demo script exercises.
LEXICON: dict[str, list[str]] = {
    "chest pain": ["chest pain", "chest pressure", "tight chest", "dolor de pecho",
                   "presion en el pecho", "胸痛", "胸口痛", "dor no peito",
                   "douleur a la poitrine", "mal a la poitrine", "serrement poitrine"],
    "shortness of breath": ["short of breath", "shortness of breath", "can't breathe",
                            "cant breathe", "trouble breathing", "falta de aire",
                            "falta el aire", "me falta aire", "no puedo respirar",
                            "呼吸困难", "喘不过气", "falta de ar",
                            "essouffle", "du mal a respirer", "manque d'air",
                            "je ne peux pas respirer"],
    "dizziness": ["dizzy", "dizziness", "lightheaded", "light headed", "mareo",
                  "mareado", "mareada", "头晕", "tontura", "chakkar",
                  "vertige", "etourdi", "la tete qui tourne"],
    "confusion": ["confused", "confusion", "not making sense", "disoriented",
                  "forgetful today", "confundido", "confusion mental", "意识模糊",
                  "糊涂", "confuso", "confus", "perdu dans ses idees",
                  "il ne sait plus"],
    "weakness one side": ["weak on one side", "one side", "arm is weak",
                          "face droop", "drooping", "slurred", "no puedo mover",
                          "un lado", "一侧无力", "嘴歪", "un cote", "bras faible",
                          "bouche qui tombe", "parle mal"],
    "fall": ["fell", "i fell", "had a fall", "slipped", "me cai", "se cayo",
             "摔倒", "跌倒", "cai", "je suis tombe", "tombee", "une chute"],
    "fever": ["fever", "feverish", "hot and cold", "fiebre", "发烧", "febre", "fievre"],
    "nausea": ["nausea", "nauseous", "sick to my stomach", "vomit", "nausea",
               "vomito", "恶心", "呕吐", "enjoo", "nausee", "mal au coeur", "vomi"],
    "poor appetite": ["not eating", "no appetite", "poor appetite", "skipped meals",
                      "sin apetito", "no tengo hambre", "没胃口", "sem apetite",
                      "pas faim", "pas d'appetit", "je ne mange plus"],
    "swelling legs": ["swollen", "swelling", "ankles are big", "hinchazon",
                      "piernas hinchadas", "肿", "inchaco", "jambes enflees", "gonfle"],
    "urinary symptoms": ["burning when i pee", "burning urine", "peeing a lot",
                         "urine smells", "ardor al orinar", "尿痛", "ardor ao urinar",
                         "ca brule quand", "brulure en urinant"],
    "trouble sleeping": ["can't sleep", "cant sleep", "not sleeping", "no puedo dormir",
                         "失眠", "nao consigo dormir", "je ne dors pas", "pas dormi"],
    "back pain": ["back pain", "my back hurts", "dolor de espalda", "背痛",
                  "dor nas costas", "mal au dos", "douleur au dos"],
    "headache": ["headache", "head hurts", "worst headache", "dolor de cabeza",
                 "头痛", "dor de cabeca", "mal a la tete", "migraine"],
    "bleeding": ["bleeding", "blood in", "coughing blood", "sangrado", "sangre",
                 "出血", "sangramento", "saignement", "du sang"],
}

# Native-script phrases are kept as Unicode escapes so Windows and Linux
# checkouts cannot silently reinterpret the source encoding. These supplement
# the Latin-script demo lexicon above.
LEXICON["chest pain"].extend(["\u80f8\u75db", "\u80f8\u53e3\u75db"])
LEXICON["shortness of breath"].extend(["\u547c\u5438\u56f0\u96be", "\u5598\u4e0d\u8fc7\u6c14"])
LEXICON["nausea"].extend(["\u6076\u5fc3", "\u5455\u5410", "\u092e\u0924\u0932\u0940", "\u091c\u0940\u0915\u093e\u092e\u0932\u093e\u0928\u093e"])
LEXICON["shortness of breath"].extend(["\u0938\u093e\u0902\u0938 \u0932\u0947\u0928\u0947 \u092e\u0947\u0902 \u0924\u0915\u0932\u0940\u092b", "\u0938\u093e\u0902\u0938 \u092b\u0942\u0932\u0928\u093e"])

# Explicit negations only. We do NOT infer negation from a nearby "no", because
# "no puedo respirar" (I can't breathe) would read as a negation and silence the
# most urgent thing a patient can say. Listing the phrases keeps it safe.
NEGATIONS: dict[str, list[str]] = {
    "shortness of breath": ["no trouble breathing", "not short of breath",
                            "no shortness of breath", "breathing is fine",
                            "sin problemas para respirar", "呼吸没问题",
                            "pas de mal a respirer", "je respire bien"],
    "chest pain": ["no chest pain", "chest is fine", "sin dolor de pecho", "没有胸痛"],
    "fever": ["no fever", "sin fiebre", "没有发烧"],
    "fall": ["did not fall", "didn't fall", "no me cai", "没有摔倒",
             "je ne suis pas tombe"],
    "confusion": ["not confused", "no confusion", "no esta confundido"],
    "nausea": ["no nausea", "sin nausea", "pas de nausee"],
    "back pain": ["no back pain", "pas mal au dos"],
}

NEW_MARKERS = ["new", "never before", "first time", "suddenly", "sudden",
               "nuevo", "de repente", "突然",
               "nouveau", "soudain", "d'un coup", "jamais avant"]

SEVERITY_WORDS: list[tuple[str, int]] = [
    ("worst", 10), ("unbearable", 10), ("severe", 8), ("really bad", 8),
    ("very bad", 8), ("bad", 6), ("moderate", 5), ("some", 4),
    ("a little", 3), ("mild", 3), ("slight", 2),
    ("muy fuerte", 8), ("fuerte", 7), ("leve", 3),
    ("很严重", 8), ("有点", 3),
    ("insupportable", 10), ("tres fort", 8), ("fort", 7), ("un peu", 3),
    ("leger", 3),
]

ONSET_RE = re.compile(
    r"(since [a-z ]{3,25}|for (?:the )?(?:last |past )?\d+ (?:minutes?|hours?|days?|weeks?)"
    r"|this (?:morning|afternoon|evening)|last night|yesterday|today|right now)",
    re.IGNORECASE,
)

_NORMALIZE = str.maketrans({"’": "'", "“": '"', "”": '"'})


def fold(text: str) -> str:
    """Lowercase and strip accents.

    Deepgram returns properly accented text -- "náusea", "frío", "cogné la
    tête" -- while the phrase lists here are written unaccented. Without
    folding, a real transcript silently misses symptoms that a typed one
    catches, which is the worst kind of bug: it only appears on stage.

    Only marks attached to a Latin base are dropped. That restriction matters:
    the Devanagari virama and the Hindi vowel signs are combining marks too, and
    stripping them turns नमस्ते into नमसते -- silently mangling a language we
    claim to support. CJK has no combining marks and is untouched either way.
    """
    lowered = text.translate(_NORMALIZE).lower()
    out: list[str] = []
    latin_base = False
    for char in unicodedata.normalize("NFKD", lowered):
        if unicodedata.combining(char):
            if not latin_base:
                out.append(char)
            continue
        latin_base = char.isascii() or "LATIN" in unicodedata.name(char, "")
        out.append(char)
    return "".join(out)


def _severity(text: str, near: str) -> int | None:
    """Pick the strongest severity word that appears near the symptom phrase."""
    idx = text.find(near)
    window = text[max(0, idx - 45): idx + len(near) + 45] if idx >= 0 else text
    hits = [score for word, score in SEVERITY_WORDS if word in window]
    if m := re.search(r"\b([0-9]|10)\s*(?:out of|/)\s*10\b", window):
        hits.append(int(m.group(1)))
    return max(hits) if hits else None


def extract(text: str | None, language: str = "en") -> list[Symptom]:
    if not text:
        return []
    norm = fold(text)
    onset_match = ONSET_RE.search(norm)
    onset = onset_match.group(0).strip() if onset_match else None
    is_new = any(m in norm for m in NEW_MARKERS) or None

    found: list[Symptom] = []
    for label, phrases in LEXICON.items():
        if any(fold(neg) in norm for neg in NEGATIONS.get(label, [])):
            continue
        hit = next((fold(p) for p in phrases if fold(p) in norm), None)
        if hit:
            found.append(
                Symptom(
                    label=label,
                    severity=_severity(norm, hit),
                    onset=onset,
                    is_new=is_new,
                )
            )
    return found
