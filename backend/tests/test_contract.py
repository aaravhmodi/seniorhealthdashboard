"""Contract tests.

These are the tests the UI teammate depends on: if one of them goes red, the
shape they built against changed. Treat a failure here as a team-wide event.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import ActionLevel


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.post("/demo/reset")
        yield c


# -- shape -----------------------------------------------------------------
def test_health_and_contract(client):
    health = client.get("/healthz").json()
    assert health["status"] == "ok"
    assert health["seeded_seniors"] == 4

    contract = client.get("/contract").json()
    assert contract["contract_version"] == "0.1.0"
    assert set(contract["action_levels"]) == {"1", "2", "3", "4"}


def test_seed_history_is_present(client):
    seniors = client.get("/seniors").json()
    assert {s["id"] for s in seniors} == {
        "sen_rosa", "sen_chen", "sen_walter", "sen_henriette"
    }

    rosa = client.get("/seniors/sen_rosa").json()
    assert rosa["preferred_language"] == "es"
    assert len(rosa["medications"]) == 4
    assert rosa["consent"]["share_with:cg_priya"] is True

    checkins = client.get("/seniors/sen_rosa/checkins?limit=50").json()
    assert len(checkins) == 14, "14 days of seeded history backs the baseline card"


def test_checkin_response_shape(client):
    body = client.post(
        "/checkins",
        json={
            "senior_id": "sen_rosa",
            "source": "text",
            "language": "es",
            "text": "Hoy me siento bien.",
            "client_ref": "ui-123",
        },
    ).json()

    assert set(body) == {"checkin", "evaluation", "notifications"}
    assert body["checkin"]["client_ref"] == "ui-123"
    ev = body["evaluation"]
    for field in (
        "level", "level_label", "red_flags", "evidence", "explanation",
        "explanation_en", "recommended_actions", "confidence",
        "requires_human_review", "engine_version",
    ):
        assert field in ev, f"UI depends on evaluation.{field}"


def test_unknown_senior_is_404(client):
    assert client.post(
        "/checkins", json={"senior_id": "sen_nobody", "text": "hello"}
    ).status_code == 404


def test_audio_url_without_deepgram_configured_fails_loudly(client):
    """A dropped transcription must never look like a patient who said nothing."""
    resp = client.post(
        "/checkins", json={"senior_id": "sen_rosa", "audio_url": "https://x/a.wav"}
    )
    assert resp.status_code == 503
    assert "DEEPGRAM_API_KEY" in resp.json()["detail"]


# -- the safety layer ------------------------------------------------------
def test_stroke_signs_force_911(client):
    ev = client.post(
        "/checkins",
        json={
            "senior_id": "sen_walter",
            "source": "voice",
            "language": "en",
            "text": "My face is drooping and my arm is weak on one side, slurred speech.",
        },
    ).json()["evaluation"]

    assert ev["level"] == ActionLevel.CALL_911
    assert "stroke_fast" in {f["code"] for f in ev["red_flags"]}
    assert ev["requires_human_review"] is True


def test_atypical_cardiac_without_chest_pain_reaches_er(client):
    """The geriatric miss we exist for: breathless plus nausea, no chest pain."""
    ev = client.post(
        "/checkins",
        json={
            "senior_id": "sen_rosa",
            "source": "voice",
            "language": "es",
            "text": "Me falta el aire y tengo nausea, y estoy sudando frio.",
            "transcript_confidence": 0.93,
        },
    ).json()["evaluation"]

    assert ev["level"] >= ActionLevel.GO_TO_ER
    assert "cardiac_acs" in {f["code"] for f in ev["red_flags"]}


def test_new_confusion_with_urinary_symptoms_reaches_er(client):
    ev = client.post(
        "/checkins",
        json={
            "senior_id": "sen_chen",
            "language": "en",
            "text": "He is suddenly confused today and says burning when i pee.",
        },
    ).json()["evaluation"]

    flags = {f["code"] for f in ev["red_flags"]}
    assert "altered_mental_status" in flags
    assert ev["level"] >= ActionLevel.GO_TO_ER


def test_fall_on_anticoagulant_is_flagged(client):
    ev = client.post(
        "/checkins",
        json={
            "senior_id": "sen_chen",
            "language": "en",
            "text": "I fell in the bathroom this morning and hit my head.",
        },
    ).json()["evaluation"]

    flag = next(f for f in ev["red_flags"] if f["code"] == "fall_head_injury")
    assert any("anticoagulated" in m for m in flag["matched_on"])


def test_low_confidence_escalates_one_rung(client):
    ev = client.post(
        "/checkins",
        json={
            "senior_id": "sen_rosa",
            "source": "voice",
            "language": "es",
            "text": "mal",
            "transcript_confidence": 0.2,
        },
    ).json()["evaluation"]

    assert ev["escalated_for_uncertainty"] is True
    assert ev["requires_human_review"] is True


def test_quiet_day_stays_at_level_one(client):
    ev = client.post(
        "/checkins",
        json={
            "senior_id": "sen_walter",
            "language": "en",
            "text": "Slept fine, no trouble breathing.",
            "transcript_confidence": 0.95,
        },
    ).json()["evaluation"]

    assert ev["level"] == ActionLevel.LOG
    assert ev["red_flags"] == []


def test_rules_are_never_overridden_downward(client):
    """Evidence may raise the level. Nothing may lower a rule's floor."""
    for scenario in client.get("/demo/scenarios").json():
        body = client.post(
            "/checkins",
            json={
                "senior_id": scenario["senior_id"],
                "source": scenario["source"],
                "language": scenario["language"],
                "text": scenario["text"],
            },
        ).json()
        flags = body["evaluation"]["red_flags"]
        if flags:
            floor = max(f["forces_level"] for f in flags)
            assert body["evaluation"]["level"] >= floor, scenario["name"]


