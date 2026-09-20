"""Choose the next question after a check-in.

The question is deliberately separate from the action ladder: it helps collect
the next useful fact, but it never changes the safety level by itself.
"""
from __future__ import annotations

from .schemas import FollowUp, RiskConcern


_QUESTIONS: dict[str, tuple[str, str]] = {
    "head_bleed": ("Did you hit your head or pass out, and are you taking a blood thinner?", "Your answer helps us tell a simple fall from a possible head injury."),
    "fracture": ("Can you put weight on it, and is anything bent, numb, or badly swollen?", "Your answer helps us judge how urgently the injury needs to be seen."),
    "cardiac": ("Are you having chest pressure, shortness of breath, sweating, or nausea right now?", "Your answer helps us check for warning signs that need urgent attention."),
    "stroke": ("Did the weakness or speech trouble start suddenly, and is it on one side?", "The timing and one-sided pattern are important emergency warning signs."),
    "infection_delirium": ("Do you have a fever, burning when you urinate, or new confusion?", "Those details help separate a mild symptom from an infection needing care."),
    "breathing": ("Are you short of breath at rest, and can you speak a full sentence?", "Your answer helps us judge how much the breathing problem is affecting you now."),
    "sepsis": ("Do you have a fever, shaking chills, feel faint, or feel newly confused?", "These details help us look for signs of a serious infection."),
    "medication_effect": ("When did this start relative to your last dose, and did anything change?", "The timing helps a pharmacist decide whether a medicine could be contributing."),
    "dehydration": ("Have you been able to drink and urinate normally today?", "Your answer helps us check whether dehydration could be making this worse."),
    "spinal_cord": ("Do you have leg weakness or numbness, trouble walking, or trouble controlling your bladder or bowels?", "Those symptoms would make back pain more urgent."),
}

_GENERIC = (
    "What else should I know: when did it start, and is it getting better or worse?",
    "Your answer helps us understand the timing and whether anything is changing.",
)


def choose_follow_up(concerns: list[RiskConcern], language: str = "en") -> FollowUp:
    """Return one useful next question, never ``None``."""
    concern = concerns[0] if concerns else None
    question, why = _QUESTIONS.get(concern.code, _GENERIC) if concern else _GENERIC
    return FollowUp(
        code=f"probe:{concern.code}" if concern else "probe:generic",
        question=question,
        why=why,
        concern_code=concern.code if concern else None,
        concern_label=concern.label if concern else None,
        sharpens=1.0,
        opens=False,
        generic=concern is None,
    )

