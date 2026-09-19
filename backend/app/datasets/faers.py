"""FAERS: what a medicine is reported alongside. The polypharmacy signal.

Source: FDA Adverse Event Reporting System quarterly files, ASCII, `$`-delimited.
Free, no credentialing.

    https://fis.fda.gov/extensions/FPD-QDE-FAERS/FPD-QDE-FAERS.html

Three things everyone gets wrong with FAERS, and what we do about them:

1. **Duplicates.** The same case is re-reported across quarters as it is
   followed up. The FDA's own instruction is to keep, for each CASEID, the row
   with the highest FDA_DT, breaking ties on the highest PRIMARYID. We do that
   before counting anything. Skip it and every count is inflated.
2. **Drug names are free text.** "Coumadin", "warfarin sodium" and "WARFARIN
   5MG" are one ingredient. We normalise through a small dictionary; a real
   build would use RxNorm.
3. **A disproportionality signal is not a risk.** ROR says an event is reported
   more often with this drug than with others. It is not an incidence, it does
   not establish cause, and reporting is biased by publicity. The UI must say
   "reported more often with", never "causes". Our card text does.

What we build: `faers_signals` (ingredient x event, with ROR, PRR and a 95%
interval) and `faers_pair_signals` (two ingredients taken together), restricted
to reports in older adults where age is given.

Usage:
    python -m app.datasets.cli faers data/raw/faers/2024q1 data/raw/faers/2024q2
"""
from __future__ import annotations

import logging
import pathlib

from .warehouse import SENIOR_AGE_MIN, connect

log = logging.getLogger(__name__)

# Ingredient normalisation. Brand -> ingredient, lowercase. Small on purpose:
# these are the medicines our demo patients take plus the usual senior suspects.
INGREDIENT_ALIASES: dict[str, str] = {
    "coumadin": "warfarin", "jantoven": "warfarin", "warfarin sodium": "warfarin",
    "eliquis": "apixaban", "xarelto": "rivaroxaban", "plavix": "clopidogrel",
    "pradaxa": "dabigatran",
    "ambien": "zolpidem", "zolpidem tartrate": "zolpidem", "stilnox": "zolpidem",
    "imovane": "zopiclone", "lunesta": "eszopiclone",
    "glucophage": "metformin", "metformin hcl": "metformin",
    "lasix": "furosemide", "zestril": "lisinopril", "prinivil": "lisinopril",
    "norvasc": "amlodipine", "ditropan": "oxybutynin",
    "zoloft": "sertraline", "synthroid": "levothyroxine",
    "tylenol": "acetaminophen", "paracetamol": "acetaminophen",
    "advil": "ibuprofen", "motrin": "ibuprofen",
}

# MedDRA preferred terms are verbose; map the ones we surface to our labels.
REACTION_TO_LABEL: dict[str, str] = {
    "dizziness": "dizziness", "vertigo": "dizziness",
    "postural dizziness": "dizziness", "syncope": "dizziness",
    "fall": "fall", "accidental fall": "fall",
    "confusional state": "confusion", "delirium": "confusion",
    "disorientation": "confusion", "memory impairment": "confusion",
    "nausea": "nausea", "vomiting": "nausea",
    "haemorrhage": "bleeding", "gastrointestinal haemorrhage": "bleeding",
    "epistaxis": "bleeding", "contusion": "bleeding",
    "oedema peripheral": "swelling legs", "peripheral swelling": "swelling legs",
    "dyspnoea": "shortness of breath",
    "decreased appetite": "poor appetite",
    "hypotension": "dizziness", "orthostatic hypotension": "dizziness",
}

QUARTER_FILES = ("DEMO", "DRUG", "REAC")


