"""What could this be, how sure are we, and what do you do at that number.

The ladder (``ladder.evaluate``) decides the *rung*: rules first, never
overridden downward. This module decides the *explanation* -- a ranked list of
named concerns, each with a probability, the evidence behind it, and the action
that probability earns. It can raise the recommended action, never lower it.

How a probability is built, in one place so nobody has to guess:

    1. A **base rate** from real data: the share of similar senior ED visits
       that ended in admission or transfer (NHAMCS reason-for-visit cohort, or
       the NEISS fall cells when the concern is a fall). That is the starting
       probability before we know anything about this particular person.

    2. **Modifiers** applied as odds multipliers -- age band, anticoagulation,
       a head strike, new onset, a FAERS medication signal, abnormal vitals,
       the person's own trend. Composed in log-odds, which is the only way to
       multiply evidence without a 140% answer falling out.

    3. The **fitted NEISS model** enters as one more multiplier: its odds
       relative to the cohort base rate. So the model is a contributor with a
       visible weight, not an oracle that replaces the epidemiology.

Every driver carries ``fitted``. ``True`` means the multiplier came out of data
(the model, a FAERS signal). ``False`` means it is a clinical weighting we chose
and are willing to defend out loud -- and the UI says which is which.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from .datasets import lookup
from .datasets import model as outcome_model
from .datasets.narratives import features as narrative_features
from .extraction import fold
from .rules import BLOOD_THINNERS, HEAD_STRIKE as SPOKEN_HEAD_STRIKE
from .schemas import (
    ActionLevel,
    BaselineSummary,
    CheckIn,
    DatasetNote,
    EvidenceKind,
    RedFlag,
    RiskAssessment,
    RiskBand,
    RiskConcern,
    RiskDriver,
    Senior,
    Vitals,
)

# --------------------------------------------------------------------------
# Bands: the percentage -> action mapping, stated once and shown to the user.
# --------------------------------------------------------------------------
BANDS: list[RiskBand] = [
    RiskBand(
        band="monitor", label="Keep watching", lower_percent=0, upper_percent=15,
        action="Log it and check in again tomorrow. Tell us if it changes.",
        action_level=ActionLevel.LOG,
    ),
    RiskBand(
        band="today", label="Get seen today", lower_percent=15, upper_percent=40,
        action="Call your clinic or pharmacist today and describe this.",
        action_level=ActionLevel.CALL_CLINIC,
    ),
    RiskBand(
        band="emergency", label="Emergency department", lower_percent=40, upper_percent=70,
        action="Go to the emergency department now. Do not drive yourself.",
        action_level=ActionLevel.GO_TO_ER,
    ),
    RiskBand(
        band="now", label="Call 911", lower_percent=70, upper_percent=100,
        action="Call 911 now and stay where you are.",
        action_level=ActionLevel.CALL_911,
    ),
]


def band_for(percent: float) -> RiskBand:
    for band in BANDS:
        if percent < band.upper_percent:
            return band
    return BANDS[-1]


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


# --------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------
@dataclass
class _Ctx:
    """Everything a concern needs to decide whether it fires and how hard."""

    checkin: CheckIn
    senior: Senior
    baseline: BaselineSummary
    flags: list[RedFlag]
    labels: set[str]
    vitals: Vitals
    narrative: dict
    anticoagulants: list[str]
    flag_codes: set[str]

    @property
    def age_band(self) -> str:
        age = self.senior.age
        return "85+" if age >= 85 else "75-84" if age >= 75 else "65-74"

    def symptom(self, label: str):
        return next((s for s in self.checkin.symptoms if s.label == label), None)

    def matched_on(self, code: str) -> list[str]:
        return [m for f in self.flags if f.code == code for m in f.matched_on]

    def abnormal_vitals(self) -> list[str]:
        v, out = self.vitals, []
        if v.spo2 is not None and v.spo2 < 92:
            out.append(f"oxygen {v.spo2}%")
        if v.systolic is not None and (v.systolic < 100 or v.systolic >= 180):
            out.append(f"top blood pressure {v.systolic}")
        if v.heart_rate is not None and (v.heart_rate >= 110 or v.heart_rate < 50):
            out.append(f"pulse {v.heart_rate}")
        if v.temp_c is not None and (v.temp_c >= 38.0 or v.temp_c <= 35.5):
            out.append(f"temperature {v.temp_c}C")
        if v.glucose_mgdl is not None and (v.glucose_mgdl < 60 or v.glucose_mgdl > 350):
            out.append(f"blood sugar {v.glucose_mgdl}")
        return out

    def med_pairs(self, event: str) -> list[tuple[str, str, dict]]:
        """Pairs on this patient's own list reported together with `event`.

        Returned for display only. FAERS pair counts are confounded by what the
        medicines are FOR -- two lung drugs look like they cause breathlessness
        because the patient has lung disease -- so this names the combination
        and asks for a pharmacist, and never multiplies anything.
        """
        resolved = []
        for med in self.senior.medications:
            spoken = med.ingredient or med.name
            resolved.append((med.name, lookup.resolve_ingredient(spoken) or spoken.lower()))

        found = []
        for i, (name_a, ing_a) in enumerate(resolved):
            for name_b, ing_b in resolved[i + 1:]:
                if ing_a == ing_b:
                    continue
                signal = lookup.drug_pair_signal(ing_a, ing_b, event)
                if signal and signal["significant"]:
                    found.append((name_a, name_b, signal))
        return found

    def med_signal(self, event: str):
        """Strongest FAERS signal between a medicine on the list and `event`."""
        best = None
        for med in self.senior.medications:
            spoken = med.ingredient or med.name
            ingredient = lookup.resolve_ingredient(spoken) or spoken.lower()
            signal = lookup.drug_event_signal(ingredient, event)
            if signal and signal["significant"]:
                if best is None or signal["eb_rrr"] > best[1]["eb_rrr"]:
                    best = (med.name, signal, ingredient)
        return best


def _driver(label: str, kind: EvidenceKind, multiplier: float, detail: str,
            source: str, fitted: bool = False) -> RiskDriver:
    return RiskDriver(
        label=label, kind=kind, delta_points=0.0, multiplier=round(multiplier, 3),
        detail=detail, source=source, fitted=fitted,
    )


# --------------------------------------------------------------------------
# Shared modifiers
# --------------------------------------------------------------------------
def _age_driver(ctx: _Ctx) -> RiskDriver | None:
    """Older is worse. The cohort rate is already age-banded, so this is only
    the residual inside the band -- kept deliberately small."""
    if ctx.senior.age >= 85:
        multiplier = 1.45
    elif ctx.senior.age >= 75:
        multiplier = 1.15
    else:
        return None
    return _driver(
        f"Age {ctx.senior.age}", EvidenceKind.BASELINE, multiplier,
        f"Adults in the {ctx.age_band} band are admitted more often from the "
        f"same presentation than the younger end of the 65+ cohort.",
        "Clinical weighting from geriatric triage practice, not fitted",
    )


def _new_onset_driver(ctx: _Ctx, label: str) -> RiskDriver | None:
    symptom = ctx.symptom(label)
    if not symptom or not symptom.is_new:
        return None
    return _driver(
        f"{label.title()} is new", EvidenceKind.BASELINE, 1.3,
        f"Not part of the last {ctx.baseline.window_days} days of check-ins. "
        f"New beats chronic when deciding how fast to move.",
        f"This patient's own check-ins, last {ctx.baseline.window_days} days",
    )


def _trend_driver(ctx: _Ctx, label: str) -> RiskDriver | None:
    if label not in set(ctx.baseline.trending_up):
        return None
    return _driver(
        f"{label.title()} is increasing", EvidenceKind.BASELINE, 1.35,
        f"Reported more in the last week than the week before, across "
        f"{ctx.baseline.checkin_count} check-ins.",
        f"This patient's own check-ins, last {ctx.baseline.window_days} days",
    )


def _severity_driver(ctx: _Ctx, label: str) -> RiskDriver | None:
    symptom = ctx.symptom(label)
    if not symptom or symptom.severity is None or symptom.severity < 7:
        return None
    return _driver(
        f"You scored it {symptom.severity} out of 10", EvidenceKind.BASELINE,
        1.2 + 0.1 * (symptom.severity - 7),
        "Severity as the patient rated it, not as anyone interpreted it.",
        "Reported in this check-in",
    )


def _vitals_driver(ctx: _Ctx) -> RiskDriver | None:
    abnormal = ctx.abnormal_vitals()
    if not abnormal:
        return None
    multiplier = min(2.6, 1.0 + 0.55 * len(abnormal))
    plural = "s" if len(abnormal) > 1 else ""
    return _driver(
        f"{len(abnormal)} vital sign{plural} out of range", EvidenceKind.RULE,
        multiplier, "Measured with this check-in: " + ", ".join(abnormal) + ".",
        "Vitals entered with this check-in",
    )


def _faers_driver(ctx: _Ctx, event: str) -> RiskDriver | None:
    """A medicine the person actually takes, disproportionately reported with
    the symptom they actually have."""
    best = ctx.med_signal(event)
    if not best:
        return None
    name, signal, ingredient = best
    return _driver(
        f"{name} is reported with {event}", EvidenceKind.FAERS,
        min(2.2, max(1.1, signal["eb_rrr"] / 2)),
        f"{signal['eb_rrr']:.1f}x more reports than expected when {ingredient} "
        f"is the suspected cause ({signal['n']:,} reports, 95% CI "
        f"{signal['ci_low']:.1f}-{signal['ci_high']:.1f}). That is a reporting "
        f"pattern, not proof of cause -- but it is worth a medication review.",
        signal["source"], fitted=True,
    )


def _anticoagulant_driver(ctx: _Ctx, multiplier: float, why: str) -> RiskDriver | None:
    if not ctx.anticoagulants:
        return None
    return _driver(
        f"You take {ctx.anticoagulants[0]}", EvidenceKind.FAERS, multiplier, why,
        "Medication list on this profile",
    )


# --------------------------------------------------------------------------
# Triggers
# --------------------------------------------------------------------------
def _reported(ctx: _Ctx, *labels: str) -> list[str]:
    return [f"you reported {label}" for label in labels if label in ctx.labels]


def _head_bleed_trigger(ctx: _Ctx) -> list[str]:
    """A plain fall is the fracture concern. It becomes the bleed concern on a
    head strike, on a blackout, or on any anticoagulant."""
    if "fall" not in ctx.labels:
        return []
    hits = ["you reported a fall"]
    if ctx.narrative["head_strike"]:
        hits.append("you said you hit your head")
    if ctx.narrative["loss_of_consciousness"]:
        hits.append("you blacked out")
    if ctx.anticoagulants:
        hits.append(f"you take {ctx.anticoagulants[0]}, a blood thinner")
    return hits if len(hits) > 1 else []


def _cardiac_trigger(ctx: _Ctx) -> list[str]:
    hits = _reported(ctx, "chest pain")
    if "cardiac_acs" in ctx.flag_codes and not hits:
        hits.append(
            "breathlessness with nausea or a cold sweat, which is how a heart "
            "attack often looks in an older adult"
        )
    return hits


def _delirium_trigger(ctx: _Ctx) -> list[str]:
    if "confusion" not in ctx.labels:
        return []
    return _reported(ctx, "confusion", "urinary symptoms", "fever")


def _medication_trigger(ctx: _Ctx) -> list[str]:
    hits = []
    for label in sorted(ctx.labels):
        best = ctx.med_signal(label)
        if best:
            hits.append(f"you take {best[0]} and reported {label}")
        # A pair can raise the concern even when neither drug does on its own,
        # which is the whole reason interactions are worth looking for.
        for name_a, name_b, signal in ctx.med_pairs(label):
            hits.append(
                f"you take {name_a} and {name_b} together, which are reported "
                f"with {label} {signal['eb_ratio']:.1f}x more than either alone "
                f"predicts"
            )
    return hits


# --------------------------------------------------------------------------
# The catalog
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Concern:
    code: str
    label: str
    plain: str
    # What in the check-in raises this at all. An empty list means we stay quiet
    # about it -- we do not list every disease a symptom could theoretically be.
    trigger: Callable[[_Ctx], list[str]]
    # Which symptom's cohort admission rate anchors the number.
    base_symptom: str | None
    # Used only when the warehouse has no cell for that cohort.
    fallback_base: float
    fallback_note: str
    modifiers: Callable[[_Ctx], list[RiskDriver]]
    # Once fired, this concern never recommends anything gentler than this.
    min_level: ActionLevel


def _head_bleed_modifiers(ctx: _Ctx) -> list[RiskDriver]:
    out = []
    if ctx.narrative["head_strike"]:
        out.append(_driver(
            "Head took the impact", EvidenceKind.NEISS, 2.4,
            "The words you used describe the head hitting something.",
            "Narrative parse of this check-in, same rules as the NEISS cells",
        ))
    if ctx.narrative["loss_of_consciousness"]:
        out.append(_driver(
            "You blacked out", EvidenceKind.NEISS, 2.0,
            "Losing consciousness after a head strike sharply raises the odds "
            "of a bleed inside the skull.",
            "Narrative parse of this check-in",
        ))
    out.append(_anticoagulant_driver(
        ctx, 3.0,
        "Anticoagulated patients bleed into the head from strikes that would be "
        "nothing in anyone else, and the bleed often declares itself hours later.",
    ))
    out.append(_age_driver(ctx))
    return [d for d in out if d]


def _fracture_modifiers(ctx: _Ctx) -> list[RiskDriver]:
    return [d for d in (_age_driver(ctx), _severity_driver(ctx, "fall"),
                        _trend_driver(ctx, "fall")) if d]


def _cardiac_modifiers(ctx: _Ctx) -> list[RiskDriver]:
    out = [_age_driver(ctx), _vitals_driver(ctx), _severity_driver(ctx, "chest pain")]
    if "shortness of breath" in ctx.labels:
        out.append(_driver(
            "Breathless as well", EvidenceKind.NEISS, 1.6,
            "Shortness of breath alongside the chest symptom.",
            "Reported in this check-in",
        ))
    return [d for d in out if d]


def _delirium_modifiers(ctx: _Ctx) -> list[RiskDriver]:
    out = [_new_onset_driver(ctx, "confusion")]
    if "urinary symptoms" in ctx.labels:
        out.append(_driver(
            "Urinary symptoms too", EvidenceKind.NEISS, 1.7,
            "New confusion plus urinary symptoms is the classic geriatric "
            "urinary-infection-as-delirium presentation.",
            "Reported in this check-in",
        ))
    if "fever" in ctx.labels:
        out.append(_driver(
            "Fever too", EvidenceKind.NEISS, 1.5,
            "Fever alongside new confusion widens this to any source of infection.",
            "Reported in this check-in",
        ))
    out += [_faers_driver(ctx, "confusion"), _age_driver(ctx), _vitals_driver(ctx)]
    return [d for d in out if d]


def _breathing_modifiers(ctx: _Ctx) -> list[RiskDriver]:
    out = [_age_driver(ctx), _vitals_driver(ctx),
           _new_onset_driver(ctx, "shortness of breath"),
           _trend_driver(ctx, "shortness of breath")]
    if "swelling legs" in ctx.labels:
        out.append(_driver(
            "Legs are swelling", EvidenceKind.NEISS, 1.6,
            "Breathlessness with leg swelling points at the heart rather than "
            "the lungs.", "Reported in this check-in",
        ))
    return [d for d in out if d]


def _sepsis_modifiers(ctx: _Ctx) -> list[RiskDriver]:
    return [d for d in (_age_driver(ctx), _vitals_driver(ctx),
                        _new_onset_driver(ctx, "confusion")) if d]


def _medication_modifiers(ctx: _Ctx) -> list[RiskDriver]:
    out = [d for d in (_faers_driver(ctx, label) for label in sorted(ctx.labels)) if d]
    if len(ctx.senior.medications) >= 5:
        out.append(_driver(
            f"{len(ctx.senior.medications)} medicines on your list",
            EvidenceKind.FAERS, 1.3,
            "More medicines means more chances that one of them is the cause, "
            "and more chances that two of them interact.",
            "Medication list on this profile",
        ))
    out.append(_age_driver(ctx))
    return [d for d in out if d][:4]


def _dehydration_modifiers(ctx: _Ctx) -> list[RiskDriver]:
    return [d for d in (_age_driver(ctx), _vitals_driver(ctx),
                        _faers_driver(ctx, "dizziness"),
                        _trend_driver(ctx, "dizziness")) if d]


def _spinal_modifiers(ctx: _Ctx) -> list[RiskDriver]:
    return [d for d in (_age_driver(ctx), _severity_driver(ctx, "back pain")) if d]


CONCERNS: tuple[Concern, ...] = (
    Concern(
        code="head_bleed",
        label="Bleeding inside the head",
        plain="A knock to the head can bleed slowly inside the skull, and it can "
              "start hours later. That is why this one is judged on the risk, not "
              "on how you feel right now.",
        trigger=_head_bleed_trigger,
        base_symptom="fall",
        fallback_base=0.31,
        fallback_note="NEISS-shaped placeholder for falls in adults 65+",
        modifiers=_head_bleed_modifiers,
        min_level=ActionLevel.GO_TO_ER,
    ),
    Concern(
        code="fracture",
        label="A broken bone from the fall",
        plain="Hips and wrists break easily in a fall, and a cracked hip can "
              "still take weight for a while before it gives way.",
        trigger=lambda ctx: _reported(ctx, "fall"),
        base_symptom="fall",
        fallback_base=0.31,
        fallback_note="NEISS-shaped placeholder for falls in adults 65+",
        modifiers=_fracture_modifiers,
        min_level=ActionLevel.CALL_CLINIC,
    ),
    Concern(
        code="cardiac",
        label="A heart problem",
        plain="In older adults a heart attack often arrives without chest pain "
              "-- breathlessness, nausea or a cold sweat instead.",
        trigger=_cardiac_trigger,
        base_symptom="chest pain",
        fallback_base=0.45,
        fallback_note="NEISS-shaped placeholder for chest pain in adults 65+",
        modifiers=_cardiac_modifiers,
        min_level=ActionLevel.GO_TO_ER,
    ),
    Concern(
        code="stroke",
        label="A stroke",
        plain="Face droop, arm weakness or trouble speaking. Stroke treatment "
              "runs on a clock, so this is called on suspicion, not certainty.",
        trigger=lambda ctx: [f"a stroke sign matched: {m}"
                             for m in ctx.matched_on("stroke_fast")],
        base_symptom="weakness one side",
        fallback_base=0.61,
        fallback_note="NEISS-shaped placeholder for one-sided weakness, 65+",
        modifiers=lambda ctx: [d for d in (_age_driver(ctx), _vitals_driver(ctx)) if d],
        min_level=ActionLevel.CALL_911,
    ),
    Concern(
        code="infection_delirium",
        label="An infection showing up as confusion",
        plain="In older adults a urine or chest infection often shows first as "
              "new confusion, before any fever appears.",
        trigger=_delirium_trigger,
        base_symptom="confusion",
        fallback_base=0.52,
        fallback_note="NEISS-shaped placeholder for confusion in adults 65+",
        modifiers=_delirium_modifiers,
        min_level=ActionLevel.GO_TO_ER,
    ),
    Concern(
        code="breathing",
        label="A breathing problem",
        plain="Fluid on the lungs, a chest infection and a clot all start the "
              "same way: short of breath.",
        trigger=lambda ctx: _reported(ctx, "shortness of breath"),
        base_symptom="shortness of breath",
        fallback_base=0.48,
        fallback_note="NEISS-shaped placeholder for breathlessness in adults 65+",
        modifiers=_breathing_modifiers,
        min_level=ActionLevel.GO_TO_ER,
    ),
    Concern(
        code="sepsis",
        label="An infection spreading through the body",
        plain="Sepsis is what an infection does when it stops staying put. It "
              "moves fast, and the vital signs move first.",
        trigger=lambda ctx: [f"vital sign out of range: {m}"
                             for m in ctx.matched_on("sepsis_screen")],
        base_symptom="fever",
        fallback_base=0.28,
        fallback_note="NEISS-shaped placeholder for fever in adults 65+",
        modifiers=_sepsis_modifiers,
        min_level=ActionLevel.GO_TO_ER,
    ),
    Concern(
        code="medication_effect",
        label="One of your medicines causing this",
        plain="The most fixable thing on this list. A pharmacist can often sort "
              "it out the same day. Where two medicines are named together, "
              "that is a prompt to have the combination reviewed, not a finding "
              "that the pair caused this.",
        trigger=_medication_trigger,
        base_symptom=None,
        fallback_base=0.22,
        fallback_note="A clinical prior for a medication-attributable symptom, "
                      "not an admission rate",
        modifiers=_medication_modifiers,
        min_level=ActionLevel.CALL_CLINIC,
    ),
    Concern(
        code="dehydration",
        label="Low blood pressure or dehydration",
        plain="Common, usually fixable, and the usual reason for dizziness on "
              "standing -- but it is also how the serious ones start.",
        trigger=lambda ctx: _reported(ctx, "dizziness"),
        base_symptom="dizziness",
        fallback_base=0.20,
        fallback_note="NEISS-shaped placeholder for dizziness in adults 65+",
        modifiers=_dehydration_modifiers,
        min_level=ActionLevel.CALL_CLINIC,
    ),
    Concern(
        code="spinal_cord",
        label="Pressure on the nerves in your back",
        plain="Back pain with leg weakness, numbness or a bladder you cannot "
              "control is the one back-pain pattern that cannot wait.",
        trigger=lambda ctx: [f"back pain with: {m}"
                             for m in ctx.matched_on("back_pain_emergency")],
        base_symptom="back pain",
        fallback_base=0.30,
        fallback_note="Clinical prior for a cord-compression workup",
        modifiers=_spinal_modifiers,
        min_level=ActionLevel.GO_TO_ER,
    ),
)

CONCERNS_BY_CODE = {c.code: c for c in CONCERNS}


# --------------------------------------------------------------------------
# Base rates
# --------------------------------------------------------------------------
def _base_rate(concern: Concern, ctx: _Ctx) -> tuple[float, str]:
    """Where the number starts, and the sentence that says where it came from."""
    if concern.base_symptom == "fall":
        on_thinner = bool(ctx.anticoagulants)
        cell = (
            lookup.fall_outcome(
                head_strike=ctx.narrative["head_strike"],
                on_anticoagulant=on_thinner,
                age_band=ctx.age_band,
            )
            or lookup.fall_outcome(
                head_strike=ctx.narrative["head_strike"], on_anticoagulant=on_thinner
            )
            or lookup.fall_admission_rate(on_thinner)
        )
        if cell:
            cut = []
            if ctx.narrative["head_strike"]:
                cut.append("with a head strike")
            if on_thinner:
                cut.append("on a blood thinner")
            band = cell.get("age_band") or "65+"
            where = f"falls {' '.join(cut)} in adults {band}".replace("  ", " ")
            return cell["rate_percent"] / 100, (
                f"{cell['rate_percent']:.0f}% of {where} ended in hospital rather "
                f"than being sent home ({cell['ci_low']:.0f}-{cell['ci_high']:.0f}%, "
                f"n={cell['n']:,}). Source: {cell['source']}."
            )

    if concern.base_symptom:
        cell = lookup.admission_rate(concern.base_symptom, ctx.senior.age)
        if cell:
            return cell["rate_percent"] / 100, (
                f"{cell['rate_percent']:.0f}% of emergency visits for "
                f"{concern.base_symptom} in adults {cell['age_band']} ended in "
                f"admission or transfer ({cell['ci_low']:.0f}-"
                f"{cell['ci_high']:.0f}%, n={cell['n']:,}). Source: "
                f"{cell['source']}."
            )

    return concern.fallback_base, (
        f"{concern.fallback_base * 100:.0f}% starting point. "
        f"{concern.fallback_note} -- load the warehouse (see docs/DATA.md) and "
        f"this swaps to the measured cohort rate with no code change."
    )


def _model_driver(model_risk: float | None, base: float, tokens: list[str],
                  metrics: dict) -> RiskDriver | None:
    """The fitted model as one more multiplier, expressed against the base rate.

    Its odds ratio versus the cohort is exactly what it adds over knowing only
    "this is a senior with this complaint". We cap it: a single linear model on
    triage text is a good reviewer and a bad oracle.
    """
    if model_risk is None:
        return None
    ratio = (model_risk / max(1e-4, 1 - model_risk)) / (base / max(1e-4, 1 - base))
    # Capped on both ends. A single linear model over triage text is a good
    # reviewer and a bad oracle: it may double the odds, it may not invent them.
    multiplier = min(2.2, max(0.5, ratio))
    auc = metrics.get("holdout_auc_weighted")
    quality = (
        f"holdout AUC {auc:.3f} on {', '.join(metrics.get('holdout_years') or []) or 'n/a'}"
        if auc else f"status: {metrics.get('status', 'unknown')}"
    )
    keyed = f" It keyed on: {', '.join(tokens)}." if tokens else ""
    return _driver(
        f"Trained outcome model says {model_risk * 100:.0f}%", EvidenceKind.MODEL,
        multiplier,
        f"A logistic model fitted on {metrics.get('senior_n', 0):,} NEISS senior "
        f"emergency records from {', '.join(metrics.get('years') or []) or 'n/a'}, "
        f"using only what is known at check-in -- the words, age, where it "
        f"happened, what was involved. The outcome is never a feature "
        f"({quality}).{keyed}",
        "NEISS outcome model, triage-time features only",
        fitted=True,
    )


def _apply(base: float, drivers: list[RiskDriver]) -> float:
    """Compose the multipliers in log-odds and fill in each driver's delta."""
    total = _logit(base) + sum(math.log(max(1e-4, d.multiplier)) for d in drivers)
    final = _sigmoid(total)
    for driver in drivers:
        # Percentage points this driver is responsible for: the answer with it
        # minus the answer without it. Honest, and it does not have to sum.
        without = _sigmoid(total - math.log(max(1e-4, driver.multiplier)))
        driver.delta_points = round((final - without) * 100, 1)
    return final


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def _datasets(metrics: dict) -> list[DatasetNote]:
    """What is behind the numbers, read off what is actually loaded.

    Only NEISS is trained on -- it is the one source with an outcome per case.
    NHAMCS supplies base rates and FAERS supplies medication signals; neither
    is a model, and the panel should not let anyone think otherwise.
    """
    from .datasets.warehouse import status as warehouse_status

    tables = (warehouse_status().get("tables") or {})

    def rows(name: str) -> int | None:
        entry = tables.get(name)
        return entry.get("rows") if entry else None

    # The model reads neiss_senior_cases directly and it is not in the
    # warehouse status list, so the training count is the authority for
    # whether NEISS is here at all.
    neiss_rows = metrics.get("senior_n") or rows("neiss_senior_rates")
    nhamcs_rows = rows("nhamcs_senior_rates")
    faers_rows = rows("faers_signals")
    pair_rows = rows("faers_pair_signals")

    years = ", ".join(metrics.get("years") or [])
    auc = metrics.get("holdout_auc_weighted")

    notes = [
        DatasetNote(
            name="NEISS",
            role="Trained outcome model, and the fall cohort",
            detail=(
                (
                    f"{metrics.get('senior_n', 0):,} emergency records for adults 65+"
                    + (f" across {years}" if years else "")
                    + ". The only source we fit a model on, because it is the "
                    "only one with an outcome attached to each case"
                    + (f"; held-out AUC {auc:.3f}" if auc else "")
                    + ". Also supplies the fall rates, cut by head strike, "
                    "anticoagulant and age band."
                )
                if neiss_rows else
                "Not loaded here, so there is no fitted model and falls start "
                "from a published placeholder rate. See docs/DATA.md."
            ),
            loaded=bool(neiss_rows),
            trained=metrics.get("status") == "ok",
            rows=neiss_rows,
        ),
        DatasetNote(
            name="NHAMCS",
            role="Where each percentage starts",
            detail=(
                "The CDC's national emergency department survey: how often a "
                "visit for this complaint, at this age, ended in hospital rather "
                "than going home. Survey-weighted, so it describes the country "
                "rather than the hospitals that were sampled. Nothing is fitted "
                "on it -- it is the base rate every concern begins from."
                if nhamcs_rows else
                "Not loaded here, so non-fall concerns start from a published "
                "placeholder rate. Each card says so. See docs/DATA.md."
            ),
            loaded=bool(nhamcs_rows),
            rows=nhamcs_rows,
        ),
        DatasetNote(
            name="FAERS",
            role="Medication signals",
            detail=(
                "FDA adverse-event reports for adults 65+, de-duplicated by case. "
                "Tells us when a medicine on your list is reported with the "
                "symptom you described more often than expected"
                + (
                    ", and when two of them together are reported with it more "
                    "than either alone predicts"
                    if pair_rows else ""
                )
                + ". Reporting patterns, not rates of harm, and never a cause."
                if faers_rows else
                "Not loaded here, so medication cards fall back to placeholders."
            ),
            loaded=bool(faers_rows),
            rows=faers_rows,
        ),
        DatasetNote(
            name="Your own check-ins",
            role="What is new or changing",
            detail=(
                "The part no national dataset can supply: whether a symptom is "
                "new for you, and whether it is being reported more often than "
                "it was last week."
            ),
            loaded=True,
        ),
    ]
    return notes


