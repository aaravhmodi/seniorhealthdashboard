"""NEISS: emergency-department injury surveillance. The fall story.

Source: CPSC National Electronic Injury Surveillance System. Free, no
credentialing, and it carries a short clinical narrative per case, which is
unusual and useful.

    https://www.cpsc.gov/Research--Statistics/NEISS-Injury-Data

NEISS covers *injuries*, so it is the right source for exactly one of our
pathways: the fall. For chest pain or confusion it has nothing to say, and we
use NHAMCS instead. Being explicit about which dataset answers which question
is worth a slide -- it is the difference between a data project and a dataset
name-drop.

What we build: for adults 65+, the share of injury visits that ended in
hospitalisation, by body part and diagnosis, plus the same split by whether the
narrative mentions an anticoagulant. That second cut is the one that matters
clinically and it is available because the narratives exist.

Usage:
    python -m app.datasets.cli neiss data/raw/neiss/neiss2019.csv data/raw/neiss/neiss2023.csv
"""
from __future__ import annotations

import logging

from .warehouse import SENIOR_AGE_MIN, connect, wilson

log = logging.getLogger(__name__)

# NEISS ages are coded: 0-114 are years, 200+ encodes months of age for infants.
# Irrelevant for 65+, but the filter has to be written knowing it.
AGE_YEARS_MAX = 120

# Disposition codes where the patient was not sent home.
ADMITTED_CODES = (4, 5)  # treated and admitted; held for observation

BODY_PART_LABELS = {
    75: "head", 76: "face", 77: "eyeball", 79: "lower trunk", 80: "upper trunk",
    82: "elbow", 83: "lower arm", 85: "wrist", 88: "shoulder", 89: "upper arm",
    92: "face", 94: "hand", 31: "face", 32: "eyeball", 33: "head", 34: "neck",
    35: "upper trunk", 36: "lower trunk", 37: "upper arm", 38: "lower arm",
    81: "hip", 90: "ankle", 93: "knee", 95: "lower leg", 36_1: "pubic region",
}

ANTICOAGULANT_TERMS = (
    "coumadin", "warfarin", "eliquis", "apixaban", "xarelto", "rivaroxaban",
    "plavix", "clopidogrel", "pradaxa", "dabigatran", "blood thinner",
)


def load(paths: list[str], min_n: int = 30) -> int:
    con = connect()
    try:
        files = "[" + ", ".join(f"'{p}'" for p in paths) + "]"
        con.execute(
            f"CREATE OR REPLACE VIEW neiss_raw AS "
            f"SELECT * FROM read_csv_auto({files}, union_by_name=true, "
            f"ignore_errors=true, sample_size=-1)"
        )

        columns = {
            row[0].lower(): row[0]
            for row in con.execute("DESCRIBE SELECT * FROM neiss_raw LIMIT 1").fetchall()
        }

        def col(*candidates: str) -> str:
            for candidate in candidates:
                if candidate.lower() in columns:
                    return columns[candidate.lower()]
            raise ValueError(
                f"NEISS file is missing any of {candidates}. "
                f"Present: {sorted(columns)[:25]}"
            )

        age = col("age", "AGE")
        body_part = col("body_part", "bdypt", "Body_Part")
        diagnosis = col("diag", "diagnosis", "Diagnosis")
        disposition = col("disposition", "disp", "Disposition")
        narrative = col("narrative", "narr1", "Narrative_1", "narrative1")
        weight = col("weight", "wt", "Weight")

        thinner_match = " OR ".join(
            f"lower(CAST({narrative} AS VARCHAR)) LIKE '%{term}%'"
            for term in ANTICOAGULANT_TERMS
        )

        con.execute(
            f"""
            CREATE OR REPLACE TABLE neiss_senior_rates AS
            WITH senior_injuries AS (
                SELECT
                    CAST({body_part} AS VARCHAR)  AS body_part_code,
                    CAST({diagnosis} AS VARCHAR)  AS diagnosis_code,
                    CASE WHEN {thinner_match} THEN true ELSE false END AS on_anticoagulant,
                    COALESCE(TRY_CAST({weight} AS DOUBLE), 1.0) AS wt,
                    CASE WHEN TRY_CAST({disposition} AS INTEGER) IN {ADMITTED_CODES}
                         THEN 1 ELSE 0 END AS admitted
                FROM neiss_raw
                WHERE TRY_CAST({age} AS DOUBLE) >= {SENIOR_AGE_MIN}
                  AND TRY_CAST({age} AS DOUBLE) <= {AGE_YEARS_MAX}
            ),
            agg AS (
                SELECT
                    body_part_code,
                    diagnosis_code,
                    on_anticoagulant,
                    count(*)   AS n,
                    sum(wt)    AS weighted_n,
                    sum(wt * admitted) / nullif(sum(wt), 0) AS rate
                FROM senior_injuries
                GROUP BY 1, 2, 3
            )
            SELECT
                body_part_code,
                diagnosis_code,
                on_anticoagulant,
                n,
                weighted_n,
                rate,
                {wilson("rate", "n")}
            FROM agg
            WHERE n >= {min_n}
            """
        )
        rows = con.execute("SELECT count(*) FROM neiss_senior_rates").fetchone()[0]
        log.info("neiss_senior_rates: %s cells", rows)
        return rows
    finally:
        con.close()
        # Anything already running is holding cached Nones from before
        # this load. Without this the app keeps serving mock cards until
        # it is restarted, and nothing looks broken.
        from .lookup import clear_cache

        clear_cache()