def _quarter_tables(con, folder: pathlib.Path) -> None:
    """Register DEMO/DRUG/REAC for one quarter folder as views."""
    for kind in QUARTER_FILES:
        matches = sorted(folder.glob(f"{kind}*.txt")) + sorted(folder.glob(f"{kind.lower()}*.txt"))
        if not matches:
            raise FileNotFoundError(
                f"no {kind}*.txt in {folder}. Unzip the FAERS ASCII quarter so "
                f"DEMO24Q1.txt, DRUG24Q1.txt and REAC24Q1.txt sit together."
            )
        files = "[" + ", ".join(f"'{p.as_posix()}'" for p in matches) + "]"
        con.execute(
            f"CREATE OR REPLACE VIEW {kind.lower()}_raw AS "
            f"SELECT * FROM read_csv_auto({files}, delim='$', header=true, "
            f"union_by_name=true, ignore_errors=true, sample_size=-1, "
            f"all_varchar=true)"
        )


def load(folders: list[str], min_reports: int = 20) -> dict[str, int]:
    con = connect()
    try:
        con.execute("CREATE OR REPLACE TABLE faers_demo_all (caseid VARCHAR, primaryid VARCHAR, fda_dt VARCHAR, age_years DOUBLE)")
        con.execute("CREATE OR REPLACE TABLE faers_drug_all (primaryid VARCHAR, ingredient VARCHAR)")
        con.execute("CREATE OR REPLACE TABLE faers_reac_all (primaryid VARCHAR, event VARCHAR)")

        alias_case = " ".join(
            f"WHEN lower(trim(drugname)) LIKE '%{brand}%' THEN '{ing}'"
            for brand, ing in INGREDIENT_ALIASES.items()
        )
        reaction_case = " ".join(
            f"WHEN lower(trim(pt)) = '{term}' THEN '{label}'"
            for term, label in REACTION_TO_LABEL.items()
        )

        for folder in folders:
            _quarter_tables(con, pathlib.Path(folder))
            con.execute(
                f"""
                INSERT INTO faers_demo_all
                SELECT caseid, primaryid, fda_dt,
                       -- FAERS stores age with a unit column; normalise to years.
                       CASE upper(coalesce(age_cod, 'YR'))
                            WHEN 'YR' THEN TRY_CAST(age AS DOUBLE)
                            WHEN 'MON' THEN TRY_CAST(age AS DOUBLE) / 12
                            WHEN 'WK' THEN TRY_CAST(age AS DOUBLE) / 52
                            WHEN 'DY' THEN TRY_CAST(age AS DOUBLE) / 365
                            WHEN 'DEC' THEN TRY_CAST(age AS DOUBLE) * 10
                            ELSE NULL END AS age_years
                FROM demo_raw
                """
            )
            con.execute(
                f"""
                INSERT INTO faers_drug_all
                SELECT primaryid,
                       CASE {alias_case} ELSE lower(trim(drugname)) END AS ingredient
                FROM drug_raw
                WHERE drugname IS NOT NULL
                """
            )
            con.execute(
                f"""
                INSERT INTO faers_reac_all
                SELECT primaryid,
                       CASE {reaction_case} ELSE lower(trim(pt)) END AS event
                FROM reac_raw
                WHERE pt IS NOT NULL
                """
            )
            log.info("loaded quarter %s", folder)

        # FDA dedupe: one row per CASEID, the latest follow-up.
        con.execute(
            f"""
            CREATE OR REPLACE TABLE faers_cases AS
            SELECT primaryid, caseid, age_years
            FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY caseid
                    ORDER BY TRY_CAST(fda_dt AS BIGINT) DESC,
                             TRY_CAST(primaryid AS BIGINT) DESC
                ) AS rn
                FROM faers_demo_all
            )
            WHERE rn = 1 AND age_years >= {SENIOR_AGE_MIN}
            """
        )

        con.execute(
            """
            CREATE OR REPLACE TABLE faers_pairs AS
            SELECT DISTINCT c.primaryid, d.ingredient, r.event
            FROM faers_cases c
            JOIN faers_drug_all d USING (primaryid)
            JOIN faers_reac_all r USING (primaryid)
            """
        )

        # 2x2 disproportionality, per ingredient x event.
        #   a = reports of this drug with this event
        #   b = reports of this drug with any other event
        #   c = reports of other drugs with this event
        #   d = everything else
        con.execute(
            f"""
            CREATE OR REPLACE TABLE faers_signals AS
            WITH totals AS (
                SELECT count(DISTINCT primaryid) AS all_reports FROM faers_pairs
            ),
            per_drug AS (
                SELECT ingredient, count(DISTINCT primaryid) AS drug_reports
                FROM faers_pairs GROUP BY ingredient
            ),
            per_event AS (
                SELECT event, count(DISTINCT primaryid) AS event_reports
                FROM faers_pairs GROUP BY event
            ),
            cells AS (
                SELECT
                    p.ingredient,
                    p.event,
                    count(DISTINCT p.primaryid)                          AS a,
                    max(d.drug_reports) - count(DISTINCT p.primaryid)    AS b,
                    max(e.event_reports) - count(DISTINCT p.primaryid)   AS c,
                    max(t.all_reports) - max(d.drug_reports)
                        - max(e.event_reports) + count(DISTINCT p.primaryid) AS d
                FROM faers_pairs p
                JOIN per_drug  d ON d.ingredient = p.ingredient
                JOIN per_event e ON e.event = p.event
                CROSS JOIN totals t
                GROUP BY p.ingredient, p.event
            ),
            corrected AS (
                -- Haldane-Anscombe: add 0.5 to every cell. Without it a drug
                -- reported only ever with one event divides by zero and the
                -- signal vanishes -- exactly the strong signals we care about.
                SELECT
                    ingredient, event, a, b, c, d,
                    a + 0.5 AS ac, b + 0.5 AS bc, c + 0.5 AS cc, d + 0.5 AS dc
                FROM cells
            ),
            stats AS (
                SELECT
                    ingredient, event, a AS n, b, c, d,
                    (ac * dc) / (bc * cc)                                AS ror,
                    (ac / (ac + bc)) / (cc / (cc + dc))                  AS prr,
                    sqrt(1/ac + 1/bc + 1/cc + 1/dc)                      AS se_log_ror
                FROM corrected
            )
            SELECT
                ingredient, event, n, b, c, d, ror, prr,
                exp(ln(ror) - 1.96 * se_log_ror) AS ci_low,
                exp(ln(ror) + 1.96 * se_log_ror) AS ci_high
            FROM stats
            WHERE n >= {min_reports}
            """
        )

        # Pair signals: the interaction story, limited to ingredients we track.
        tracked = "', '".join(sorted(set(INGREDIENT_ALIASES.values())))
        con.execute(
            f"""
            CREATE OR REPLACE TABLE faers_pair_signals AS
            WITH co AS (
                SELECT
                    least(p1.ingredient, p2.ingredient)    AS ingredient_a,
                    greatest(p1.ingredient, p2.ingredient) AS ingredient_b,
                    p1.event,
                    count(DISTINCT p1.primaryid)           AS n
                FROM faers_pairs p1
                JOIN faers_pairs p2
                  ON p1.primaryid = p2.primaryid
                 AND p1.event = p2.event
                 AND p1.ingredient < p2.ingredient
                WHERE p1.ingredient IN ('{tracked}')
                  AND p2.ingredient IN ('{tracked}')
                GROUP BY 1, 2, 3
            )
            SELECT * FROM co WHERE n >= {min_reports}
            """
        )

        counts = {
            "faers_signals": con.execute("SELECT count(*) FROM faers_signals").fetchone()[0],
            "faers_pair_signals": con.execute(
                "SELECT count(*) FROM faers_pair_signals"
            ).fetchone()[0],
            "senior_cases": con.execute("SELECT count(*) FROM faers_cases").fetchone()[0],
        }
        log.info("faers: %s", counts)
        return counts
    finally:
        con.close()
