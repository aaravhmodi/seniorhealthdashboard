"""Read side of the warehouse.

Every function returns None when the data is not loaded. That is the contract
the rest of the app is built on: `evidence.py` asks for a real number first and
falls back to its MOCK card when it gets None, so the system behaves identically
whether or not anyone has run the loaders yet.

Results are cached in process. The tables are static once built; re-reading
DuckDB on every check-in would add latency to the one path that has to feel
instant.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from . import nhamcs
from .warehouse import connect, exists, table_exists, wilson

log = logging.getLogger(__name__)


def available() -> dict[str, bool]:
    if not exists():
        return {name: False for name in
                ("nhamcs_senior_rates", "neiss_senior_rates", "neiss_narratives",
                 "faers_signals", "faers_pair_signals", "faers_ingredients")}
    try:
        con = connect(read_only=True)
    except Exception:
        return {}
    try:
        return {
            name: table_exists(con, name)
            for name in ("nhamcs_senior_rates", "neiss_senior_rates",
                         "neiss_narratives", "faers_signals", "faers_pair_signals",
                         "faers_ingredients")
        }
    finally:
        con.close()


def _query(sql: str, params: list[Any]) -> list[tuple]:
    if not exists():
        return []
    try:
        con = connect(read_only=True)
    except Exception as exc:
        log.warning("warehouse unavailable: %s", exc)
        return []
    try:
        return con.execute(sql, params).fetchall()
    except Exception as exc:
        # A missing table is normal before the loaders have run.
        log.debug("lookup failed (%s); falling back to mock", exc)
        return []
    finally:
        con.close()


@lru_cache(maxsize=512)
def admission_rate(symptom_label: str, age: int) -> dict | None:
    """NHAMCS: share of similar ED visits that ended in admission or transfer.

    One row per symptom and age band. The loader already did the aggregation
    across the symptom's reason-for-visit codes, survey-weighted, with a single
    interval -- so this is a lookup, not an average of averages.
    """
    if symptom_label not in nhamcs.SYMPTOM_TO_RFV:
        return None
    band = nhamcs.age_band(age)
    rows = _query(
        """
        SELECT n, rate, ci_low, ci_high, year_min, year_max
        FROM nhamcs_senior_rates
        WHERE symptom = ? AND age_band = ?
        """,
        [symptom_label, band],
    )
    if not rows or not rows[0][0]:
        return None
    n, rate, ci_low, ci_high, year_min, year_max = rows[0]
    years = (
        f"{year_min}" if year_min == year_max else f"{year_min}-{year_max}"
    ) if year_min else "public-use files"
    return {
        "n": int(n),
        "rate_percent": round(float(rate) * 100, 1),
        "ci_low": round(float(ci_low) * 100, 1),
        "ci_high": round(float(ci_high) * 100, 1),
        "age_band": band,
        "years": years,
        "source": (
            f"NHAMCS ED {years}, ages {band}, survey-weighted to national estimates"
        ),
    }


# What it takes for us to put a medication card in front of a clinician.
# Both conditions, deliberately: the interval must exclude 1 (the association
# is not noise) AND the shrunk estimate must stay meaningfully above 1 (it is
# not a thin cell flattered by a wide interval).
MIN_SHRUNK_RRR = 1.5


@lru_cache(maxsize=512)
def drug_event_signal(ingredient: str, event: str) -> dict | None:
    """FAERS: is this event reported disproportionately with this ingredient?

    Primary-suspect drugs only, ages 65+, de-duplicated by CASEID.
    """
    rows = _query(
        """
        SELECT n, ror, prr, eb_rrr, expected, ci_low, ci_high
        FROM faers_signals
        WHERE ingredient = ? AND event = ?
        """,
        [ingredient.lower(), event.lower()],
    )
    if not rows:
        return None
    n, ror, prr, eb_rrr, expected, ci_low, ci_high = rows[0]
    if ror is None or eb_rrr is None:
        return None
    return {
        "n": int(n),
        "ror": round(float(ror), 2),
        "prr": round(float(prr), 2) if prr else None,
        # The shrunk estimate is the one to quote: a Gamma-Poisson posterior
        # mean that pulls small cells back toward "no signal".
        "eb_rrr": round(float(eb_rrr), 2),
        "expected": round(float(expected), 1) if expected else None,
        "ci_low": round(float(ci_low), 2) if ci_low else None,
        "ci_high": round(float(ci_high), 2) if ci_high else None,
        "significant": bool(
            ci_low and float(ci_low) > 1.0 and float(eb_rrr) >= MIN_SHRUNK_RRR
        ),
        "source": "FAERS primary-suspect reports, ages 65+, de-duplicated by CASEID",
    }


@lru_cache(maxsize=1)
def known_ingredients() -> tuple[str, ...]:
    """The ingredient dictionary, built from the data.

    Used to check whether a spoken or photographed medicine is something we
    can actually say anything about.
    """
    return tuple(row[0] for row in _query(
        "SELECT ingredient FROM faers_ingredients ORDER BY senior_reports DESC", []
    ))


@lru_cache(maxsize=512)
def resolve_ingredient(spoken: str) -> str | None:
    """Map what a patient says to the ingredient FAERS is keyed on.

    PROD_AI carries the salt: "WARFARIN SODIUM", "AMLODIPINE BESYLATE",
    "LEVOTHYROXINE SODIUM". A patient says "warfarin", and their med list says
    "Warfarin". Without this, every real lookup would miss and the medication
    cards would silently never appear -- the failure mode being that nothing
    looks broken.

    Resolution order: the brand dictionary, then an exact match, then the most
    reported ingredient that starts with what they said.
    """
    if not spoken:
        return None
    name = spoken.strip().lower()

    from .faers import INGREDIENT_ALIASES

    name = INGREDIENT_ALIASES.get(name, name)

    known = known_ingredients()
    if not known:
        return name  # nothing loaded; hand it back for the mock path
    if name in known:
        return name

    # known_ingredients() is ordered by report count, so the first prefix match
    # is the salt form that actually dominates the data.
    for candidate in known:
        if candidate.startswith(name + " ") or candidate == name:
            return candidate
    for candidate in known:
        if name in candidate.split():
            return candidate
    return None


# What it takes to call a pair an interaction rather than a coincidence. The
# shrunk ratio has to clear this AFTER the noisy-OR baseline has already
# accounted for both drugs' own reporting rates for the event -- so 1.5 here is
# "half again as often as the two drugs together would explain", not "half
# again as often as nothing".
MIN_PAIR_RATIO = 1.5


@lru_cache(maxsize=512)
def drug_pair_signal(ingredient_a: str, ingredient_b: str, event: str) -> dict | None:
    """FAERS: is this event reported with the PAIR more than each drug predicts?

    Distinct from `drug_event_signal`, which asks about one drug against the
    rest of the database. This asks whether two medicines together are reported
    with the event more often than their individual rates would produce -- the
    question a pharmacist is actually being asked to look at.
    """
    a, b = sorted([ingredient_a.lower(), ingredient_b.lower()])
    rows = _query(
        """
        SELECT n, pair_reports, expected, eb_ratio, ci_low, ci_high
        FROM faers_pair_signals
        WHERE ingredient_a = ? AND ingredient_b = ? AND event = ?
        """,
        [a, b, event.lower()],
    )
    if not rows:
        return None
    n, pair_reports, expected, eb_ratio, ci_low, ci_high = rows[0]
    if eb_ratio is None:
        return None
    return {
        "n": int(n),
        "pair_reports": int(pair_reports),
        "expected": round(float(expected), 1),
        "eb_ratio": round(float(eb_ratio), 2),
        "ci_low": round(float(ci_low), 2),
        "ci_high": round(float(ci_high), 2),
        # The LOWER bound has to clear the bar, not the point estimate: a pair
        # seen fifty times with a wide interval has not earned a card.
        "significant": bool(float(ci_low) >= MIN_PAIR_RATIO),
        "pair": [a, b],
        "source": (
            "FAERS reports listing both medicines, ages 65+, de-duplicated by "
            "CASEID; compared against each drug's own reporting rate"
        ),
    }


def narrative_rows(limit: int = 5000) -> list[dict]:
    """The retrieval corpus: senior injury cases with their outcome.

    Not cached -- it is read once at index build, and holding thousands of rows
    in an lru_cache for the life of the process is pure waste.
    """
    rows = _query(
        f"""
        SELECT case_id, narrative, mechanism, head_strike,
               loss_of_consciousness, on_anticoagulant, age_band, admitted
        FROM neiss_narratives
        LIMIT {int(limit)}
        """,
        [],
    )
    return [
        {
            "case_id": r[0],
            "narrative": r[1],
            "mechanism": r[2],
            "head_strike": bool(r[3]),
            "loss_of_consciousness": bool(r[4]),
            "on_anticoagulant": bool(r[5]),
            "age_band": r[6],
            "admitted": bool(r[7]),
        }
        for r in rows
    ]


def cohort_rows(limit: int = 2000) -> list[dict]:
    """Citable warehouse statistics for the retrieval index.

    Only FAERS cells that pass the same significance and shrinkage guard used
    by evidence cards are exposed. Every returned row includes its sample size.
    """
    rows: list[dict] = []
    faers = _query(
        f"""
        SELECT ingredient, event, n, eb_rrr, ci_low, ci_high
        FROM faers_signals
        WHERE ci_low > 1 AND eb_rrr >= {MIN_SHRUNK_RRR}
        ORDER BY n DESC
        LIMIT {int(limit)}
        """,
        [],
    )
    for ingredient, event, n, estimate, ci_low, ci_high in faers:
        rows.append(
            {
                "id": f"faers_{ingredient}_{event}",
                "text": (
                    f"Among FAERS reports for adults 65 and older, {event} was "
                    f"reported more often with primary-suspect {ingredient}: "
                    f"shrunk reporting ratio {float(estimate):.2f} "
                    f"(95% interval {float(ci_low):.2f}-{float(ci_high):.2f}; "
                    f"{int(n)} reports). This is an association, not proof of cause."
                ),
                "source": "FDA FAERS, primary-suspect reports, ages 65+",
                "n": int(n),
                "kind": "faers",
            }
        )

    remaining = max(0, int(limit) - len(rows))
    if remaining:
        neiss = _query(
            f"""
            SELECT mechanism, head_strike, on_anticoagulant, age_band,
                   n, rate, ci_low, ci_high
            FROM neiss_senior_rates
            ORDER BY n DESC
            LIMIT {remaining}
            """,
            [],
        )
        for mechanism, head, anticoag, age_band, n, rate, lo, hi in neiss:
            rows.append(
                {
                    "id": f"neiss_{mechanism}_{head}_{anticoag}_{age_band}",
                    "text": (
                        f"In CPSC NEISS injury cases for ages {age_band}, "
                        f"{float(rate) * 100:.1f}% were admitted or observed "
                        f"(95% interval {float(lo) * 100:.1f}-{float(hi) * 100:.1f}%; "
                        f"{int(n)} sampled cases). Mechanism: {mechanism}; "
                        f"head strike: {bool(head)}; blood thinner mentioned: "
                        f"{bool(anticoag)}."
                    ),
                    "source": "CPSC NEISS injury surveillance, ages 65+",
                    "n": int(n),
                    "kind": "neiss",
                }
            )
    return rows


@lru_cache(maxsize=512)
def fall_outcome(
    mechanism: str | None = None,
    head_strike: bool | None = None,
    on_anticoagulant: bool | None = None,
    age_band: str | None = None,
) -> dict | None:
    """Hospitalisation rate for the cut that matches this patient.

    Every argument is optional, so the caller can ask the most specific
    question the narrative supports and let the rest aggregate away.
    """
    clauses, params = [], []
    for column, value in (
        ("mechanism", mechanism),
        ("head_strike", head_strike),
        ("on_anticoagulant", on_anticoagulant),
        ("age_band", age_band),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    rows = _query(
        f"""
        WITH agg AS (
            SELECT count(*) AS n,
                   sum(wt * admitted) / nullif(sum(wt), 0) AS rate
            FROM neiss_senior_cases
            {where}
        )
        SELECT n, rate, {wilson('rate', 'n')}
        FROM agg
        WHERE n >= 30
        """,
        params,
    )
    if not rows or not rows[0][0]:
        return None
    n, rate, ci_low, ci_high = rows[0]
    return {
        "n": int(n),
        "rate_percent": round(float(rate) * 100, 1),
        "ci_low": round(float(ci_low) * 100, 1),
        "ci_high": round(float(ci_high) * 100, 1),
        "mechanism": mechanism,
        "head_strike": head_strike,
        "on_anticoagulant": on_anticoagulant,
        "age_band": age_band,
        "source": "NEISS injury surveillance, ages 65+",
    }


@lru_cache(maxsize=256)
def fall_admission_rate(on_anticoagulant: bool, body_part: str = "head") -> dict | None:
    """NEISS: hospitalisation rate after a fall, split by anticoagulant mention."""
    result = fall_outcome(on_anticoagulant=on_anticoagulant)
    if not result:
        return None
    return {**result, "on_anticoagulant": on_anticoagulant}


def clear_cache() -> None:
    """Call after running a loader, or the process keeps serving the old None."""
    for fn in (admission_rate, drug_event_signal, drug_pair_signal,
               fall_admission_rate, fall_outcome, known_ingredients,
               resolve_ingredient):
        fn.cache_clear()
