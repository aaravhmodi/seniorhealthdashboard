# Senior outcome model

The NEISS outcome model estimates the probability that an injury visit ends in
hospital admission or observation. It is a second pair of eyes, not a triage
replacement: deterministic red-flag rules still own the action ladder.

## Features and leakage boundary

The model uses only fields available at check-in:

- narrative
- age
- sex
- location
- product
- body part

`disposition` is the label. It, diagnosis, weighted outcome aggregates, and
any post-visit field are rejected by `validate_feature_columns()` and never
enter the feature matrix.

## Validation

Metrics are exposed by `GET /datasets/status` and written to
`backend/data/model_metrics.json` after retrieval reindexing. The model reports
`insufficient_years` until at least two NEISS years are loaded. Once multiple
years are present, the latest year is held out, AUC is weighted by the NEISS
survey weight, and calibration can be reported by age band.

An evaluation includes `model_risk`. If the estimated risk is well above the
current ladder rung, it adds a `model` evidence card and sets `under_triage` and
`requires_human_review`; it never lowers or overrides a deterministic level.
