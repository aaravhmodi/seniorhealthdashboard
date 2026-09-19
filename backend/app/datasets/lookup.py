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
from .warehouse import connect, exists, table_exists

log = logging.getLogger(__name__)


def available() -> dict[str, bool]:
    if not exists():
        return {name: False for name in
                ("nhamcs_senior_rates", "neiss_senior_rates", "faers_signals",
                 "faers_pair_signals", "faers_ingredients")}
    try:
        con = connect(read_only=True)
    except Exception:
        return {}
    try:
        return {
            name: table_exists(con, name)
            for name in ("nhamcs_senior_rates", "neiss_senior_rates",
                         "faers_signals", "faers_pair_signals", "faers_ingredients")
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
    """NHAMCS: share of similar ED visits that ended in admission or transfer."""
    codes = nhamcs.SYMPTOM_TO_RFV.get(symptom_label)
    if not codes:
        return None
    band = nhamcs.age_band(age)
    placeholders = ", ".join("?" for _ in codes)
    rows = _query(
        f"""
        SELECT sum(n)                                   AS n,
               sum(rate * n) / nullif(sum(n), 0)        AS rate,
               min(ci_low)                              AS ci_low,
               max(ci_high)                             AS ci_high
        FROM nhamcs_senior_rates
        WHERE reason_code IN ({placeholders}) AND age_band = ?
        """,
        [*codes, band],
    )
    if not rows or not rows[0][0]:
        return None
    n, rate, ci_low, ci_high = rows[0]
    return {
        "n": int(n),
        "rate_percent": round(float(rate) * 100, 1),
        "ci_low": round(float(ci_low) * 100, 1),
        "ci_high": round(float(ci_high) * 100, 1),
        "age_band": band,
        "source": f"NHAMCS ED public-use files, ages {band}, survey-weighted",
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


@lru_cache(maxsize=512)
def drug_pair_signal(ingredient_a: str, ingredient_b: str, event: str) -> dict | None:
    a, b = sorted([ingredient_a.lower(), ingredient_b.lower()])
    rows = _query(
        """
        SELECT n FROM faers_pair_signals
        WHERE ingredient_a = ? AND ingredient_b = ? AND event = ?
        """,
        [a, b, event.lower()],
    )
    if not rows:
        return None
    return {
        "n": int(rows[0][0]),
        "pair": [a, b],
        "source": "FAERS co-reported pair, ages 65+",
    }


@lru_cache(maxsize=256)
def fall_admission_rate(on_anticoagulant: bool, body_part: str = "head") -> dict | None:
    """NEISS: hospitalisation rate after a fall, split by anticoagulant mention."""
    rows = _query(
        """
        SELECT sum(n)                            AS n,
               sum(rate * n) / nullif(sum(n), 0) AS rate,
               min(ci_low), max(ci_high)
        FROM neiss_senior_rates
        WHERE on_anticoagulant = ?
        """,
        [on_anticoagulant],
    )
    if not rows or not rows[0][0]:
        return None
    n, rate, ci_low, ci_high = rows[0]
    return {
        "n": int(n),
        "rate_percent": round(float(rate) * 100, 1),
        "ci_low": round(float(ci_low) * 100, 1),
        "ci_high": round(float(ci_high) * 100, 1),
        "on_anticoagulant": on_anticoagulant,
        "source": "NEISS injury surveillance, ages 65+",
    }


def clear_cache() -> None:
    """Call after running a loader, or the process keeps serving the old None."""
    for fn in (admission_rate, drug_event_signal, drug_pair_signal,
               fall_admission_rate, known_ingredients, resolve_ingredient):
        fn.cache_clear()
