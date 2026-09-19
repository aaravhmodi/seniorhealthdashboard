"""Deterministic red-flag layer.

This runs BEFORE any model and its output is never overridden downward. It is
the layer we point at when a judge asks what happens if the LLM is wrong: the
can't-miss presentations escalate on a keyword match, not on a probability.

Rules are written against the extracted symptoms plus the raw text, so a rule
still fires when extraction misses a synonym.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .schemas import ActionLevel, CheckIn, RedFlag, Senior, Vitals


@dataclass(frozen=True)
class Rule:
    code: str
    label: str
    level: ActionLevel
    test: Callable[[CheckIn, Senior, str], list[str]]  # returns what matched


def _labels(checkin: CheckIn) -> set[str]:
    return {s.label for s in checkin.symptoms}


def _any_phrase(text: str, phrases: list[str]) -> list[str]:
    return [p for p in phrases if p in text]


# -- individual rules ------------------------------------------------------
def _stroke(c: CheckIn, s: Senior, text: str) -> list[str]:
    hits = _any_phrase(text, ["face droop", "drooping", "slurred", "weak on one side",
                              "one side", "can't lift", "cant lift", "嘴歪", "un lado"])
    if "weakness one side" in _labels(c):
        hits.append("symptom:weakness one side")
    return hits


def _cardiac(c: CheckIn, s: Senior, text: str) -> list[str]:
    labels = _labels(c)
    hits: list[str] = []
    if "chest pain" in labels:
        hits.append("symptom:chest pain")
    # Atypical MI in seniors: no chest pain, but breathlessness plus one of
    # nausea, sweating or sudden weakness. This is the geriatric miss we exist for.
    diaphoresis = ["sweaty", "cold sweat", "clammy", "sudando frio", "sudor frio",
                   "sudando", "冒冷汗", "出冷汗", "suando frio"]
    atypical = {"shortness of breath"} & labels and (
        {"nausea"} & labels or _any_phrase(text, diaphoresis)
    )
    if atypical:
        hits.append("atypical:breathless + nausea/diaphoresis")
    return hits


def _breathing(c: CheckIn, s: Senior, text: str) -> list[str]:
    hits = _any_phrase(text, ["can't breathe", "cant breathe", "gasping",
                              "no puedo respirar", "呼吸困难"])
    v = c.vitals or Vitals()
    if v.spo2 is not None and v.spo2 < 92:
        hits.append(f"spo2:{v.spo2}")
    return hits


def _altered_mental_status(c: CheckIn, s: Senior, text: str) -> list[str]:
    """New confusion in a senior is a red flag, not a personality trait.

    Classic geriatric trap: a UTI presenting as delirium. We escalate on new
    confusion, and escalate harder when urinary symptoms or fever ride along.
    """
    labels = _labels(c)
    if "confusion" not in labels:
        return []
    sym = next(x for x in c.symptoms if x.label == "confusion")
    hits = ["symptom:confusion"]
    if sym.is_new:
        hits.append("new onset")
    if "urinary symptoms" in labels:
        hits.append("co-occurring urinary symptoms")
    if "fever" in labels:
        hits.append("co-occurring fever")
    return hits


BLOOD_THINNERS = {"warfarin", "apixaban", "rivaroxaban", "clopidogrel",
                  "dabigatran", "eliquis", "xarelto", "coumadin", "plavix"}

HEAD_STRIKE = ["hit my head", "hit her head", "hit his head", "blacked out",
               "passed out", "knocked out", "me golpee la cabeza", "撞到头",
               "cogne la tete", "cognee la tete", "tape la tete", "perdu connaissance"]


def _on_blood_thinner(s: Senior) -> list[str]:
    return [
        m.name for m in s.medications
        if (m.ingredient or m.name).lower() in BLOOD_THINNERS
    ]


def _fall_reported(c: CheckIn, s: Senior, text: str) -> list[str]:
    """A fall on its own: worth a call, not an ambulance."""
    if "fall" not in _labels(c):
        return []
    return [] if _head_injury_fall(c, s, text) else ["symptom:fall"]


def _head_injury_fall(c: CheckIn, s: Senior, text: str) -> list[str]:
    """A fall becomes an ED trip on a head strike, or on any anticoagulant.

    Anticoagulated seniors bleed into the head from strikes that would be
    nothing in anyone else, and the bleed can present hours later.
    """
    if "fall" not in _labels(c):
        return []
    hits = _any_phrase(text, HEAD_STRIKE)
    on_thinner = _on_blood_thinner(s)
    if on_thinner:
        hits.append(f"anticoagulated:{on_thinner[0]}")
    return ["symptom:fall", *hits] if hits else []


def _severe_bleeding(c: CheckIn, s: Senior, text: str) -> list[str]:
    return _any_phrase(text, ["coughing blood", "vomiting blood", "blood in my stool",
                              "black stool", "won't stop bleeding", "wont stop bleeding"])


def _sepsis_ish(c: CheckIn, s: Senior, text: str) -> list[str]:
    v = c.vitals or Vitals()
    hits: list[str] = []
    if v.temp_c is not None and (v.temp_c >= 38.5 or v.temp_c <= 35.5):
        hits.append(f"temp:{v.temp_c}C")
    if v.heart_rate is not None and v.heart_rate >= 110:
        hits.append(f"hr:{v.heart_rate}")
    if v.systolic is not None and v.systolic < 100:
        hits.append(f"sbp:{v.systolic}")
    return hits if len(hits) >= 2 else []


def _hypertensive_or_hypo(c: CheckIn, s: Senior, text: str) -> list[str]:
    v = c.vitals or Vitals()
    hits: list[str] = []
    if v.systolic is not None and v.systolic >= 180:
        hits.append(f"sbp:{v.systolic}")
    if v.diastolic is not None and v.diastolic >= 120:
        hits.append(f"dbp:{v.diastolic}")
    if v.glucose_mgdl is not None and (v.glucose_mgdl < 60 or v.glucose_mgdl > 350):
        hits.append(f"glucose:{v.glucose_mgdl}")
    return hits


def _worst_headache(c: CheckIn, s: Senior, text: str) -> list[str]:
    if "headache" not in _labels(c):
        return []
    return _any_phrase(text, ["worst headache", "worst of my life", "thunderclap",
                              "sudden headache"])


RULES: list[Rule] = [
    Rule("stroke_fast", "Possible stroke (FAST signs)", ActionLevel.CALL_911, _stroke),
    Rule("breathing_distress", "Respiratory distress", ActionLevel.CALL_911, _breathing),
    Rule("severe_bleeding", "Significant bleeding", ActionLevel.CALL_911, _severe_bleeding),
    Rule("worst_headache", "Sudden worst-ever headache", ActionLevel.CALL_911, _worst_headache),
    Rule("cardiac_acs", "Possible cardiac event (incl. atypical presentation)",
         ActionLevel.GO_TO_ER, _cardiac),
    Rule("altered_mental_status", "New confusion / possible delirium",
         ActionLevel.GO_TO_ER, _altered_mental_status),
    Rule("fall_head_injury", "Fall with head-injury risk", ActionLevel.GO_TO_ER,
         _head_injury_fall),
    Rule("fall_reported", "Fall reported", ActionLevel.CALL_CLINIC, _fall_reported),
    Rule("sepsis_screen", "Two or more abnormal vitals (sepsis screen)",
         ActionLevel.GO_TO_ER, _sepsis_ish),
    Rule("vitals_out_of_range", "Vital sign outside safe range", ActionLevel.CALL_CLINIC,
         _hypertensive_or_hypo),
]


def evaluate_rules(checkin: CheckIn, senior: Senior) -> list[RedFlag]:
    text = (checkin.raw_text or "").lower()
    flags: list[RedFlag] = []
    for rule in RULES:
        matched = rule.test(checkin, senior, text)
        if matched:
            flags.append(
                RedFlag(
                    code=rule.code,
                    label=rule.label,
                    matched_on=matched,
                    forces_level=rule.level,
                )
            )
    return sorted(flags, key=lambda f: int(f.forces_level), reverse=True)


def rule_floor(flags: list[RedFlag]) -> ActionLevel:
    """The lowest level we are allowed to recommend given the flags that fired."""
    if not flags:
        return ActionLevel.LOG
    return ActionLevel(max(int(f.forces_level) for f in flags))
