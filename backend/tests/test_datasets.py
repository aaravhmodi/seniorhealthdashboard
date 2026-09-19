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
    # A thin cell that must be dropped by min_n: 5 chest-pain visits at 68.
    rows += [(68, 2, "1050", 4, 1000) for _ in range(5)]

    cells = nhamcs.load([write_nhamcs(tmp_path, rows)], min_n=30)
    assert cells == 2, "75-84 and all-65+ survive; the 5-visit 65-74 cell does not"
    assert lookup.admission_rate("chest pain", 68)["age_band"] == "65+", (
        "a thin band falls back to every senior rather than to MOCK"
    )

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


def write_public_use(tmp_path, rows, name="ed2021.csv"):
    """The layout the CDC actually ships (checked against ED 2021/2022):
    a 5-digit RFV1, its 4-digit RFV13D, one 0/1 flag per disposition, and an
    ADISP that is -7 ("not applicable") for almost everyone."""
    path = tmp_path / name
    lines = ["AGE,SEX,YEAR,RFV1,RFV13D,PATWT,ADMITHOS,TRANOTH,OBSHOS,OBSDIS,DIEDED,ADISP"]
    for age, rfv1, admit, tran, obs_admit, obs_home, died, w in rows:
        lines.append(
            f"{age},1,2021,{rfv1},{rfv1 // 10},{w},{admit},{tran},{obs_admit},"
            f"{obs_home},{died},-7"
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def test_nhamcs_reads_the_real_public_use_layout(warehouse_at, tmp_path):
    """The first version keyed on RFV1 with 4-digit codes and read ADISP as the
    outcome. On the real files that matched nothing and every card stayed MOCK
    -- with no error anywhere."""
    rows = [(80, 14150, 1, 0, 0, 0, 0, 1000) for _ in range(20)]   # admitted
    rows += [(80, 14150, 0, 1, 0, 0, 0, 1000) for _ in range(5)]   # transferred
    rows += [(80, 14150, 0, 0, 1, 0, 0, 1000) for _ in range(5)]   # observed, stayed
    rows += [(80, 14150, 0, 0, 0, 1, 0, 1000) for _ in range(10)]  # observed, went home
    rows += [(80, 14150, 0, 0, 0, 0, 0, 1000) for _ in range(20)]  # home
    nhamcs.load([write_public_use(tmp_path, rows)], min_n=30)

    result = lookup.admission_rate("shortness of breath", 80)
    assert result is not None, "RFV13D 1415 must match the symptom map"
    assert result["n"] == 60
    # 30 of 60 not sent home. Observation-then-discharge counts as home.
    assert result["rate_percent"] == pytest.approx(50.0, abs=0.1)


def test_a_five_digit_rfv1_alone_is_normalised(warehouse_at, tmp_path):
    path = tmp_path / "rfv1_only.csv"
    lines = ["AGE,RFV1,PATWT,ADMITHOS"]
    lines += [f"70,10500,1000,{1 if i < 10 else 0}" for i in range(40)]
    path.write_text("\n".join(lines), encoding="utf-8")
    nhamcs.load([str(path)], min_n=30)

    result = lookup.admission_rate("chest pain", 70)
    assert result is not None and result["n"] == 40
    assert result["rate_percent"] == pytest.approx(25.0, abs=0.1)


def test_every_symptom_we_extract_has_a_reason_code_except_fall():
    """A symptom with no code silently shows a MOCK card forever. Fall is the
    exception on purpose: the classification has no fall code, NEISS answers it."""
    from app.extraction import LEXICON

    missing = set(LEXICON) - set(nhamcs.SYMPTOM_TO_RFV) - {"fall"}
    assert not missing, f"no NHAMCS reason code for {sorted(missing)}"
    assert "fall" not in nhamcs.SYMPTOM_TO_RFV
    all_codes = [c for codes in nhamcs.SYMPTOM_TO_RFV.values() for c in codes]
    assert all(len(c) == 4 and c.isdigit() for c in all_codes)
    # 1905 used to be both "back pain" and "swelling legs": one visit counted
    # as two symptoms.
    assert len(all_codes) == len(set(all_codes)), "a code maps to two symptoms"


def test_stata_files_are_converted_and_loaded(warehouse_at, tmp_path):
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame({
        "AGE": [82] * 40, "SEX": [2] * 40, "YEAR": [2022] * 40,
        "RFV1": [12250] * 40, "RFV13D": [1225] * 40, "PATWT": [5000.0] * 40,
        "ADMITHOS": [1] * 8 + [0] * 32, "TRANOTH": [0] * 40, "OBSHOS": [0] * 40,
        "DIEDED": [0] * 40, "UNUSED_COLUMN": [9] * 40,
    })
    dta = tmp_path / "ed2022-stata.dta"
    frame.to_stata(dta, write_index=False)

    nhamcs.load([str(dta)], min_n=30)
    result = lookup.admission_rate("dizziness", 82)
    assert result["rate_percent"] == pytest.approx(20.0, abs=0.1)

    slim = (tmp_path / "ed2022-stata.slim.csv").read_text().splitlines()[0]
    assert "UNUSED_COLUMN" not in slim, "only the columns we use are kept"


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
    """Files shaped like a real unzipped FAERS ASCII quarter.

    DRUG carries drug_seq, role_cod and prod_ai, because those three are what
    the loader now depends on: the sequence within the case, whether anyone
    actually suspects the drug, and the curated active ingredient.
    """
    folder = tmp_path / f"faers{quarter}"
    folder.mkdir()
    (folder / f"DEMO{quarter}.txt").write_text(
        "primaryid$caseid$fda_dt$age$age_cod\n"
        + "\n".join(f"{p}${c}${dt}${age}$YR" for p, c, dt, age in demo),
        encoding="utf-8",
    )
    (folder / f"DRUG{quarter}.txt").write_text(
        "primaryid$drug_seq$role_cod$drugname$prod_ai\n"
        + "\n".join(
            f"{pid}${seq}${role}${name}${ai}" for pid, seq, role, name, ai in drug
        ),
        encoding="utf-8",
    )
    (folder / f"REAC{quarter}.txt").write_text(
        "primaryid$pt\n" + "\n".join(f"{p}${r}" for p, r in reac),
        encoding="utf-8",
    )
    return str(folder)


def ps(pid, name, ai, seq=1):
    """A primary-suspect drug row."""
    return (pid, seq, "PS", name, ai)


def concomitant(pid, name, ai, seq=2):
    """A drug the patient happened to be taking. Nobody is blaming it."""
    return (pid, seq, "C", name, ai)


def test_faers_dedupes_by_caseid_keeping_the_latest_followup(warehouse_at, tmp_path):
    """Case 1 is reported three times. It must count once, or every number in
    the medication cards is inflated."""
    demo = [("100", "1", "20240101", "80"),
            ("101", "1", "20240201", "80"),
            ("102", "1", "20240301", "80")]
    drug = [ps(p, "COUMADIN", "WARFARIN SODIUM") for p, *_ in demo]
    reac = [(p, "Haemorrhage") for p, *_ in demo]
    faers.load([write_faers_quarter(tmp_path, demo, drug, reac)], min_reports=1)

    con = warehouse.connect(read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM faers_cases").fetchone()[0] == 1
        kept = con.execute("SELECT primaryid FROM faers_cases").fetchone()[0]
        assert kept == "102", "the latest follow-up is the one to keep"
    finally:
        con.close()


def test_faers_uses_prod_ai_not_the_reporters_free_text(warehouse_at, tmp_path):
    """Three spellings of one ingredient must collapse to one row, and the
    curated PROD_AI is what decides -- not the typed DRUGNAME."""
    demo, drug, reac = [], [], []
    for i, typed in enumerate(["COUMADIN", "warfarin sodium 5mg", "Warfarin"]):
        pid = str(1000 + i)
        demo.append((pid, pid, "20240101", "78"))
        drug.append(ps(pid, typed, "WARFARIN SODIUM"))
        reac.append((pid, "Haemorrhage"))
    faers.load([write_faers_quarter(tmp_path, demo, drug, reac)], min_reports=1)

    con = warehouse.connect(read_only=True)
    try:
        rows = con.execute("SELECT ingredient, senior_reports FROM faers_ingredients").fetchall()
    finally:
        con.close()
    assert rows == [("warfarin sodium", 3)], rows


def test_concomitant_drugs_are_not_counted_as_suspects(warehouse_at, tmp_path):
    """The bug this whole rewrite exists to fix.

    Every bleeding-on-warfarin report also lists the statin and the eye drops.
    Counting those as suspects invents a signal for whatever older people
    happen to take.
    """
    demo, drug, reac = [], [], []
    for i in range(40):
        pid = str(3000 + i)
        demo.append((pid, pid, "20240101", "79"))
        drug.append(ps(pid, "COUMADIN", "WARFARIN SODIUM"))
        drug.append(concomitant(pid, "LIPITOR", "ATORVASTATIN CALCIUM"))
        reac.append((pid, "Haemorrhage"))
    faers.load([write_faers_quarter(tmp_path, demo, drug, reac)], min_reports=1)

    assert lookup.drug_event_signal("warfarin sodium", "bleeding") is not None
    assert lookup.drug_event_signal("atorvastatin calcium", "bleeding") is None, (
        "a concomitant drug must never produce a signal"
    )
    assert "atorvastatin calcium" not in lookup.known_ingredients()


def test_combination_products_take_the_first_ingredient(warehouse_at, tmp_path):
    demo = [(str(4000 + i), str(4000 + i), "20240101", "77") for i in range(20)]
    drug = [ps(p, "EXFORGE", "AMLODIPINE BESYLATE\\VALSARTAN") for p, *_ in demo]
    reac = [(p, "Oedema peripheral") for p, *_ in demo]
    faers.load([write_faers_quarter(tmp_path, demo, drug, reac)], min_reports=1)

    assert "amlodipine besylate" in lookup.known_ingredients()


def test_faers_computes_a_shrunk_signal(warehouse_at, tmp_path):
    demo, drug, reac = [], [], []
    # 60 senior cases on warfarin reporting bleeding.
    for i in range(60):
        pid = str(1000 + i)
        demo.append((pid, pid, "20240101", "78"))
        drug.append(ps(pid, "COUMADIN", "WARFARIN SODIUM"))
        reac.append((pid, "Haemorrhage"))
    # 60 on an unrelated drug reporting something else, for the denominator.
    for i in range(60):
        pid = str(2000 + i)
        demo.append((pid, pid, "20240101", "78"))
        drug.append(ps(pid, "SYNTHROID", "LEVOTHYROXINE SODIUM"))
        reac.append((pid, "Nausea"))

    counts = faers.load([write_faers_quarter(tmp_path, demo, drug, reac)], min_reports=10)
    assert counts["senior_cases"] == 120
    assert counts["faers_ingredients"] == 2

    signal = lookup.drug_event_signal("warfarin sodium", "bleeding")
    assert signal is not None
    assert signal["n"] == 60
    assert signal["ror"] > 1
    assert signal["eb_rrr"] > 1
    assert signal["expected"] is not None
    assert signal["significant"] is True
    assert "primary-suspect" in signal["source"]


def test_shrinkage_pulls_a_thin_cell_back_toward_no_signal(warehouse_at, tmp_path):
    """A drug mentioned a handful of times must not top the table.

    The raw ratio for a 3-report cell is enormous; the shrunk estimate is the
    one a clinician should see, and it has to be much smaller.
    """
    demo, drug, reac = [], [], []
    for i in range(3):  # thin: 3 reports, all one event
        pid = str(5000 + i)
        demo.append((pid, pid, "20240101", "80"))
        drug.append(ps(pid, "RAREDRUG", "RARE INGREDIENT"))
        reac.append((pid, "Haemorrhage"))
    for i in range(300):  # the bulk of the database
        pid = str(6000 + i)
        demo.append((pid, pid, "20240101", "80"))
        drug.append(ps(pid, "SYNTHROID", "LEVOTHYROXINE SODIUM"))
        reac.append((pid, "Nausea"))

    faers.load([write_faers_quarter(tmp_path, demo, drug, reac)], min_reports=1)
    thin = lookup.drug_event_signal("rare ingredient", "bleeding")

    assert thin is not None
    assert thin["eb_rrr"] < thin["ror"], "shrinkage must move it toward the null"
    assert thin["n"] == 3


def test_faers_excludes_reports_from_under_65s(warehouse_at, tmp_path):
    demo = [(str(i), str(i), "20240101", "40") for i in range(30)]
    drug = [ps(str(i), "COUMADIN", "WARFARIN SODIUM") for i in range(30)]
    reac = [(str(i), "Haemorrhage") for i in range(30)]
    counts = faers.load([write_faers_quarter(tmp_path, demo, drug, reac)], min_reports=1)
    assert counts["senior_cases"] == 0


def test_a_pre_2014q3_quarter_is_refused_by_name(warehouse_at, tmp_path):
    """PROD_AI does not exist before 2014 Q3. Loading an older quarter silently
    would key every signal on free text again."""
    folder = tmp_path / "faers13q1"
    folder.mkdir()
    (folder / "DEMO13Q1.txt").write_text(
        "primaryid$caseid$fda_dt$age$age_cod\n1$1$20130101$80$YR", encoding="utf-8")
    (folder / "DRUG13Q1.txt").write_text(
        "primaryid$drug_seq$role_cod$drugname\n1$1$PS$COUMADIN", encoding="utf-8")
    (folder / "REAC13Q1.txt").write_text("primaryid$pt\n1$Haemorrhage", encoding="utf-8")

    with pytest.raises(ValueError, match="2014 Q3"):
        faers.load([str(folder)], min_reports=1)


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


# -- patient medicine -> FAERS ingredient ----------------------------------
def test_a_patients_medicine_resolves_to_the_faers_ingredient(warehouse_at, tmp_path):
    """PROD_AI carries the salt. Without resolution every real lookup misses,
    and the failure mode is that nothing looks broken -- the cards just never
    appear."""
    demo, drug, reac = [], [], []
    for i in range(40):
        pid = str(7000 + i)
        demo.append((pid, pid, "20240101", "79"))
        drug.append(ps(pid, "COUMADIN", "WARFARIN SODIUM"))
        reac.append((pid, "Haemorrhage"))
    for i in range(40):
        pid = str(8000 + i)
        demo.append((pid, pid, "20240101", "79"))
        drug.append(ps(pid, "SYNTHROID", "LEVOTHYROXINE SODIUM"))
        reac.append((pid, "Nausea"))
    faers.load([write_faers_quarter(tmp_path, demo, drug, reac)], min_reports=5)

    assert lookup.resolve_ingredient("warfarin") == "warfarin sodium"
    assert lookup.resolve_ingredient("Coumadin") == "warfarin sodium"
    assert lookup.resolve_ingredient("levothyroxine") == "levothyroxine sodium"
    assert lookup.resolve_ingredient("something nobody takes") is None


def test_the_medication_card_appears_for_a_real_patient_med_list(warehouse_at, tmp_path, client):
    """Wei's list says 'Warfarin'; FAERS says 'warfarin sodium'. The card has
    to show up anyway."""
    demo, drug, reac = [], [], []
    for i in range(60):
        pid = str(9000 + i)
        demo.append((pid, pid, "20240101", "84"))
        drug.append(ps(pid, "COUMADIN", "WARFARIN SODIUM"))
        reac.append((pid, "Haemorrhage"))
    for i in range(60):
        pid = str(9500 + i)
        demo.append((pid, pid, "20240101", "84"))
        drug.append(ps(pid, "SYNTHROID", "LEVOTHYROXINE SODIUM"))
        reac.append((pid, "Nausea"))
    faers.load([write_faers_quarter(tmp_path, demo, drug, reac)], min_reports=10)

    wei = store.get_senior("sen_chen")
    assert any(m.ingredient == "warfarin" for m in wei.medications)

    cards = evidence.faers_cards(probe(["bleeding"], senior_id="sen_chen"), wei)
    assert cards, "a real signal for a med the patient takes must surface"
    assert not cards[0].source.startswith("MOCK")
    assert "Warfarin Sodium" in cards[0].title
