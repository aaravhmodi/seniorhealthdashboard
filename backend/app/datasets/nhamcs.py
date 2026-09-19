"""NHAMCS: national ED visit survey. The base rates behind the triage suggestion.

Source: CDC National Hospital Ambulatory Medical Care Survey, ED public-use
files. Free, no credentialing, roughly 16k sampled ED visits a year from ~500
hospitals, 1992-2022. That last fact is a limitation to say out loud in the
pitch, along with this one: it is a *survey sample*, so every rate must be
computed with the survey weight (PATWT), and it describes the country, not your
hospital.

    https://www.cdc.gov/nchs/ahcd/datasets_documentation_related.htm

What we build: for adults 65+, the share of visits with a given reason-for-visit
that ended in admission or transfer rather than being sent home. That is the
number the dashboard shows as "in visits like this, X% were admitted", and it is
what turns a triage suggestion into an evidence-backed one.

Leakage discipline, because Voloridge's judges will look for it: only fields
known at the moment of triage go in. Diagnosis and disposition are outcomes,
never features. Reason-for-visit (RFV1) is recorded at check-in, so it stays.

**What the real files look like** (checked against ED 2021 and 2022):

  * The CDC publishes Stata/SAS/SPSS, not CSV. `load()` takes the `.dta`
    directly and converts it once to a slim CSV beside it.
  * `RFV1` is a 5-digit code (14150); `RFV13D` is the same code at the 4-digit
    level the classification is published at (1415, "Shortness of breath").
    Keying on `RFV1` with 4-digit codes matches nothing and fails silently.
  * There is no single disposition column. Admission, transfer, observation
    and death are separate 0/1 flags (ADMITHOS, TRANOTH, ...). `ADISP` exists
    but is *where an admitted-elsewhere patient went* and is -7 for most rows.

Usage:
    python -m app.datasets.cli nhamcs data/raw/nhamcs/ed*-stata.dta
"""
from __future__ import annotations

import logging
import pathlib
from typing import Iterable

from .warehouse import SENIOR_AGE_MIN, connect, wilson

log = logging.getLogger(__name__)

# NHAMCS column names drift between years and between the CSV conversions
# people publish. We resolve each field from a list of candidates rather than
# hardcoding one spelling, and fail loudly naming what was missing.
COLUMN_CANDIDATES: dict[str, tuple[str, ...]] = {
    "age": ("AGE", "age"),
    "sex": ("SEX", "sex"),
    # The 4-digit field first: it is what the classification (and our symptom
    # map) is written in. RFV1 is normalised to 4 digits below if it is all
    # a file has.
    "reason": ("RFV13D", "rfv13d", "RFV1", "rfv1", "PRIMARY_RFV"),
    "disposition": ("DISPO", "dispo", "ADISP", "adisp"),
    "weight": ("PATWT", "patwt", "PATWTF"),
    "year": ("VYEAR", "vyear", "YEAR", "year"),
    "arrival": ("ARREMS", "arrems", "ARRIVE"),
    "immediacy": ("IMMEDR", "immedr"),
}

# The public-use files record the outcome as one 0/1 flag per disposition.
# Any of these means "not sent home". OBSDIS (observed, then discharged) is
# deliberately not one of them.
ADMIT_FLAGS = ("ADMITHOS", "TRANOTH", "TRANPSYC", "OBSHOS", "DIEDED")

# Fallback for CSVs that carry one coded disposition column instead of flags.
ADMIT_CODES = (4, 5, 6, 7, 8, 9)
ADMIT_LABELS = ("admit", "transfer", "observation", "died")

# Kept when converting a .dta: the rate inputs, plus the triage-time fields the
# predictive model will want. Everything else in the 900-column file is dropped.
KEEP_COLUMNS = (
    "AGE", "SEX", "YEAR", "VYEAR", "PATWT", "RFV1", "RFV13D", "RFV2", "RFV3",
    "ARREMS", "IMMEDR", "TEMPF", "PULSE", "RESPR", "BPSYS", "BPDIAS", "POPCT",
    "PAINSCALE", "INJURY", "ADISP", "OBSDIS", *ADMIT_FLAGS,
)


# Only these are needed to build the rates. `sex`, `arrival` and `immediacy`
# are useful for the model work later, so they are resolved when present and
# ignored when the year's file spells them differently.
REQUIRED_FIELDS = ("age", "reason", "weight")


def _columns(con, relation: str) -> dict[str, str]:
    """Upper-cased name -> actual name."""
    return {
        row[0].upper(): row[0]
        for row in con.execute(f"DESCRIBE SELECT * FROM {relation} LIMIT 1").fetchall()
    }