def assess(
    checkin: CheckIn,
    senior: Senior,
    baseline: BaselineSummary,
    flags: list[RedFlag],
    ladder_level: ActionLevel,
) -> RiskAssessment:
    """Rank what this could be, with a number and an action for each."""
    metrics = outcome_model.status()
    model_risk = outcome_model.predict(checkin, senior)
    tokens = outcome_model.explain_tokens(checkin, senior) if model_risk is not None else []

    # `narratives.features` matches NEISS clinician shorthand ("struck head");
    # a patient says "I hit my head on the sink". Both count, or the cut that
    # matters most for an anticoagulated senior silently never fires.
    narrative = narrative_features(checkin.raw_text)
    folded = fold(checkin.raw_text or "")
    if any(fold(phrase) in folded for phrase in SPOKEN_HEAD_STRIKE):
        narrative = {**narrative, "head_strike": True}

    ctx = _Ctx(
        checkin=checkin,
        senior=senior,
        baseline=baseline,
        flags=flags,
        labels={s.label for s in checkin.symptoms},
        vitals=checkin.vitals or Vitals(),
        narrative=narrative,
        anticoagulants=[
            m.name for m in senior.medications
            if (m.ingredient or m.name).lower() in BLOOD_THINNERS
        ],
        flag_codes={f.code for f in flags},
    )

    concerns: list[RiskConcern] = []
    for concern in CONCERNS:
        matched = concern.trigger(ctx)
        if not matched:
            continue
        base, base_detail = _base_rate(concern, ctx)
        drivers = list(concern.modifiers(ctx))
        # The fitted model speaks to how sick this person looks overall, so it
        # informs every concern -- but only where the base rate is an outcome
        # rate it can be compared against.
        if concern.base_symptom:
            model_driver = _model_driver(model_risk, base, tokens, metrics)
            if model_driver:
                drivers.append(model_driver)
        probability = _apply(base, drivers)
        percent = round(probability * 100, 1)
        band = band_for(percent)
        level = ActionLevel(max(int(band.action_level), int(concern.min_level)))
        # A probability may send someone to the emergency department. Only a
        # deterministic red flag calls an ambulance -- an 80% number about a
        # patient who can be driven does not earn a 911, and over-triaging the
        # drivable cases is how a system teaches people to ignore it.
        if (int(level) > int(ActionLevel.GO_TO_ER)
                and int(ladder_level) < int(ActionLevel.CALL_911)):
            level = ActionLevel.GO_TO_ER
        action = next(b.action for b in BANDS if b.action_level == level)
        concerns.append(RiskConcern(
            code=concern.code,
            label=concern.label,
            plain=concern.plain,
            probability_percent=percent,
            band=band.band,
            band_label=band.label,
            action=action,
            action_level=level,
            base_rate_percent=round(base * 100, 1),
            base_rate_detail=base_detail,
            drivers=sorted(drivers, key=lambda d: d.delta_points, reverse=True),
            matched_on=matched,
        ))

    concerns.sort(
        key=lambda c: (int(c.action_level), c.probability_percent), reverse=True
    )

    top = concerns[0] if concerns else None
    return RiskAssessment(
        concerns=concerns,
        top_concern=top.code if top else None,
        overall_percent=max((c.probability_percent for c in concerns), default=None),
        bands=BANDS,
        datasets=_datasets(metrics),
        model_status=metrics.get("status", "unavailable") if model_risk is not None
        else "not_loaded",
        model_risk_percent=round(model_risk * 100, 1) if model_risk is not None else None,
        model_auc=metrics.get("holdout_auc_weighted"),
        model_n=metrics.get("senior_n"),
        model_years=list(metrics.get("years") or []),
        model_holdout_years=list(metrics.get("holdout_years") or []),
        model_basis=(
            f"Logistic regression over TF-IDF of the check-in words plus age, "
            f"location, product and body part -- fitted on "
            f"{metrics.get('senior_n', 0):,} NEISS emergency records for adults "
            f"65+ across {', '.join(metrics.get('years') or []) or 'no loaded years'}. "
            f"The outcome (admitted, transferred or held for observation) is the "
            f"label and is never a feature, and the score is validated on a "
            f"held-out later year, not a random split."
            if model_risk is not None else
            "The fitted model is not available in this deployment, so every "
            "number below comes from published cohort rates and the rules. "
            "Load the NEISS warehouse (docs/DATA.md) to turn it on."
        ),
        model_tokens=tokens,
        ladder_floor=ladder_level,
        ladder_note=(
            "These percentages explain and rank. They never lower the "
            "recommendation -- the red-flag rules set the floor and the action "
            "shown is the more urgent of the two -- and they stop at the "
            "emergency department. Calling 911 stays with the rules."
        ),
    )
