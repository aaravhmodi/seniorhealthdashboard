"""NEISS narratives: the free-text field that makes this dataset worth using.

A NEISS narrative is a coder's shorthand, not a sentence:

    "80YOF GLF AT HOME STRUCK HEAD ON NIGHTSTAND, ON COUMADIN, DX SDH"

Two jobs here, and they are deliberately separate:

1. **Features** (`features()`): the flags we can defend in front of a clinician
   -- head strike, loss of consciousness, mechanism, anticoagulant mention.
   These feed the outcome cut and the red-flag story. Pattern-matched, so a
   human can read the rule and disagree with it.

2. **Expansion** (`expand()`): turning the shorthand into something an
   embedding model can actually use. "80YOF GLF" embeds near nothing; "80 year
   old female ground level fall" embeds near a patient saying they fell at
   home. This is the difference between retrieval that works and retrieval that
   returns noise.

The abbreviations below are the common NEISS ones. The list is not exhaustive
and does not need to be -- an unexpanded token costs a little recall, it does
not produce a wrong answer.
"""
from __future__ import annotations

import re

# Longest first, so "GLF" is not eaten by a shorter overlapping key.
ABBREVIATIONS: list[tuple[str, str]] = [
    (r"\bGLF\b", "ground level fall"),
    (r"\bMFLF\b", "fall from a level higher than the ground"),
    (r"\bLOC\b", "loss of consciousness"),
    (r"\bS/P\b", "after"),
    (r"\bC/O\b", "complains of"),
    (r"\bH/O\b", "history of"),
    (r"\bR/O\b", "to rule out"),
    (r"\bW/\b", "with"),
    (r"\bWO\b", "without"),
    (r"\bDX\b", "diagnosis"),
    (r"\bFX\b", "fracture"),
    (r"\bLAC\b", "laceration"),
    (r"\bCONT\b", "contusion"),
    (r"\bABR\b", "abrasion"),
    (r"\bSDH\b", "subdural haematoma"),
    (r"\bICH\b", "bleeding inside the head"),
    (r"\bCHI\b", "closed head injury"),
    (r"\bHI\b", "head injury"),
    (r"\bPT\b", "patient"),
    (r"\bPTA\b", "before arrival"),
    (r"\bED\b", "emergency department"),
    (r"\bNH\b", "nursing home"),
    (r"\bBIB\b", "brought in by"),
    (r"\bEMS\b", "ambulance"),
    (r"\bAMS\b", "altered mental status"),
    (r"\bSOB\b", "shortness of breath"),
    (r"\bN/V\b", "nausea and vomiting"),
    (r"\bLE\b", "lower extremity"),
    (r"\bUE\b", "upper extremity"),
    (r"\bL\s?HIP\b", "left hip"),
    (r"\bR\s?HIP\b", "right hip"),
    (r"\bETOH\b", "alcohol"),
    (r"\bA&O\b", "alert and oriented"),
    (r"\bNAD\b", "no distress"),
]

# "80YOF" / "92 YO M" / "77YOM"
AGE_SEX = re.compile(r"\b(\d{1,3})\s*(?:YO|Y/O|YR|YEAR)\s*(?:OLD)?\s*([MF])?\b", re.I)


def expand(narrative: str | None) -> str:
    """Shorthand -> readable English, for embedding and for clinician display."""
    if not narrative:
        return ""
    text = " ".join(str(narrative).split())

    def _age_sex(match: re.Match) -> str:
        age, sex = match.group(1), (match.group(2) or "").upper()
        who = {"M": "man", "F": "woman"}.get(sex, "patient")
        return f"{age} year old {who}"

    text = AGE_SEX.sub(_age_sex, text)
    for pattern, replacement in ABBREVIATIONS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    # NEISS is upper case throughout; sentence case reads better and embeds the
    # same. Leave the rest of the casing alone.
    return text[:1].upper() + text[1:].lower() if text else ""


# --------------------------------------------------------------------------
# Features -- each one is a rule a clinician can read and argue with
# --------------------------------------------------------------------------
HEAD_STRIKE = (
    "struck head", "hit head", "head strike", "struck her head", "struck his head",
    "hit her head", "hit his head", "head lac", "scalp lac", "facial lac",
    "chi", "sdh", "ich", "head injury", "to head", "on head",
)

LOSS_OF_CONSCIOUSNESS = (
    "loc", "unresponsive", "passed out", "syncope", "syncopal", "fainted",
    "blacked out", "found down",
)

ANTICOAGULANTS = (
    "coumadin", "warfarin", "eliquis", "apixaban", "xarelto", "rivaroxaban",
    "plavix", "clopidogrel", "pradaxa", "dabigatran", "blood thinner",
    "anticoagulant", "on asa", "aspirin",
)

# Mechanism matters clinically: a fall down stairs is not a fall from a chair,
# and "found on floor" is an unwitnessed fall, which is worse than both.
MECHANISMS: list[tuple[str, tuple[str, ...]]] = [
    ("stairs", ("stairs", "steps", "stairway", "staircase")),
    ("bathroom", ("tub", "shower", "toilet", "bathroom", "commode")),
    ("bed", ("bed", "out of bed", "rolled out")),
    ("chair", ("chair", "wheelchair", "recliner", "sofa", "couch")),
    ("ladder_or_height", ("ladder", "roof", "stool", "step stool")),
    ("outdoors", ("sidewalk", "curb", "driveway", "yard", "garden", "street",
                  "parking lot", "ice", "snow")),
    ("unwitnessed", ("found on floor", "found down", "found on the floor")),
    ("ground_level", ("glf", "ground level", "tripped", "slipped", "stumbled")),
]


def features(narrative: str | None) -> dict:
    """Structured flags from one narrative. Matched on the raw shorthand."""
    raw = (narrative or "").lower()

    mechanism = next(
        (name for name, terms in MECHANISMS if any(t in raw for t in terms)),
        "unspecified",
    )
    return {
        "head_strike": any(t in raw for t in HEAD_STRIKE),
        "loss_of_consciousness": any(t in raw for t in LOSS_OF_CONSCIOUSNESS),
        "anticoagulant": any(t in raw for t in ANTICOAGULANTS),
        "mechanism": mechanism,
    }


def sql_flag(column: str, terms: tuple[str, ...]) -> str:
    """The same matching, as SQL, so the table build stays in DuckDB.

    Kept next to the Python version on purpose: if the two drift, the flags on
    the table stop agreeing with the flags in the app, and nobody notices.
    """
    clauses = " OR ".join(
        f"lower(CAST({column} AS VARCHAR)) LIKE '%{term}%'" for term in terms
    )
    return f"CASE WHEN {clauses} THEN true ELSE false END"


def sql_mechanism(column: str) -> str:
    whens = []
    for name, terms in MECHANISMS:
        clause = " OR ".join(
            f"lower(CAST({column} AS VARCHAR)) LIKE '%{term}%'" for term in terms
        )
        whens.append(f"WHEN {clause} THEN '{name}'")
    return "CASE " + " ".join(whens) + " ELSE 'unspecified' END"
