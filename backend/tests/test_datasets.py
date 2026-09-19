"""The data pipelines.

These build a tiny warehouse from synthetic files shaped like the real public
ones, so the SQL is exercised for real without a 2GB download. When the actual
NHAMCS/NEISS/FAERS files land, the same code runs -- only the row counts change.

The first test is the most important one: with no warehouse at all, everything
still works and the evidence is honestly labelled as mock.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import evidence
from app.config import get_settings
from app.datasets import faers, lookup, neiss, nhamcs, warehouse
from app.main import app
from app.schemas import CheckIn, CheckInSource, Symptom
from app.store import now, store

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.post("/demo/reset")
        yield c


@pytest.fixture
def warehouse_at(tmp_path, monkeypatch):
    """Point the warehouse at a temp file and clear every cache around it."""
    def _set():
        monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "test.duckdb"))
        get_settings.cache_clear()
        lookup.clear_cache()
    _set()
    yield tmp_path
    get_settings.cache_clear()
    lookup.clear_cache()


def probe(symptoms: list[str], senior_id: str = "sen_rosa") -> CheckIn:
    return CheckIn(
        id="chk_probe",
        senior_id=senior_id,
        created_at=now(),
        source=CheckInSource.TEXT,
        language="en",
        raw_text="probe",
        symptoms=[Symptom(label=s) for s in symptoms],
    )


# -- degradation -----------------------------------------------------------
def test_no_warehouse_means_mock_cards_not_a_crash(warehouse_at, client):
    assert not warehouse.exists()
    assert lookup.admission_rate("chest pain", 79) is None
    assert lookup.drug_event_signal("warfarin", "bleeding") is None

    senior = store.get_senior("sen_rosa")
    cards = evidence.neiss_cards(probe(["chest pain"]), senior)
    assert cards and cards[0].source.startswith("MOCK")


def test_status_endpoint_reports_an_empty_warehouse(warehouse_at, client):
    body = client.get("/datasets/status").json()
    assert body["warehouse"]["present"] is False
    assert all(v is False for v in body["tables_available"].values())


# -- NHAMCS ----------------------------------------------------------------
def write_nhamcs(tmp_path, rows):
    path = tmp_path / "nhamcs2022.csv"
    lines = ["AGE,SEX,RFV1,ADISP,PATWT,VYEAR"]
    lines += [f"{a},{s},{r},{d},{w},2022" for a, s, r, d, w in rows]
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def test_nhamcs_builds_weighted_rates_with_intervals(warehouse_at, tmp_path):
    # 40 chest-pain visits in 75-84: 30 admitted (code 4), 10 sent home (1).
    rows = [(78, 1, "1050", 4, 1000) for _ in range(30)]
    rows += [(78, 1, "1050", 1, 1000) for _ in range(10)]
    # A thin cell that must be dropped by min_n.
    rows += [(80, 2, "1245", 4, 1000) for _ in range(5)]

    cells = nhamcs.load([write_nhamcs(tmp_path, rows)], min_n=30)
    assert cells == 1, "the 5-visit cell is too thin to quote"

    result = lookup.admission_rate("chest pain", 78)
    assert result is not None
    assert result["n"] == 40
    assert result["rate_percent"] == pytest.approx(75.0, abs=0.1)
    assert result["ci_low"] < 75.0 < result["ci_high"]
    assert 0 <= result["ci_low"] and result["ci_high"] <= 100
    assert result["age_band"] == "75-84"
    assert not result["source"].startswith("MOCK")


def test_nhamcs_excludes_under_65s(warehouse_at, tmp_path):
    rows = [(40, 1, "1050", 4, 1000) for _ in range(50)]
    assert nhamcs.load([write_nhamcs(tmp_path, rows)], min_n=1) == 0


def test_nhamcs_respects_the_survey_weight(warehouse_at, tmp_path):
    """Unweighted this is 50%; weighted it is not. Getting this wrong would
    quietly misstate every base rate in the demo."""
    rows = [(70, 1, "1050", 4, 9000) for _ in range(20)]   # admitted, heavy
    rows += [(70, 1, "1050", 1, 1000) for _ in range(20)]  # home, light
    nhamcs.load([write_nhamcs(tmp_path, rows)], min_n=10)

    result = lookup.admission_rate("chest pain", 70)
    assert result["rate_percent"] == pytest.approx(90.0, abs=0.1)


def test_a_missing_column_fails_loudly(warehouse_at, tmp_path):
    bad = tmp_path / "wrong.csv"
    bad.write_text("AGE,SEX\n70,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        nhamcs.load([str(bad)])


def test_real_nhamcs_rates_replace_the_mock_card(warehouse_at, tmp_path, client):
    rows = [(78, 1, "1050", 4, 1000) for _ in range(30)]
    rows += [(78, 1, "1050", 1, 1000) for _ in range(10)]
    nhamcs.load([write_nhamcs(tmp_path, rows)], min_n=30)

    senior = store.get_senior("sen_rosa")
    card = evidence.neiss_cards(probe(["chest pain"]), senior)[0]
    assert not card.source.startswith("MOCK")
    assert "NHAMCS" in card.source
    assert card.stat.n == 40
    assert card.stat.ci_low is not None


# -- NEISS -----------------------------------------------------------------
def write_neiss(tmp_path, rows):
    path = tmp_path / "neiss2023.csv"
    lines = ["age,sex,body_part,diag,disposition,weight,narrative"]
    lines += [f"{a},1,{b},{d},{disp},{w},{narr}" for a, b, d, disp, w, narr in rows]
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def test_neiss_splits_falls_by_anticoagulant(warehouse_at, tmp_path):
    # On a thinner: 35 of 40 admitted. Not on one: 10 of 40.
    rows = [(80, 75, 62, 4, 100, "80YOF FELL AT HOME ON COUMADIN") for _ in range(35)]
    rows += [(80, 75, 62, 1, 100, "80YOF FELL AT HOME ON COUMADIN") for _ in range(5)]
    rows += [(80, 75, 62, 4, 100, "80YOF FELL AT HOME") for _ in range(10)]
    rows += [(80, 75, 62, 1, 100, "80YOF FELL AT HOME") for _ in range(30)]

    assert neiss.load([write_neiss(tmp_path, rows)], min_n=30) == 2

    on = lookup.fall_admission_rate(True)
    off = lookup.fall_admission_rate(False)
    assert on["rate_percent"] == pytest.approx(87.5, abs=0.1)
    assert off["rate_percent"] == pytest.approx(25.0, abs=0.1)
    assert on["rate_percent"] > off["rate_percent"], "this is the clinical point"


# -- FAERS -----------------------------------------------------------------
def write_faers_quarter(tmp_path, demo, drug, reac, quarter="24Q1"):
    folder = tmp_path / f"faers{quarter}"
    folder.mkdir()
    (folder / f"DEMO{quarter}.txt").write_text(
        "primaryid$caseid$fda_dt$age$age_cod\n"
        + "\n".join(f"{p}${c}${dt}${age}$YR" for p, c, dt, age in demo),
        encoding="utf-8",
    )
    (folder / f"DRUG{quarter}.txt").write_text(
        "primaryid$drugname\n" + "\n".join(f"{p}${d}" for p, d in drug),
        encoding="utf-8",
    )
    (folder / f"REAC{quarter}.txt").write_text(
        "primaryid$pt\n" + "\n".join(f"{p}${r}" for p, r in reac),
        encoding="utf-8",
    )
    return str(folder)


def test_faers_dedupes_by_caseid_keeping_the_latest_followup(warehouse_at, tmp_path):
    """Case 1 is reported three times. It must count once, or every number in
    the medication cards is inflated."""
    demo = [("100", "1", "20240101", "80"),
            ("101", "1", "20240201", "80"),
            ("102", "1", "20240301", "80")]
    drug = [(p, "COUMADIN") for p, *_ in demo]
    reac = [(p, "Haemorrhage") for p, *_ in demo]
    faers.load([write_faers_quarter(tmp_path, demo, drug, reac)], min_reports=1)

    con = warehouse.connect(read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM faers_cases").fetchone()[0] == 1
        kept = con.execute("SELECT primaryid FROM faers_cases").fetchone()[0]
        assert kept == "102", "the latest follow-up is the one to keep"
    finally:
        con.close()


def test_faers_normalises_brands_and_computes_a_signal(warehouse_at, tmp_path):
    demo, drug, reac = [], [], []
    # 60 senior cases on warfarin (mixed brand spellings) reporting bleeding.
    for i in range(60):
        pid = str(1000 + i)
        demo.append((pid, pid, "20240101", "78"))
        drug.append((pid, "COUMADIN" if i % 2 else "warfarin sodium"))
        reac.append((pid, "Haemorrhage"))
    # 60 cases on an unrelated drug reporting something else, for the denominator.
    for i in range(60):
        pid = str(2000 + i)
        demo.append((pid, pid, "20240101", "78"))
        drug.append((pid, "SYNTHROID"))
        reac.append((pid, "Nausea"))

    counts = faers.load([write_faers_quarter(tmp_path, demo, drug, reac)], min_reports=10)
    assert counts["senior_cases"] == 120

    signal = lookup.drug_event_signal("warfarin", "bleeding")
    assert signal is not None, "brand names must normalise to the ingredient"
    assert signal["n"] == 60
    assert signal["ror"] > 1
    assert signal["significant"] is True


def test_faers_excludes_reports_from_under_65s(warehouse_at, tmp_path):
    demo = [(str(i), str(i), "20240101", "40") for i in range(30)]
    drug = [(str(i), "COUMADIN") for i in range(30)]
    reac = [(str(i), "Haemorrhage") for i in range(30)]
    counts = faers.load([write_faers_quarter(tmp_path, demo, drug, reac)], min_reports=1)
    assert counts["senior_cases"] == 0


def test_an_unzipped_quarter_with_missing_files_says_so(warehouse_at, tmp_path):
    empty = tmp_path / "faers_empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="DEMO"):
        faers.load([str(empty)])


# -- status ----------------------------------------------------------------
def test_status_lists_what_was_loaded(warehouse_at, tmp_path, client):
    rows = [(78, 1, "1050", 4, 1000) for _ in range(40)]
    nhamcs.load([write_nhamcs(tmp_path, rows)], min_n=10)

    body = client.get("/datasets/status").json()
    assert body["warehouse"]["present"] is True
    assert body["warehouse"]["tables"]["nhamcs_senior_rates"]["rows"] >= 1
    assert body["tables_available"]["nhamcs_senior_rates"] is True
    assert body["tables_available"]["faers_signals"] is False
