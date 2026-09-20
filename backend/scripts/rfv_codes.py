"""Regenerate the NHAMCS reason-for-visit codes in `datasets/nhamcs.py`.

The RFV classification is five digits and the app maps thirteen spoken symptom
labels onto it. Those codes are quoted to patients as base rates, so they are
read out of the survey's own value labels rather than transcribed by hand:

    pip install pandas
    curl -O https://data.nber.org/nhamcs/data/nhamcsed2015.dta
    python -m scripts.rfv_codes nhamcsed2015.dta

It prints every code whose label matches each symptom. Read the output, pick
the codes that belong, and paste them into SYMPTOM_TO_RFV -- the choice of
which near-matches to include is a clinical judgement, so it is deliberately
not automated.

Note there is no code for "fall": NHAMCS records a fall as a cause of injury,
not a reason for visit. Falls go to the NEISS cells instead.
"""
from __future__ import annotations

import re
import sys

# What the extraction lexicon can produce -> what to hunt for in the codebook.
SEARCHES: dict[str, list[str]] = {
    "chest pain": [r"chest pain"],
    "shortness of breath": [r"shortness of breath", r"breathing", r"dyspnea"],
    "dizziness": [r"vertigo", r"dizz"],
    "confusion": [r"confus", r"deliri", r"memory", r"behavio", r"conscious"],
    "weakness one side": [r"weakness", r"paralysis", r"numbness", r"speech"],
    "fever": [r"fever", r"chills"],
    "nausea": [r"nausea", r"vomit"],
    "poor appetite": [r"appetite"],
    "swelling legs": [r"swelling of (leg|ankle|foot)", r"edema"],
    "urinary symptoms": [r"urin"],
    "back pain": [r"back pain", r"backache"],
    "headache": [r"headache", r"pain in head"],
    "bleeding": [r"bleeding", r"hemorrhag"],
}


def main(path: str) -> int:
    import pandas as pd

    labels = pd.io.stata.StataReader(path).value_labels()["RFVF"]
    for symptom, patterns in SEARCHES.items():
        hits = sorted(
            (int(code), str(text))
            for code, text in labels.items()
            if any(re.search(p, str(text), re.I) for p in patterns)
        )
        print(f"\n=== {symptom} ({len(hits)} candidates) ===")
        for code, text in hits:
            print(f"  {code:6}  {text}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
