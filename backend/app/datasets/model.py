"""Leakage-safe NEISS outcome model for senior under-triage review.

The model uses only fields available at check-in: narrative, age, sex,
location, product, and body part. Disposition is the label and is never a
feature. With one loaded year we still fit a runtime scorer, but metrics say
``insufficient_years`` instead of pretending a random split is validation.
"""
from __future__ import annotations

import json
import logging
import math
import pathlib
from datetime import date
from functools import lru_cache

from ..config import get_settings
from ..schemas import ActionLevel, CheckIn, EvidenceCard, EvidenceKind, Senior, Stat
from .warehouse import connect, exists, table_exists

FEATURE_COLUMNS = (
    "narrative", "age", "sex", "location", "product", "body_part_code",
)
LEAKAGE_COLUMNS = {"disposition", "admitted", "diagnosis_code", "weighted_n", "rate"}
METRICS_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "model_metrics.json"
log = logging.getLogger(__name__)


def _rows() -> list[dict]:
    if not exists():
        return []
    con = connect(read_only=True)
    try:
        if not table_exists(con, "neiss_senior_cases"):
            return []
        fields = ", ".join((*FEATURE_COLUMNS, "treatment_date", "admitted", "wt"))
        rows = con.execute(f"SELECT {fields} FROM neiss_senior_cases").fetchall()
        return [dict(zip((*FEATURE_COLUMNS, "treatment_date", "admitted", "wt"), row)) for row in rows]
    finally:
        con.close()


def validate_feature_columns(columns: list[str] | tuple[str, ...]) -> None:
    """Fail loudly if an outcome/leaking field is accidentally selected."""
    leaked = set(columns) & LEAKAGE_COLUMNS
    if leaked:
        raise ValueError(f"outcome leakage: {sorted(leaked)} cannot be model features")
    missing = set(FEATURE_COLUMNS) - set(columns)
    if missing:
        raise ValueError(f"model feature set is missing {sorted(missing)}")


def _feature_text(row: dict) -> str:
    return " ".join(str(row.get(name) or "") for name in FEATURE_COLUMNS)


def _fit(rows: list[dict]):
    validate_feature_columns(list(FEATURE_COLUMNS))
    if len(rows) < 40 or len({str(r.get("treatment_date") or "")[:4] for r in rows}) < 1:
        return None
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression

    texts = [_feature_text(row) for row in rows]
    labels = [int(bool(row["admitted"])) for row in rows]
    if len(set(labels)) < 2:
        return None
    vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=12000)
    x = vectorizer.fit_transform(texts)
    model = LogisticRegression(max_iter=300, class_weight="balanced", random_state=0)
    model.fit(x, labels, sample_weight=[float(r.get("wt") or 1.0) for r in rows])
    return vectorizer, model


def _metrics(rows: list[dict]) -> dict:
    years = sorted({str(r.get("treatment_date") or "")[:4] for r in rows if r.get("treatment_date")})
    metrics: dict = {
        "feature_columns": list(FEATURE_COLUMNS),
        "label": "admitted_or_observed",
        "years": years,
        "senior_n": len(rows),
        "status": "insufficient_years" if len(years) < 2 else "ok",
        "holdout_years": years[-1:] if len(years) >= 2 else [],
    }
    if len(years) < 2:
        metrics["note"] = "Load at least two NEISS years for a year-based holdout AUC."
        return metrics
    from sklearn.metrics import roc_auc_score
    train = [r for r in rows if str(r["treatment_date"])[:4] < years[-1]]
    test = [r for r in rows if str(r["treatment_date"])[:4] == years[-1]]
    fitted = _fit(train)
    if not fitted or len({int(bool(r["admitted"])) for r in test}) < 2:
        metrics["status"] = "insufficient_holdout_variation"
        return metrics
    vectorizer, model = fitted
    pred = model.predict_proba(vectorizer.transform([_feature_text(r) for r in test]))[:, 1]
    metrics["holdout_auc_weighted"] = round(float(roc_auc_score(
        [int(bool(r["admitted"])) for r in test], pred,
        sample_weight=[float(r.get("wt") or 1.0) for r in test],
    )), 4)
    return metrics


@lru_cache(maxsize=1)
def runtime_model():
    rows = _rows()
    return _fit(rows), _metrics(rows)


def refresh() -> dict:
    runtime_model.cache_clear()
    _, metrics = runtime_model()
    # Vercel's deployed function bundle is read-only.  The runtime metrics
    # remain available through /datasets/status; persistence is only a local
    # convenience and must not prevent application startup in serverless mode.
    try:
        METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        METRICS_PATH.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        log.warning("could not persist model metrics at %s: %s", METRICS_PATH, exc)
    return metrics


def status() -> dict:
    _, metrics = runtime_model()
    return metrics


def predict(checkin: CheckIn, senior: Senior) -> float | None:
    fitted, _ = runtime_model()
    if not fitted:
        return None
    vectorizer, model = fitted
    row = {
        "narrative": checkin.raw_text or "",
        "age": senior.age,
        "sex": "",
        "location": "",
        "product": "",
        "body_part_code": "",
    }
    return round(float(model.predict_proba(vectorizer.transform([_feature_text(row)]))[0, 1]), 4)


def card(checkin: CheckIn, senior: Senior, level: ActionLevel) -> tuple[EvidenceCard, float] | None:
    risk = predict(checkin, senior)
    if risk is None:
        return None
    ceiling = {1: 0.20, 2: 0.45, 3: 0.70, 4: 1.01}[int(level)]
    if risk <= ceiling + 0.15:
        return None
    return EvidenceCard(
        kind=EvidenceKind.MODEL,
        title="Outcome model suggests human review",
        detail=(
            f"The leakage-safe NEISS model estimates {risk:.0%} admission or "
            f"observation risk, well above the current action rung. This is a "
            "review flag, not a diagnosis or a replacement for the safety rules."
        ),
        stat=Stat(value=risk * 100, unit="percent"),
        source="NEISS outcome model, triage-time features only",
        weight=0.0,
    ), risk
