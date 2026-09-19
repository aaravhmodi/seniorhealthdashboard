"""DuckDB warehouse: one file, three pipelines, no server to run.

Why DuckDB and not Postgres or pandas: the public files are tens of millions of
rows of fixed-width and CSV text, DuckDB reads them straight off disk with SQL,
and the result is a single .duckdb file a teammate can drop in Slack. Nothing
to install, nothing to keep running during the demo.

Tables this package builds (all `senior_*` are filtered to ages 65+):

    nhamcs_senior_rates   reason-for-visit  -> admission/transfer rate, n, CI
    neiss_senior_rates    injury + body part -> hospitalised rate, n, CI
    faers_signals         ingredient + event -> ROR, PRR, n, CI
    faers_pair_signals    ingredient pair + event -> ROR, n

Everything downstream reads through `lookup.py`, which returns None when a
table is missing. That is the whole degradation story: no data file, no crash,
evidence falls back to the MOCK cards and the UI shows the mock badge.
"""
from __future__ import annotations

import pathlib
from typing import Any

from ..config import get_settings

TABLES = (
    "nhamcs_senior_rates",
    "neiss_senior_rates",
    "faers_signals",
    "faers_pair_signals",
)

SENIOR_AGE_MIN = 65


def db_path() -> pathlib.Path:
    return pathlib.Path(get_settings().duckdb_path).resolve()


def exists() -> bool:
    return db_path().is_file()


def connect(read_only: bool = False):
    """Open the warehouse. Raises a useful message if duckdb is not installed."""
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "duckdb is not installed. `pip install duckdb`, or run without the "
            "warehouse -- evidence falls back to the MOCK cards."
        ) from exc

    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if read_only and not path.is_file():
        raise FileNotFoundError(f"no warehouse at {path}; run the loaders first")
    return duckdb.connect(str(path), read_only=read_only)


def table_exists(con, name: str) -> bool:
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return bool(row and row[0])


def status() -> dict[str, Any]:
    """What is loaded, for /datasets/status and for the pitch slide."""
    if not exists():
        return {"warehouse": str(db_path()), "present": False, "tables": {}}
    try:
        con = connect(read_only=True)
    except Exception as exc:
        return {"warehouse": str(db_path()), "present": True, "error": str(exc)}
    try:
        tables = {}
        for name in TABLES:
            if table_exists(con, name):
                rows = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                tables[name] = {"rows": rows}
        return {"warehouse": str(db_path()), "present": True, "tables": tables}
    finally:
        con.close()


# --------------------------------------------------------------------------
# Wilson interval -- the right one for proportions with small cells
# --------------------------------------------------------------------------
WILSON_SQL = """
    -- Wilson score interval at 95%. Normal-approximation intervals go
    -- negative on rare outcomes, which looks absurd on a clinician's screen.
    (({p} + 1.9208 / (2 * {n})
      - 1.96 * sqrt(({p} * (1 - {p}) + 0.9604 / (4 * {n})) / {n}))
     / (1 + 3.8416 / {n})) AS ci_low,
    (({p} + 1.9208 / (2 * {n})
      + 1.96 * sqrt(({p} * (1 - {p}) + 0.9604 / (4 * {n})) / {n}))
     / (1 + 3.8416 / {n})) AS ci_high
"""


def wilson(p_expr: str, n_expr: str) -> str:
    return WILSON_SQL.format(p=p_expr, n=n_expr)
