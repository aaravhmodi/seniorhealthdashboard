"""Evidence cards.

Real numbers when the warehouse is loaded, placeholders when it is not, and the
`source` string always says which. The UI renders a mock badge for any source
beginning with "MOCK", and only non-mock figures may appear in the deck.

The swap is automatic: each builder asks `datasets.lookup` first and falls back
to its placeholder when that returns None. Run the loaders (see docs/DATA.md)
and the cards change under the running app -- no code change, no redeploy.

    neiss_cards(checkin, senior)       -> injury/fall outcomes  (NEISS)
    faers_cards(checkin, senior)       -> medication signals    (FAERS)
    baseline_cards(baseline, checkin)  -> this patient's own trend
"""
from __future__ import annotations

from .datasets import lookup

from .schemas import (
    BaselineSummary,
    CheckIn,
    EvidenceCard,
    EvidenceKind,
    Senior,
    Stat,
)

MOCK = "MOCK placeholder -- replaced by the NEISS/FAERS service in Sprint 2"

BLOOD_THINNERS = {"warfarin", "apixaban", "rivaroxaban", "clopidogrel",
                  "dabigatran", "eliquis", "xarelto", "coumadin", "plavix"}

# symptom -> (admit-rate %, n, ci) for a 65+ cohort. Invented, plausible shape.
_NEISS_MOCK: dict[str, tuple[float, int, tuple[float, float]]] = {
    "fall": (31.4, 18422, (30.1, 32.7)),
    "chest pain": (44.8, 6110, (42.9, 46.7)),
    "shortness of breath": (48.2, 5387, (46.1, 50.3)),
    "confusion": (52.6, 3094, (50.0, 55.2)),
    "weakness one side": (61.0, 1408, (57.8, 64.2)),
    "dizziness": (19.7, 7731, (18.6, 20.8)),
    "urinary symptoms": (22.3, 2560, (20.7, 23.9)),
    "fever": (27.9, 4102, (26.4, 29.4)),
    "nausea": (17.1, 5220, (16.0, 18.2)),
    "swelling legs": (25.4, 1980, (23.5, 27.3)),
}

# ingredient -> (event, reporting-odds-ratio, n reports)
_FAERS_MOCK: dict[str, list[tuple[str, float, int]]] = {
    "lisinopril": [("dizziness", 2.1, 4180), ("cough", 6.4, 12904)],
    "metformin": [("nausea", 2.8, 9110), ("confusion", 1.2, 880)],
    "furosemide": [("dizziness", 3.4, 6203), ("dehydration", 5.1, 3011)],
    "amlodipine": [("swelling legs", 7.9, 11402), ("dizziness", 2.0, 3980)],
    "warfarin": [("bleeding", 9.6, 22841), ("fall", 1.9, 2204)],
    "zolpidem": [("fall", 4.7, 5602), ("confusion", 4.1, 3388)],
    "oxybutynin": [("confusion", 3.9, 1502)],
    "sertraline": [("dizziness", 1.8, 4400), ("confusion", 1.5, 1120)],
}

# Pairs where the combination is the story, not either drug alone.
_FAERS_PAIR_MOCK: dict[frozenset[str], tuple[str, float]] = {
    frozenset({"furosemide", "lisinopril"}): ("dizziness / hypotension", 4.6),
    frozenset({"zolpidem", "oxybutynin"}): ("confusion / delirium", 6.2),
    frozenset({"warfarin", "sertraline"}): ("bleeding", 3.3),
}


def _outcome_card(sym, senior: Senior) -> EvidenceCard | None:
    """Real cohort rate if the warehouse has one, else the placeholder."""
    real = lookup.admission_rate(sym.label, senior.age)
    if real:
        return EvidenceCard(
            kind=EvidenceKind.NEISS,
            title=f"{sym.label.title()} in adults {real['age_band']}",
            detail=(
                f"In similar emergency visits, {real['rate_percent']:.0f}% ended in "
                f"admission or transfer rather than being sent home "
                f"({real['ci_low']:.0f}-{real['ci_high']:.0f}%, n={real['n']:,})."
            ),
            stat=Stat(
                value=real["rate_percent"], unit="percent", n=real["n"],
                ci_low=real["ci_low"], ci_high=real["ci_high"],
            ),
            source=real["source"],
            source_url="https://www.cdc.gov/nchs/ahcd/",
            weight=min(1.0, real["rate_percent"] / 60),
        )

    row = _NEISS_MOCK.get(sym.label)
    if not row:
        return None
    rate, n, (lo, hi) = row
    band = senior.age // 5 * 5
    return EvidenceCard(
        kind=EvidenceKind.NEISS,
        title=f"{sym.label.title()} in adults {band}+",
        detail=(
            f"In similar reported visits, {rate:.0f}% ended in admission or "
            f"transfer rather than being sent home."
        ),
        stat=Stat(value=rate, unit="percent", n=n, ci_low=lo, ci_high=hi),
        source=f"{MOCK} (NEISS-shaped, {band}+)",
        weight=min(1.0, rate / 60),
    )


