"""FAERS: what a medicine is reported alongside. The polypharmacy signal.

Source: FDA Adverse Event Reporting System quarterly files, ASCII, `$`-delimited.
Free, no credentialing.

    https://fis.fda.gov/extensions/FPD-QDE-FAERS/FPD-QDE-FAERS.html

Five things everyone gets wrong with FAERS, and what we do about them:

1. **Duplicates.** The same case is re-reported across quarters as it is
   followed up. The FDA's own instruction is to keep, for each CASEID, the row
   with the highest FDA_DT, breaking ties on the highest PRIMARYID. We do that
   before counting anything. Skip it and every count is inflated.
2. **Not every drug on a report is a suspect.** Each DRUG row carries ROLE_COD:
   PS (primary suspect), SS (secondary suspect), C (concomitant) or I
   (interacting). A report of bleeding on warfarin also lists the patient's
   statin, their metformin and their eye drops. Counting those as if someone
   had implicated them inflates every denominator and invents signals for
   whatever old people happen to take. We count **PS only**.
3. **Use PROD_AI, not DRUGNAME.** DRUGNAME is whatever the reporter typed
   ("COUMADIN", "warfarin sodium", "WARFARIN 5MG"). PROD_AI is the curated
   active ingredient. It exists from **2014 Q3 onward**, which is exactly why
   that is the earliest quarter worth loading.
4. **Thin cells lie.** A drug with three reports, all of one event, produces a
   spectacular ratio that means nothing. We shrink every estimate toward the
   null with a Gamma-Poisson prior, so small cells have to earn their signal.
5. **A disproportionality signal is not a risk.** ROR says an event is reported
   more often with this drug than with others. It is not an incidence, it does
   not establish cause, and reporting is biased by publicity and litigation.
   The UI must say "reported more often with", never "causes". Our card text
   does.

What we build, all restricted to primary-suspect drugs on reports from adults
65+ where an age is given:

    faers_ingredients    the ingredient dictionary, built FROM the data
    faers_signals        ingredient x event: ROR, PRR, shrunk EB estimate, CI
    faers_pair_signals   two ingredients co-reported with the same event

Usage:
    python -m app.datasets.cli faers data/raw/faers/2024q1 data/raw/faers/2024q2
"""
from __future__ import annotations

import logging
import pathlib

from .warehouse import SENIOR_AGE_MIN, connect

log = logging.getLogger(__name__)

# Brand -> ingredient, for normalising what a PATIENT says or photographs.
# FAERS rows no longer go through this (they use PROD_AI, which is already
# curated); this dictionary exists to turn "my Coumadin" from a voice check-in
# into the ingredient the signal table is keyed on. `faers_ingredients`, built
# from the data, is the authority for what ingredients exist at all.
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

# ROLE_COD values we count. PS is the primary suspect drug -- the one the
# reporter is pointing at. SS/C/I are secondary suspect, concomitant and
# interacting, and including them is the single most common way to manufacture
# a signal out of nothing.
SUSPECT_ROLES = ("PS",)

# Gamma-Poisson prior for the shrunk estimate. alpha=beta=0.5 is weak: it pulls
# a cell with a handful of reports most of the way back to 1, and barely moves
# one with thousands. Stated here rather than buried so it can be argued with.
PRIOR_ALPHA = 0.5
PRIOR_BETA = 0.5


def _require_columns(con, view: str, needed: tuple[str, ...], hint: str) -> None:
    present = {
        row[0].lower()
        for row in con.execute(f"DESCRIBE SELECT * FROM {view} LIMIT 1").fetchall()
    }
    missing = [c for c in needed if c.lower() not in present]
    if missing:
        raise ValueError(
            f"{view} is missing {missing}. {hint} Columns present: "
            f"{sorted(present)[:20]}"
        )


def _quarter_tables(con, folder: pathlib.Path) -> None:
    """Register DEMO/DRUG/REAC for one quarter folder as views."""
    # The FDA zip unpacks into ASCII/ -- accept the quarter folder or that.
    if not any(folder.glob("*.txt")) and (folder / "ASCII").is_dir():
        folder = folder / "ASCII"
    for kind in QUARTER_FILES:
        # Case-insensitive, and each file once: on Windows "DEMO*" and "demo*"
        # match the same file, and globbing both read every row twice.
        matches = sorted({
            p.resolve()
            for p in folder.glob("*.txt")
            if p.name.upper().startswith(kind) and p.name.upper().endswith(".TXT")
        })
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

    _require_columns(
        con, "drug_raw", ("primaryid", "drug_seq", "role_cod", "prod_ai"),
        "PROD_AI exists only from 2014 Q3 onward -- load that quarter or later.",
    )
    _require_columns(con, "demo_raw", ("primaryid", "caseid", "fda_dt", "age"), "")
    _require_columns(con, "reac_raw", ("primaryid", "pt"), "")