def _resolve(con, relation: str, wanted: Iterable[str]) -> dict[str, str]:
    upper = _columns(con, relation)
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for field in wanted:
        for candidate in COLUMN_CANDIDATES[field]:
            if candidate.upper() in upper:
                resolved[field] = upper[candidate.upper()]
                break
        else:
            if field in REQUIRED_FIELDS:
                missing.append(field)
    if missing:
        raise ValueError(
            f"NHAMCS file is missing {missing}. Columns present: "
            f"{sorted(upper.values())[:25]}... Check you downloaded the ED file, "
            f"not the outpatient one."
        )
    return resolved


def stata_to_csv(path: str) -> str:
    """Convert one CDC `.dta` to a slim CSV beside it, once.

    Numeric codes are kept (`convert_categoricals=False`): the value labels are
    for humans, and the codes are what the symptom map is written in.
    """
    src = pathlib.Path(path)
    out = src.with_suffix(".slim.csv")
    if out.is_file() and out.stat().st_mtime >= src.stat().st_mtime:
        return str(out)
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "Reading the CDC .dta files needs pandas: "
            "`pip install -r requirements-data.txt`"
        ) from exc

    with pd.read_stata(src, iterator=True) as reader:
        in_file = set(reader.variable_labels())
    present = [c for c in KEEP_COLUMNS if c in in_file]
    frame = pd.read_stata(src, convert_categoricals=False, columns=present)
    frame.to_csv(out, index=False)
    log.info("converted %s -> %s (%s rows, %s columns)",
             src.name, out.name, len(frame), len(frame.columns))
    return str(out)


def _admitted_sql(con, cols: dict[str, str]) -> str:
    """Flags when the file has them, a coded column when it does not."""
    upper = _columns(con, "nhamcs_raw")
    flags = [upper[f] for f in ADMIT_FLAGS if f in upper]
    if flags:
        any_flag = " OR ".join(f"TRY_CAST({f} AS INTEGER) = 1" for f in flags)
        return f"CASE WHEN {any_flag} THEN 1 ELSE 0 END"

    if "disposition" not in cols:
        raise ValueError(
            f"NHAMCS file has no disposition: expected flags {ADMIT_FLAGS} "
            f"or one of {COLUMN_CANDIDATES['disposition']}"
        )
    disposition = cols["disposition"]
    return (
        f"CASE WHEN TRY_CAST({disposition} AS INTEGER) IN {ADMIT_CODES} THEN 1 "
        + " ".join(
            f"WHEN lower(CAST({disposition} AS VARCHAR)) LIKE '%{label}%' THEN 1"
            for label in ADMIT_LABELS
        )
        + " ELSE 0 END"
    )


def _reason_sql(column: str) -> str:
    """Normalise to the 4-digit classification code, as text.

    RFV1 is 5 digits (the last is a modifier); RFV13D is already 4. Negative
    values are NHAMCS missing-data codes and stay as they are, so they simply
    match no symptom.
    """
    n = f"TRY_CAST({column} AS INTEGER)"
    return (
        f"CASE WHEN {n} >= 10000 THEN CAST({n} // 10 AS VARCHAR) "
        f"WHEN {n} IS NOT NULL THEN CAST({n} AS VARCHAR) "
        f"ELSE CAST({column} AS VARCHAR) END"
    )


