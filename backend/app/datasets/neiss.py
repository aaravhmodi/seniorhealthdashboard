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

What we build, for adults 65+:

    neiss_senior_rates   hospitalisation rate by body part, diagnosis,
                         mechanism, head strike and anticoagulant mention
    neiss_narratives     one row per case, with the narrative and its flags,
                         ready to be embedded for similar-case retrieval

The cuts beyond body part exist because the narrative exists. "Fell down the
stairs, struck head, on Coumadin" and "slipped in the kitchen" are the same
body part and a completely different night, and only the free text knows.

**The 2019 schema break:** before 2019 the text lives in two short fields
(NARR1, NARR2) that have to be concatenated; from 2019 it is a single 400-char
field. The loader handles both and records which, because comparing across the
break without noticing is a silent bug -- narratives simply get shorter as you
go back, and every text-derived flag quietly drops with them.

Usage:
    python -m app.datasets.cli neiss data/raw/neiss/neiss2019.csv data/raw/neiss/neiss2023.csv
"""
from __future__ import annotations

import logging

from .narratives import sql_flag, sql_mechanism
from .narratives import ANTICOAGULANTS, HEAD_STRIKE, LOSS_OF_CONSCIOUSNESS
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

# How many narratives to keep for retrieval. The whole file is millions of
# rows; the index only needs enough senior falls to find a close match, and
# embedding more costs money and start-up time for no extra recall.
NARRATIVE_LIMIT = 5000


def load(
    paths: list[str], min_n: int = 30, narrative_limit: int = NARRATIVE_LIMIT
) -> int:
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
        weight = col("weight", "wt", "Weight")

        # The 2019 break: two short fields before, one long field after.
        def maybe(*candidates: str) -> str | None:
            for candidate in candidates:
                if candidate.lower() in columns:
                    return columns[candidate.lower()]
            return None

        narr1 = maybe("narrative", "narr1", "narrative1", "Narrative_1")
        narr2 = maybe("narr2", "narrative2", "Narrative_2")
        if narr1 is None:
            raise ValueError(
                f"NEISS file has no narrative column. Present: {sorted(columns)[:25]}"
            )
        narrative = (
            f"trim(concat(coalesce(CAST({narr1} AS VARCHAR), ''), ' ', "
            f"coalesce(CAST({narr2} AS VARCHAR), '')))"
            if narr2 else f"CAST({narr1} AS VARCHAR)"
        )
        log.info("narrative source: %s", "narr1+narr2 (pre-2019)" if narr2 else "single field")

        thinner_flag = sql_flag(narrative, ANTICOAGULANTS)
        head_flag = sql_flag(narrative, HEAD_STRIKE)
        loc_flag = sql_flag(narrative, LOSS_OF_CONSCIOUSNESS)
        mechanism = sql_mechanism(narrative)

        con.execute(
            f"""
            CREATE OR REPLACE TABLE neiss_senior_cases AS
            SELECT
                CAST({body_part} AS VARCHAR)  AS body_part_code,
                CAST({diagnosis} AS VARCHAR)  AS diagnosis_code,
                {narrative}                   AS narrative,
                {thinner_flag}                AS on_anticoagulant,
                {head_flag}                   AS head_strike,
                {loc_flag}                    AS loss_of_consciousness,
                {mechanism}                   AS mechanism,
                TRY_CAST({age} AS DOUBLE)     AS age,
                CASE WHEN TRY_CAST({age} AS DOUBLE) >= 85 THEN '85+'
                     WHEN TRY_CAST({age} AS DOUBLE) >= 75 THEN '75-84'
                     ELSE '65-74' END         AS age_band,
                COALESCE(TRY_CAST({weight} AS DOUBLE), 1.0) AS wt,
                CASE WHEN TRY_CAST({disposition} AS INTEGER) IN {ADMITTED_CODES}
                     THEN 1 ELSE 0 END        AS admitted
            FROM neiss_raw
            WHERE TRY_CAST({age} AS DOUBLE) >= {SENIOR_AGE_MIN}
              AND TRY_CAST({age} AS DOUBLE) <= {AGE_YEARS_MAX}
            """
        )

        con.execute(
            f"""
            CREATE OR REPLACE TABLE neiss_senior_rates AS
            WITH agg AS (
                SELECT
                    body_part_code,
                    diagnosis_code,
                    mechanism,
                    head_strike,
                    on_anticoagulant,
                    age_band,
                    count(*)   AS n,
                    sum(wt)    AS weighted_n,
                    sum(wt * admitted) / nullif(sum(wt), 0) AS rate
                FROM neiss_senior_cases
                GROUP BY 1, 2, 3, 4, 5, 6
            )
            SELECT
                body_part_code, diagnosis_code, mechanism, head_strike,
                on_anticoagulant, age_band, n, weighted_n, rate,
                {wilson("rate", "n")}
            FROM agg
            WHERE n >= {min_n}
            """
        )

        # The retrieval corpus: senior cases with enough text to be worth
        # embedding, most severe first so a truncated index keeps the cases a
        # clinician would most want to see.
        con.execute(
            f"""
            CREATE OR REPLACE TABLE neiss_narratives AS
            SELECT
                row_number() OVER ()  AS case_id,
                narrative, mechanism, head_strike, loss_of_consciousness,
                on_anticoagulant, age_band, admitted, wt
            FROM neiss_senior_cases
            WHERE length(trim(narrative)) >= 20
            ORDER BY admitted DESC, head_strike DESC, on_anticoagulant DESC
            LIMIT {narrative_limit}
            """
        )

        rows = con.execute("SELECT count(*) FROM neiss_senior_rates").fetchone()[0]
        kept = con.execute("SELECT count(*) FROM neiss_narratives").fetchone()[0]
        log.info("neiss_senior_rates: %s cells, neiss_narratives: %s rows", rows, kept)
        return rows
    finally:
        con.close()
        # Anything already running is holding cached Nones from before
        # this load. Without this the app keeps serving mock cards until
        # it is restarted, and nothing looks broken.
        from .lookup import clear_cache

        clear_cache()
