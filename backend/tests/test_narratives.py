"""NEISS narratives: parsing, expansion, and similar-case retrieval.

The narrative is the reason NEISS is worth using at all. "Fell down the stairs,
struck head, on Coumadin" and "slipped in the kitchen" are the same body part
and a completely different night -- only the free text knows which.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import retrieval
from app.config import get_settings
from app.datasets import lookup, neiss, warehouse
from app.datasets.narratives import expand, features
from app.main import app

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.post("/demo/reset")
        yield c


@pytest.fixture
def warehouse_at(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "test.duckdb"))
    get_settings.cache_clear()
    lookup.clear_cache()
    yield tmp_path
    get_settings.cache_clear()
    lookup.clear_cache()


# -- expansion -------------------------------------------------------------
def test_shorthand_becomes_something_an_embedding_can_use():
    out = expand("80YOF GLF AT HOME STRUCK HEAD ON NIGHTSTAND, ON COUMADIN, DX SDH")
    assert "80 year old woman" in out
    assert "ground level fall" in out
    assert "subdural haematoma" in out
    assert "GLF" not in out.upper().split()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("92YOM S/P FALL C/O HIP PAIN", "92 year old man"),
        ("77 YO F FELL", "77 year old woman"),
        ("85YO PT FOUND DOWN", "85 year old patient"),
    ],
)
def test_age_and_sex_are_read_out(raw, expected):
    assert expected in expand(raw)


def test_expansion_is_safe_on_junk():
    assert expand(None) == ""
    assert expand("") == ""
    assert expand("   ") == ""


# -- features --------------------------------------------------------------
@pytest.mark.parametrize(
    "narrative,flag",
    [
        ("80YOF FELL STRUCK HEAD ON FLOOR", "head_strike"),
        ("79YOM FELL DX CHI", "head_strike"),
        ("81YOF FOUND DOWN AT HOME, LOC", "loss_of_consciousness"),
        ("88YOM SYNCOPE THEN FELL", "loss_of_consciousness"),
        ("75YOF FELL, ON COUMADIN", "anticoagulant"),
        ("90YOF FELL, TAKES BLOOD THINNER", "anticoagulant"),
    ],
)
def test_clinically_important_flags_are_found(narrative, flag):
    assert features(narrative)[flag] is True


def test_absent_flags_stay_false():
    f = features("70YOM TRIPPED ON RUG, C/O WRIST PAIN, DX WRIST FX")
    assert f["head_strike"] is False
    assert f["loss_of_consciousness"] is False
    assert f["anticoagulant"] is False


@pytest.mark.parametrize(
    "narrative,mechanism",
    [
        ("80YOF FELL DOWN STAIRS", "stairs"),
        ("82YOM FELL GETTING OUT OF TUB", "bathroom"),
        ("91YOF ROLLED OUT OF BED", "bed"),
        ("77YOM FELL FROM LADDER", "ladder_or_height"),
        ("85YOF FOUND ON FLOOR BY DAUGHTER", "unwitnessed"),
        ("79YOM SLIPPED ON ICE ON SIDEWALK", "outdoors"),
        ("88YOF INJURED ARM SOMEHOW", "unspecified"),
    ],
)
def test_mechanism_is_classified(narrative, mechanism):
    assert features(narrative)["mechanism"] == mechanism


# -- loading ---------------------------------------------------------------
def write_neiss(tmp_path, rows, two_narrative_fields=False, name="neiss.csv"):
    path = tmp_path / name
    if two_narrative_fields:
        header = "age,sex,body_part,diag,disposition,weight,narr1,narr2"
        lines = [header] + [
            f"{a},1,{b},{d},{disp},{w},{n1},{n2}" for a, b, d, disp, w, n1, n2 in rows
        ]
    else:
        header = "age,sex,body_part,diag,disposition,weight,narrative"
        lines = [header] + [
            f"{a},1,{b},{d},{disp},{w},{n}" for a, b, d, disp, w, n in rows
        ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def test_the_2019_schema_break_is_handled(warehouse_at, tmp_path):
    """Before 2019 the text is in two short fields. Reading only the first
    silently loses half of every narrative -- and every flag derived from it."""
    rows = [
        (80, 75, 62, 4, 100, "80YOF FELL AT HOME", "STRUCK HEAD ON COUMADIN")
        for _ in range(40)
    ]
    neiss.load([write_neiss(tmp_path, rows, two_narrative_fields=True)], min_n=10)

    con = warehouse.connect(read_only=True)
    try:
        flags = con.execute(
            "SELECT head_strike, on_anticoagulant FROM neiss_narratives LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    assert flags == (True, True), "the second field has to be concatenated"


def test_rates_are_cut_by_the_narrative_derived_fields(warehouse_at, tmp_path):
    """A head strike on a blood thinner is the cut that matters clinically."""
    rows = [(80, 75, 62, 4, 100, "80YOF FELL STRUCK HEAD ON COUMADIN") for _ in range(38)]
    rows += [(80, 75, 62, 1, 100, "80YOF FELL STRUCK HEAD ON COUMADIN") for _ in range(2)]
    rows += [(80, 75, 62, 1, 100, "80YOF TRIPPED ON RUG HURT WRIST") for _ in range(35)]
    rows += [(80, 75, 62, 4, 100, "80YOF TRIPPED ON RUG HURT WRIST") for _ in range(5)]
    neiss.load([write_neiss(tmp_path, rows)], min_n=10)

    struck = lookup.fall_outcome(head_strike=True, on_anticoagulant=True)
    other = lookup.fall_outcome(head_strike=False, on_anticoagulant=False)

    assert struck["rate_percent"] == pytest.approx(95.0, abs=0.5)
    assert other["rate_percent"] == pytest.approx(12.5, abs=0.5)
    assert struck["rate_percent"] > other["rate_percent"], "this is the clinical point"


def test_an_unmatched_cut_returns_none_rather_than_a_wrong_number(warehouse_at, tmp_path):
    rows = [(80, 75, 62, 4, 100, "80YOF FELL STRUCK HEAD") for _ in range(40)]
    neiss.load([write_neiss(tmp_path, rows)], min_n=10)
    assert lookup.fall_outcome(mechanism="ladder_or_height") is None


def test_short_narratives_are_kept_out_of_the_retrieval_corpus(warehouse_at, tmp_path):
    rows = [(80, 75, 62, 4, 100, "FELL") for _ in range(40)]
    neiss.load([write_neiss(tmp_path, rows)], min_n=10)
    assert lookup.narrative_rows() == [], "nothing to embed in a 4-character note"


# -- retrieval -------------------------------------------------------------
def test_similar_cases_are_retrieved_and_their_outcome_reported(warehouse_at, tmp_path):
    rows = [
        (80, 75, 62, 4, 100, "80YOF FELL DOWN STAIRS STRUCK HEAD ON COUMADIN DX SDH")
        for _ in range(30)
    ]
    rows += [
        (80, 75, 62, 1, 100, "80YOF TRIPPED ON RUG C/O WRIST PAIN DX WRIST FX")
        for _ in range(30)
    ]
    neiss.load([write_neiss(tmp_path, rows)], min_n=10)

    retrieval.store.reset()
    added = retrieval.ingest_neiss_narratives(lookup.narrative_rows())
    assert added == 2, "60 cases, two distinct narratives, two chunks"

    result = retrieval.similar_cases("I fell down the stairs and hit my head", k=5)
    # Only two distinct narratives exist, so two is the honest answer even
    # though k=5 was asked for -- duplicates are collapsed.
    assert result["matched"] == 2
    assert result["cases_represented"] == 60, "the case count must not be lost"
    assert result["admitted_share"] == 0.5, "30 of 60 were admitted"
    assert result["cases"][0]["mechanism"] == "stairs", (
        f"the stairs case should rank first: {result['cases'][0]['text']}"
    )


def test_the_outcome_is_not_in_the_embedded_text(warehouse_at, tmp_path):
    """Otherwise 'admitted' pulls every query toward admitted cases and the
    rate we report becomes circular."""
    rows = [(80, 75, 62, 4, 100, "80YOF FELL DOWN STAIRS STRUCK HEAD") for _ in range(20)]
    neiss.load([write_neiss(tmp_path, rows)], min_n=10)

    retrieval.store.reset()
    retrieval.ingest_neiss_narratives(lookup.narrative_rows())
    for chunk in retrieval.store.chunks.values():
        assert "admitted" not in chunk.text.lower()
        assert chunk.metadata["admitted_cases"] == chunk.metadata["cases"]


def test_similar_case_endpoint_is_empty_without_the_warehouse(client):
    body = client.get("/evidence/similar-cases", params={"text": "I fell"}).json()
    assert body["matched"] == 0
    assert body["admitted_share"] is None


def test_reindex_reports_how_many_real_cases_it_indexed(client):
    body = client.post("/retrieval/reindex").json()
    assert body["neiss_cases"] == 0, "no warehouse in tests"
    assert body["indexed_chunks"] > 0, "guidelines and history still index"


def test_identical_narratives_are_collapsed(warehouse_at, tmp_path):
    """NEISS shorthand repeats heavily. Five copies of one sentence looks
    broken next to a clinician, however correct the retrieval was."""
    rows = [(80, 75, 62, 4, 100, "80YOF FELL DOWN STAIRS STRUCK HEAD ON COUMADIN")
            for _ in range(50)]
    rows += [(80, 75, 62, 1, 100, "80YOF TRIPPED ON RUG C/O WRIST PAIN")
             for _ in range(50)]
    neiss.load([write_neiss(tmp_path, rows)], min_n=10)

    retrieval.store.reset()
    retrieval.ingest_neiss_narratives(lookup.narrative_rows())
    result = retrieval.similar_cases("I fell down the stairs and hit my head", k=5)

    texts = [c["text"] for c in result["cases"]]
    assert len(texts) == len(set(texts)), f"duplicates returned: {texts}"
    assert sum(c["cases"] for c in result["cases"]) == 100