def load(
    folders: list[str], min_reports: int = 20, pair_top_k: int = 150
) -> dict[str, int]:
    con = connect()
    try:
        con.execute("CREATE OR REPLACE TABLE faers_demo_all (caseid VARCHAR, primaryid VARCHAR, fda_dt VARCHAR, age_years DOUBLE)")
        con.execute(
            "CREATE OR REPLACE TABLE faers_drug_all "
            "(primaryid VARCHAR, drug_seq VARCHAR, ingredient VARCHAR, role_cod VARCHAR)"
        )
        con.execute("CREATE OR REPLACE TABLE faers_reac_all (primaryid VARCHAR, event VARCHAR)")

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
            roles = "', '".join(SUSPECT_ROLES)
            con.execute(
                f"""
                INSERT INTO faers_drug_all
                SELECT primaryid,
                       drug_seq,
                       -- PROD_AI lists combination products with a backslash
                       -- ("AMLODIPINE\\VALSARTAN"); the first is the one the
                       -- report is about often enough to be the useful default.
                       lower(trim(split_part(prod_ai, '\\', 1))) AS ingredient,
                       upper(trim(role_cod)) AS role_cod
                FROM drug_raw
                WHERE prod_ai IS NOT NULL
                  AND trim(prod_ai) <> ''
                  AND upper(trim(role_cod)) IN ('{roles}')
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

        # The ingredient dictionary, built from the data rather than typed by
        # hand. This is what a spoken or photographed medicine gets matched
        # against, so it has to reflect what FAERS actually contains.
        con.execute(
            """
            CREATE OR REPLACE TABLE faers_ingredients AS
            SELECT ingredient, count(DISTINCT primaryid) AS senior_reports
            FROM faers_pairs
            GROUP BY ingredient
            ORDER BY senior_reports DESC
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
                    a + 0.5 AS ac, b + 0.5 AS bc, c + 0.5 AS cc, d + 0.5 AS dc,
                    -- Expected count under independence, for the shrunk estimate.
                    ((a + b)::DOUBLE * (a + c)) / nullif(a + b + c + d, 0) AS expected
                FROM cells
            ),
            stats AS (
                SELECT
                    ingredient, event, a AS n, b, c, d, expected,
                    (ac * dc) / (bc * cc)                                AS ror,
                    (ac / (ac + bc)) / (cc / (cc + dc))                  AS prr,
                    sqrt(1/ac + 1/bc + 1/cc + 1/dc)                      AS se_log_ror,
                    -- Gamma-Poisson posterior mean: (a + alpha) / (E + beta).
                    -- A cell with 3 reports collapses toward 1; a cell with
                    -- 3000 barely moves. This is what stops a drug that was
                    -- mentioned twice from topping the table.
                    (a + {PRIOR_ALPHA}) / nullif(expected + {PRIOR_BETA}, 0) AS eb_rrr
                FROM corrected
            )
            SELECT
                ingredient, event, n, b, c, d, expected, ror, prr, eb_rrr,
                exp(ln(ror) - 1.96 * se_log_ror) AS ci_low,
                exp(ln(ror) + 1.96 * se_log_ror) AS ci_high
            FROM stats
            WHERE n >= {min_reports}
            """
        )

        # Pair signals: the interaction story. All pairs is combinatorial, so we
        # limit to the ingredients that actually appear often in senior reports
        # -- which is where the compute earns its keep rather than burning on
        # pairs nobody takes.
        top_k = [
            row[0] for row in con.execute(
                f"SELECT ingredient FROM faers_ingredients "
                f"WHERE senior_reports >= {min_reports} "
                f"ORDER BY senior_reports DESC LIMIT {pair_top_k}"
            ).fetchall()
        ]
        tracked = "', '".join(top_k) if top_k else "__none__"
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
            "faers_ingredients": con.execute(
                "SELECT count(*) FROM faers_ingredients"
            ).fetchone()[0],
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
        # Anything already running is holding cached Nones from before
        # this load. Without this the app keeps serving mock cards until
        # it is restarted, and nothing looks broken.
        from .lookup import clear_cache

        clear_cache()