def neiss_cards(checkin: CheckIn, senior: Senior) -> list[EvidenceCard]:
    cards: list[EvidenceCard] = []
    for sym in checkin.symptoms:
        card = _outcome_card(sym, senior)
        if card:
            cards.append(card)

        # A fall is the one thing NEISS itself answers, and the anticoagulant
        # split is the clinically interesting cut.
        if sym.label == "fall":
            on_thinner = any(
                (m.ingredient or m.name).lower() in BLOOD_THINNERS
                for m in senior.medications
            )
            fall = lookup.fall_admission_rate(on_thinner)
            if fall:
                cards.append(
                    EvidenceCard(
                        kind=EvidenceKind.NEISS,
                        title=(
                            "Falls in older adults on a blood thinner"
                            if on_thinner else "Falls in older adults"
                        ),
                        detail=(
                            f"{fall['rate_percent']:.0f}% were hospitalised rather "
                            f"than sent home ({fall['ci_low']:.0f}-"
                            f"{fall['ci_high']:.0f}%, n={fall['n']:,})."
                        ),
                        stat=Stat(
                            value=fall["rate_percent"], unit="percent", n=fall["n"],
                            ci_low=fall["ci_low"], ci_high=fall["ci_high"],
                        ),
                        source=fall["source"],
                        source_url="https://www.cpsc.gov/Research--Statistics/NEISS-Injury-Data",
                        weight=min(1.0, fall["rate_percent"] / 60),
                    )
                )
    return cards[:3]


def faers_cards(checkin: CheckIn, senior: Senior) -> list[EvidenceCard]:
    labels = {s.label for s in checkin.symptoms}
    ingredients = {
        (m.ingredient or m.name).lower() for m in senior.medications
    }
    cards: list[EvidenceCard] = []

    for ing in sorted(ingredients):
        # Real signal first. An interval that crosses 1 is not a signal, so we
        # do not show it -- a card that says "1.05x" teaches the clinician to
        # ignore the cards.
        for label in sorted(labels):
            real = lookup.drug_event_signal(ing, label)
            if real and real["significant"]:
                cards.append(
                    EvidenceCard(
                        kind=EvidenceKind.FAERS,
                        title=f"{ing.title()} is reported with {label}",
                        detail=(
                            f"Reported {real['ror']:.1f}x more often with this medicine "
                            f"than with others (95% CI {real['ci_low']:.1f}-"
                            f"{real['ci_high']:.1f}, {real['n']:,} reports). This is a "
                            f"reporting pattern, not proof of cause. Worth a "
                            f"medication review."
                        ),
                        stat=Stat(
                            value=real["ror"], unit="ratio", n=real["n"],
                            ci_low=real["ci_low"], ci_high=real["ci_high"],
                        ),
                        source=real["source"],
                        source_url="https://fis.fda.gov/extensions/FPD-QDE-FAERS/FPD-QDE-FAERS.html",
                        weight=min(1.0, (real["ror"] - 1) / 9),
                    )
                )

        if cards:
            continue

        for event, ror, n in _FAERS_MOCK.get(ing, []):
            if event in labels:
                cards.append(
                    EvidenceCard(
                        kind=EvidenceKind.FAERS,
                        title=f"{ing.title()} is associated with {event}",
                        detail=(
                            f"Reported {ror:.1f}x more often with this medicine than "
                            f"with others ({n:,} reports). A medication review may "
                            f"explain this symptom."
                        ),
                        stat=Stat(value=ror, unit="ratio", n=n),
                        source=f"{MOCK} (FAERS-shaped ROR)",
                        weight=min(1.0, (ror - 1) / 9),
                    )
                )

    # Real pair signals before the placeholders.
    for a in sorted(ingredients):
        for b in sorted(ingredients):
            if a >= b:
                continue
            for label in sorted(labels):
                real_pair = lookup.drug_pair_signal(a, b, label)
                if real_pair:
                    cards.append(
                        EvidenceCard(
                            kind=EvidenceKind.FAERS,
                            title=f"{a.title()} + {b.title()} together",
                            detail=(
                                f"{real_pair['n']:,} reports mention both medicines "
                                f"alongside {label}. Worth a pharmacist call."
                            ),
                            stat=Stat(value=float(real_pair["n"]), unit="count",
                                      n=real_pair["n"]),
                            source=real_pair["source"],
                            weight=0.5,
                        )
                    )

    if cards:
        return cards[:3]

    for pair, (event, ror) in _FAERS_PAIR_MOCK.items():
        if pair <= ingredients and any(e in labels for e in event.split(" / ")):
            a, b = sorted(pair)
            cards.append(
                EvidenceCard(
                    kind=EvidenceKind.FAERS,
                    title=f"{a.title()} + {b.title()} together",
                    detail=(
                        f"The combination is reported with {event} {ror:.1f}x more "
                        f"often than expected. Worth a pharmacist call."
                    ),
                    stat=Stat(value=ror, unit="ratio"),
                    source=f"{MOCK} (FAERS-shaped pair signal)",
                    weight=min(1.0, (ror - 1) / 9),
                )
            )
    return cards[:3]


def baseline_cards(baseline: BaselineSummary, checkin: CheckIn) -> list[EvidenceCard]:
    """Change against this senior's own history -- the part no dataset can give us."""
    cards: list[EvidenceCard] = []
    labels = {s.label for s in checkin.symptoms}

    for label in sorted(labels & set(baseline.trending_up)):
        cards.append(
            EvidenceCard(
                kind=EvidenceKind.BASELINE,
                title=f"{label.title()} is increasing",
                detail=(
                    f"Reported more often in the last week than the week before, "
                    f"across {baseline.checkin_count} check-ins."
                ),
                stat=Stat(
                    value=float(baseline.symptom_frequency.get(label, 0)),
                    unit="count",
                    n=baseline.checkin_count,
                ),
                source=f"This patient's own check-ins, last {baseline.window_days} days",
                weight=0.4,
            )
        )

    for sym in checkin.symptoms:
        if sym.is_new and sym.label not in baseline.symptom_frequency:
            cards.append(
                EvidenceCard(
                    kind=EvidenceKind.BASELINE,
                    title=f"{sym.label.title()} is new",
                    detail=(
                        f"Not reported at all in the last {baseline.window_days} days. "
                        f"New symptoms in older adults deserve a lower threshold."
                    ),
                    source=f"This patient's own check-ins, last {baseline.window_days} days",
                    weight=0.5,
                )
            )
    return cards[:3]
