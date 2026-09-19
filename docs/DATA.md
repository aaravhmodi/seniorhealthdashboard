# Data: which dataset answers which question

Three public datasets, no credentialing, no data-use agreement, all downloadable
today. Each one answers a different question, and saying which is which is the
difference between a data project and a dataset name-drop.

| question | dataset | table |
|---|---|---|
| "In ED visits like this, how often does the patient get admitted?" | **NHAMCS** (CDC ED survey) | `nhamcs_senior_rates` |
| "After a fall, how often is an older adult hospitalised — and does a blood thinner change it?" | **NEISS** (CPSC injury surveillance) | `neiss_senior_rates` |
| "Is this symptom reported unusually often with a medicine they take?" | **FAERS** (FDA adverse events) | `faers_signals`, `faers_pair_signals` |
| "Is this different from *this* patient's normal?" | their own check-ins | computed live in `store.baseline` |

Deliberately **not** used: MIMIC-IV-ED needs credentialing and a signed DUA,
which will not land in a hackathon weekend. Synthea is for generating demo
patients, not for evidence.

## Get the files

```bash
mkdir -p backend/data/raw/{nhamcs,neiss,faers}
```

- **NHAMCS** — <https://www.cdc.gov/nchs/ahcd/datasets_documentation_related.htm>
  Download the **ED** public-use files (not outpatient). Pool several years.
- **NEISS** — <https://www.cpsc.gov/Research--Statistics/NEISS-Injury-Data>
  Any recent years. The narrative column is what makes the anticoagulant split
  possible, so keep it.
- **FAERS** — <https://fis.fda.gov/extensions/FPD-QDE-FAERS/FPD-QDE-FAERS.html>
  Download the **ASCII** quarters and unzip each so `DEMO*.txt`, `DRUG*.txt` and
  `REAC*.txt` sit together in one folder per quarter.

## Load them

```bash
cd backend
python -m app.datasets.cli status
python -m app.datasets.cli nhamcs data/raw/nhamcs/*.csv
python -m app.datasets.cli neiss  data/raw/neiss/*.csv
python -m app.datasets.cli faers  data/raw/faers/2024q1 data/raw/faers/2024q2
```

Everything lands in one DuckDB file (`DUCKDB_PATH`, default
`./data/warehouse.duckdb`) that you can hand a teammate in Slack. Loaders are
idempotent and independent — re-run one without touching the others.

Check what the app sees: `GET /datasets/status`.

## The swap is automatic

`evidence.py` asks `datasets.lookup` first and falls back to its placeholder
when a table is missing. Load the data and the cards change under a running app.
Any card whose `source` starts with `MOCK` is a placeholder, the UI badges it,
and **only non-MOCK numbers may appear in the deck.**

## Decisions a judge will ask about

**Survey weights.** NHAMCS is a sample of visits, not a census. Every rate is
computed with `PATWT`, so it is a national estimate. Unweighted counts would
describe the hospitals that happened to be sampled.

**Leakage.** Only fields known at triage are used. Reason-for-visit is recorded
at check-in, so it stays; diagnosis and disposition are outcomes and are never
features. This is the first thing to point at when someone asks how we know the
model is not cheating.

**Thin cells.** `min_n` (default 30) drops cells too small to quote. A rate from
four sampled visits is noise with a percent sign on it.

**Wilson intervals**, not normal-approximation ones: the normal approximation
produces negative lower bounds on rare outcomes, which looks absurd next to a
clinician.

**Haldane–Anscombe correction** (+0.5 to each cell of the 2×2) before computing
ROR. A drug reported only ever with one event gives a zero cell, which divides
by zero and silently deletes exactly the strongest signals.

**A disproportionality signal is not a risk.** ROR says an event is *reported*
more often with this drug than with others. It is not an incidence, it does not
establish causation, and reporting is biased by publicity and litigation. Our
card text says "reported more often with", never "causes", and signals whose
interval crosses 1 are not shown at all.

**FAERS duplicates.** The same case is re-reported across quarters as follow-ups
arrive. We keep one row per `CASEID` — the highest `FDA_DT`, ties broken on
`PRIMARYID`. Skip this and every count is inflated.

## Limitations to say out loud

- NHAMCS is a national sample and ends at 2022. It gives base rates; it is not
  calibrated to any one emergency department.
- NEISS covers injuries only. It has nothing to say about chest pain.
- FAERS is spontaneous reporting: voluntary, incomplete, and biased.
- The demo patients are synthetic. No real patient data is in this repo.

## Next, if there is time

The predictive model: train on 65+ NHAMCS visits using only triage-time fields,
split by year (train earlier, test 2021–22), report AUC and calibration, and
compare 65+ against younger adults. Then flag patients whose assigned acuity is
far below the model's risk — a second pair of eyes for atypical presentations.
That is the piece that turns this from retrieval into a data-science result.