def test_demo_scenarios_land_on_their_advertised_level(client):
    """If this goes red, the demo script is lying. Fix one or the other."""
    for scenario in client.get("/demo/scenarios").json():
        client.post("/demo/reset")
        ev = client.post(
            "/checkins",
            json={
                "senior_id": scenario["senior_id"],
                "source": scenario["source"],
                "language": scenario["language"],
                "text": scenario["text"],
                "transcript_confidence": 0.93,
            },
        ).json()["evaluation"]
        assert ev["level"] == scenario["expect_level"], (
            f"{scenario['name']}: expected {scenario['expect_level']}, got {ev['level']}"
        )


# -- language --------------------------------------------------------------
@pytest.mark.parametrize("senior_id", ["sen_rosa", "sen_chen", "sen_walter", "sen_henriette"])
def test_explanation_follows_submitted_language(client, senior_id):
    ev = client.post(
        "/checkins",
        json={
            "senior_id": senior_id,
            "language": "en",
            "text": "I have chest pain and trouble breathing.",
        },
    ).json()["evaluation"]

    assert "emergency" in ev["explanation"]
    assert "emergency department" in ev["explanation_en"], "clinician view stays English"


# -- caregiver messaging ---------------------------------------------------
def test_no_phi_in_the_caregiver_text(client):
    body = client.post(
        "/checkins",
        json={
            "senior_id": "sen_rosa",
            "language": "es",
            "text": "Me falta el aire y tengo nausea, y estoy sudando frio.",
        },
    ).json()

    assert body["notifications"], "level 3 must notify the daughter"
    text = body["notifications"][0]["body"].lower()
    for leak in ("chest", "nausea", "diagnos", "lisinopril", "mg"):
        assert leak not in text
    assert "http" in text, "the detail lives behind a consented link"


def test_level_one_does_not_wake_the_family(client):
    body = client.post(
        "/checkins",
        json={"senior_id": "sen_walter", "language": "en", "text": "Feeling steady today."},
    ).json()
    assert body["notifications"] == []


# -- baseline, timeline, handoff -------------------------------------------
def test_baseline_picks_up_the_two_week_drift(client):
    baseline = client.get("/seniors/sen_rosa/baseline").json()
    assert baseline["checkin_count"] == 14
    assert "dizziness" in baseline["trending_up"]
    assert "poor appetite" in baseline["trending_up"]


def test_timeline_is_newest_first(client):
    timeline = client.get("/seniors/sen_rosa/timeline").json()
    ats = [e["at"] for e in timeline["entries"]]
    assert ats == sorted(ats, reverse=True)
    assert timeline["baseline"]["window_days"] == 14


def test_handoff_packet_is_built_at_level_three(client):
    client.post(
        "/checkins",
        json={
            "senior_id": "sen_rosa",
            "language": "es",
            "text": "Me falta el aire y tengo nausea, y estoy sudando frio.",
        },
    )
    packet = client.get("/handoff/sen_rosa").json()

    assert packet["level"] >= ActionLevel.GO_TO_ER
    assert "79-year-old" in packet["patient_summary_en"]
    assert "Furosemide" in packet["patient_summary_en"]
    assert "penicillin" in packet["allergies"]
    assert packet["caregiver_contact"]["relationship"] == "daughter"
    assert "Decision support only" in packet["disclaimer"]


def test_handoff_404s_before_anything_urgent(client):
    assert client.get("/handoff/sen_walter").status_code == 404


# -- events ----------------------------------------------------------------
def test_websocket_streams_the_checkin(client):
    with client.websocket_connect("/events") as socket:
        assert socket.receive_json()["type"] == "ready"
        client.post(
            "/checkins",
            json={"senior_id": "sen_walter", "language": "en", "text": "I feel steady."},
        )
        types = [socket.receive_json()["type"] for _ in range(2)]
        assert types == ["checkin.created", "evaluation.completed"]


def test_level_change_emits_its_own_event(client):
    client.post(
        "/checkins",
        json={"senior_id": "sen_walter", "language": "en", "text": "All good today."},
    )
    client.post(
        "/checkins",
        json={
            "senior_id": "sen_walter",
            "language": "en",
            "text": "My face is drooping and my arm is weak on one side.",
        },
    )
    types = [e["type"] for e in client.get("/events/recent").json()]
    assert "level.changed" in types
    assert "handoff.ready" in types


# -- linq inbound ----------------------------------------------------------
def test_linq_photo_webhook_records_a_pending_med_update(client):
    resp = client.post(
        "/webhooks/linq",
        json={
            "thread_id": "thread_rosa_family",
            "from_phone_e164": "+16175550142",
            "text": "Here are her pill bottles",
            "media_urls": ["https://example.test/bottles.jpg"],
        },
    )
    body = resp.json()
    # Subset, not equality: the webhook response gained `intent` and `actions`
    # when the family side went live, and additive fields are allowed.
    assert body["ok"] is True
    assert body["senior_id"] == "sen_rosa"
    assert body["medications_added"] == 0
    assert "media_recorded" in body["actions"]

    summaries = [e["type"] for e in client.get("/seniors/sen_rosa/timeline").json()["entries"]]
    assert "meds.updated" in summaries


def test_linq_webhook_for_unknown_thread_is_404(client):
    assert client.post(
        "/webhooks/linq",
        json={"thread_id": "thread_nobody", "from_phone_e164": "+10000000000"},
    ).status_code == 404