def load(paths: list[str], min_n: int = 30) -> int:
    """Build `nhamcs_senior_rates` from one or more year files (.csv or .dta).

    `min_n` drops cells too thin to quote. A rate computed from four sampled
    visits is not a base rate, it is noise with a percent sign.
    """
    paths = [stata_to_csv(p) if p.lower().endswith(".dta") else p for p in paths]
    con = connect()
    try:
        files = "[" + ", ".join(f"'{p}'" for p in paths) + "]"
        con.execute(
            f"CREATE OR REPLACE VIEW nhamcs_raw AS "
            f"SELECT * FROM read_csv_auto({files}, union_by_name=true, "
            f"ignore_errors=true, sample_size=-1)"
        )
        cols = _resolve(con, "nhamcs_raw", [*REQUIRED_FIELDS, "disposition"])
        admitted = _admitted_sql(con, cols)

        # Aggregate by *symptom*, not by code. A symptom spans several codes
        # (urinary is five), each too thin to quote alone; filtering per code
        # and summing afterwards threw the whole symptom away and left an
        # interval stitched from min/max of its parts. Here min_n and the
        # Wilson interval apply to exactly the cell the card quotes.
        mapping = ", ".join(
            f"('{symptom}', '{code}')"
            for symptom, codes in SYMPTOM_TO_RFV.items()
            for code in codes
        )
        con.execute(
            f"CREATE OR REPLACE TEMP TABLE rfv_map AS "
            f"SELECT * FROM (VALUES {mapping}) AS m(symptom, reason_code)"
        )

        con.execute(
            f"""
            CREATE OR REPLACE TABLE nhamcs_senior_rates AS
            WITH senior_visits AS (
                SELECT
                    {_reason_sql(cols['reason'])}                AS reason_code,
                    TRY_CAST({cols['age']} AS INTEGER)           AS age,
                    COALESCE(TRY_CAST({cols['weight']} AS DOUBLE), 1.0) AS patwt,
                    {admitted}                                   AS admitted
                FROM nhamcs_raw
                WHERE TRY_CAST({cols['age']} AS INTEGER) >= {SENIOR_AGE_MIN}
            ),
            banded AS (
                SELECT
                    m.symptom,
                    CASE WHEN v.age >= 85 THEN '85+'
                         WHEN v.age >= 75 THEN '75-84'
                         ELSE '65-74' END                        AS age_band,
                    v.patwt,
                    v.admitted
                FROM senior_visits v
                JOIN rfv_map m USING (reason_code)
            ),
            agg AS (
                -- One row per band, plus an all-65+ row per symptom that the
                -- lookup falls back to when a band is too thin to quote.
                SELECT
                    symptom,
                    coalesce(age_band, '65+')                    AS age_band,
                    count(*)                                     AS n,
                    sum(patwt)                                   AS weighted_n,
                    -- The survey weight is what makes this a national rate
                    -- rather than a rate among the hospitals that were sampled.
                    sum(patwt * admitted) / nullif(sum(patwt), 0) AS rate
                FROM banded
                GROUP BY GROUPING SETS ((symptom, age_band), (symptom))
            )
            SELECT
                symptom,
                age_band,
                n,
                weighted_n,
                rate,
                {wilson("rate", "n")}
            FROM agg
            WHERE n >= {min_n}
            """
        )
        rows = con.execute("SELECT count(*) FROM nhamcs_senior_rates").fetchone()[0]
        log.info("nhamcs_senior_rates: %s cells", rows)
        return rows
    finally:
        con.close()
        # Anything already running is holding cached Nones from before
        # this load. Without this the app keeps serving mock cards until
        # it is restarted, and nothing looks broken.
        from .lookup import clear_cache

        clear_cache()


# --------------------------------------------------------------------------
# Symptom label -> NHAMCS reason-for-visit code
# --------------------------------------------------------------------------
# NCHS "Reason for Visit Classification", 4-digit level (the RFV13D field).
# Every code below was checked against the value labels shipped inside the
# ED 2021 and 2022 Stata files, not copied from memory -- the first version of
# this map had "fall" pointing at 5820, which is *suicide attempt*.
#
# "fall" is deliberately absent: the classification has no fall code (falls
# arrive as the injury, e.g. 5505 head injury), so the fall rate comes from
# NEISS, which is built for exactly that question.
SYMPTOM_TO_RFV: dict[str, tuple[str, ...]] = {
    "chest pain": ("1050",),                        # Chest pain and related symptoms
    "shortness of breath": ("1415", "1420"),        # Shortness of breath; labored breathing
    "dizziness": ("1225",),                         # Vertigo - dizziness
    "confusion": ("5842", "1215"),                  # Altered consciousness; memory disturbance
    "weakness one side": ("1230",),                 # Weakness (neurologic)
    "fever": ("1010",),                             # Fever
    "nausea": ("1525", "1530"),                     # Nausea; vomiting
    "poor appetite": ("1570",),                     # Appetite, abnormal
    "swelling legs": ("1035",),                     # Symptoms of fluid abnormalities (oedema)
    "urinary symptoms": ("1640", "1645", "1650", "1660", "1675"),
    "trouble sleeping": ("1135",),                  # Disturbances of sleep
    "back pain": ("1905", "1910"),                  # Back symptoms; low back symptoms
    "headache": ("1210",),                          # Headache, pain in head
    "bleeding": ("1070", "1580"),                   # Bleeding, site unspecified; GI bleeding
}


def age_band(age: int) -> str:
    if age >= 85:
        return "85+"
    if age >= 75:
        return "75-84"
    return "65-74"
