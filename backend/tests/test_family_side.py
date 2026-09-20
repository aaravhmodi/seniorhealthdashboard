"""The family side: circles, tone, acknowledgement, escalation, teach-back.

Everything here runs in mock mode, so no text leaves the building and the
whole escalation clock is exercised without waiting ten minutes. The live
path has its own smoke script.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import caretone, circle, followup, linq, linq_events
from app.config import get_settings
from app.main import app
from app.schemas import ActionLevel
from app.store import now, store


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.post("/demo/reset")
        yield c


def _enroll(client, senior_id="sen_rosa"):
    return client.post(
        "/circle/enroll",
        json={
            "senior_id": senior_id,
            "include_patient": True,
            "patient_phone_e164": "+16175550100",
            "caregivers": [
                {
                    "name": "Priya Mendez",
                    "phone_e164": "+16175550142",
                    "relationship": "daughter",
                    "escalation_order": 0,
                },
                {
                    "name": "Marco Mendez",
                    "phone_e164": "+16175550178",
                    "relationship": "son",
                    "escalation_order": 1,
                },
            ],
        },
    )


# --------------------------------------------------------------------------
# Tone
# --------------------------------------------------------------------------
@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_every_alert_body_passes_the_tone_contract(level):
    body = caretone.alert_body(
        "Rosa", level, "https://carepath.test/c/sen_rosa", caregiver_name="Priya Mendez"
    )
    result = caretone.lint(body, level)
    assert result.ok, result.problems


def test_alerts_never_shout_and_never_falsely_reassure():
    for level in (1, 2, 3, 4):
        body = caretone.alert_body("Rosa", level, "https://carepath.test/c/x")
        assert "!" not in body or level == 1
        for phrase in caretone.FALSE_REASSURANCE:
            assert phrase not in body.lower()


def test_urgency_never_becomes_volume():
    """Level 4 must not be level 3 in capitals. It earns urgency with words."""
    three = caretone.alert_body("Rosa", 3, "https://carepath.test/c/x")
    four = caretone.alert_body("Rosa", 4, "https://carepath.test/c/x")
    assert three != four
    assert four.upper() != four
    assert "nine one one" in four  # spoken-out digits, per the persona rules


def test_every_family_message_carries_a_link_and_no_phi():
    link = "https://carepath.test/c/sen_rosa"
    bodies = [
        caretone.alert_body("Rosa", 3, link),
        caretone.escalation_body("Rosa", "Priya Mendez", link),
        caretone.followup_body("Rosa", 24, link),
        caretone.teachback_miss_body("Rosa", link),
        caretone.welcome_body("Rosa", ["Priya (daughter)"], link),
        caretone.status_reply_body("Rosa", 2, "earlier today", link),
    ]
    for body in bodies:
        assert link in body
        assert not linq.contains_phi(body)


def test_the_transport_refuses_a_body_with_clinical_detail():
    """Last gate before bytes leave: even a hand-rolled body is stopped."""
    assert linq.contains_phi("Rosa has chest pain, go to the ER")
    assert not linq.contains_phi("Rosa checked in and needs a call today")


# --------------------------------------------------------------------------
# Inbound understanding
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected",
    [
        ("where is dad??", "status"),
        ("Where's Mom right now", "status"),
        ("hows she doing", "status"),
        ("STATUS", "status"),
        ("any news?", "status"),
        ("ok", "ack"),
        ("Got it", "ack"),
        ("on my way", "ack"),
        ("call me", "call"),
        ("STOP", "stop"),
        ("help", "help"),
        ("I took her to the hospital this afternoon and they said", None),
    ],
)
def test_caregiver_intent(text, expected):
    assert caretone.detect_intent(text) == expected


def test_a_long_message_is_not_mistaken_for_an_acknowledgement():
    """A false ack means we stop escalating. That is the expensive mistake."""
    assert not caretone.is_acknowledgement(
        "ok so I called the clinic and they said to wait until Monday"
    )


def test_normalize_flattens_a_real_linq_message_event():
    payload = {
        "event": "message.received",
        "data": {
            "chat": {"id": "chat-123", "is_group": True},
            "id": "msg-9",
            "direction": "inbound",
            "sender_handle": {"handle": "+16175550142", "is_me": False},
            "parts": [{"type": "text", "value": "where is mom"}],
            "sent_at": "2026-09-19T23:52:51Z",
        },
    }
    inbound = linq_events.normalize(payload)
    assert inbound.thread_id == "chat-123"
    assert inbound.from_phone_e164 == "+16175550142"
    assert inbound.text == "where is mom"


def test_normalize_flattens_a_tapback():
    inbound = linq_events.normalize(
        {
            "event": "reaction.added",
            "data": {
                "chat_id": "chat-123",
                "message_id": "msg-9",
                "reaction_type": "like",
                "from_handle": {"handle": "+16175550142"},
            },
        }
    )
    assert inbound.reaction == "like"
    assert inbound.reacted_to == "msg-9"
    assert linq_events.is_ack_reaction(inbound.reaction)


def test_an_event_we_do_not_act_on_is_ignored_not_errored():
    """Linq retries a non-2xx for 25 minutes. Silence must be cheap."""
    assert linq_events.normalize({"event": "message.delivered", "data": {}}) is None
    assert linq_events.normalize({"nonsense": True}) is None


def test_our_own_sprint_zero_shape_still_parses():
    inbound = linq_events.normalize(
        {"thread_id": "thread_rosa_family", "from_phone_e164": "+16175550142", "text": "ok"}
    )
    assert inbound.text == "ok"


# --------------------------------------------------------------------------
# Enrollment
# --------------------------------------------------------------------------
def test_enrolling_numbers_opens_one_group_chat(client):
    body = _enroll(client).json()
    assert body["status"] == "active"
    assert body["chat_id"]
    roles = sorted(m["role"] for m in body["members"])
    assert "patient" in roles
    assert roles.count("caregiver") == 2


def test_demo_reminder_is_sent_to_the_senior_and_done_is_recorded(client):
    response = client.post(
        "/reminders/send",
        json={"senior_id": "sen_rosa", "kind": "meds", "language": "en"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["mocked"] is True
    assert set(body["recipients"]) == {"+16175550100", "+16175550142"}
    assert "Reply DONE" in body["body"]

    inbound = client.post(
        "/webhooks/linq",
        json={"thread_id": "", "from_phone_e164": "+16175550100", "text": "DONE"},
    )
    assert "reminder_acknowledged" in inbound.json()["actions"]
    assert next(iter(store.reminders.values()))["status"] == "acknowledged"


def test_unrecognized_caregiver_message_gets_a_caring_reply(client):
    response = client.post(
        "/webhooks/linq",
        json={"thread_id": "", "from_phone_e164": "+16175550142", "text": "I am worried and need you with me"},
    )
    assert response.status_code == 200
    assert "conversation_replied" in response.json()["actions"]


def test_enrolling_the_same_number_twice_does_not_double_the_texts(client):
    _enroll(client)
    first = len(client.get("/seniors/sen_rosa").json()["caregivers"])
    _enroll(client)
    assert len(client.get("/seniors/sen_rosa").json()["caregivers"]) == first


def test_a_caregiver_who_did_not_consent_is_not_in_the_circle(client):
    resp = client.post(
        "/circle/enroll",
        json={
            "senior_id": "sen_walter",
            "include_patient": False,
            "caregivers": [
                {"name": "Nosy Neighbor", "phone_e164": "+16175559999", "consent": False}
            ],
        },
    )
    handles = [m["handle"] for m in resp.json()["members"]]
    assert "+16175559999" not in handles


# --------------------------------------------------------------------------
# Alerts, acknowledgement, escalation
# --------------------------------------------------------------------------
def test_an_ed_level_checkin_alerts_the_circle(client):
    _enroll(client)
    client.post(
        "/checkins",
        json={"senior_id": "sen_rosa", "text": "Me falta el aire y tengo nausea, y estoy sudando frio.",
              "language": "es", "source": "text"},
    )
    alerts = client.get("/circle/sen_rosa/alerts").json()
    assert alerts
    assert alerts[0]["level"] >= 3
    assert alerts[0]["pending_order"], "there must be someone left to escalate to"


def test_a_tapback_acknowledges_the_alert_and_stops_the_clock(client):
    _enroll(client)
    client.post(
        "/checkins",
        json={"senior_id": "sen_rosa", "text": "Me falta el aire y tengo nausea, y estoy sudando frio.",
              "language": "es", "source": "text"},
    )
    alert = client.get("/circle/sen_rosa/alerts").json()[0]
    chat_id = client.get("/circle/sen_rosa").json()["chat_id"]

    resp = client.post(
        "/webhooks/linq",
        json={
            "event": "reaction.added",
            "data": {
                "chat_id": chat_id,
                "message_id": alert["message_id"],
                "reaction_type": "like",
                "from_handle": {"handle": "+16175550142"},
            },
        },
    )
    assert "acknowledged" in resp.json()["actions"]

    after = client.get("/circle/sen_rosa/alerts").json()[0]
    assert after["resolved"] is True
    assert after["escalate_after"] is None
    assert after["acks"][0]["via"] == "tapback"


def test_a_short_text_reply_also_acknowledges(client):
    _enroll(client)
    client.post("/checkins", json={"senior_id": "sen_rosa", "text": "Me falta el aire y tengo nausea, y estoy sudando frio.", "language": "es", "source": "text"})
    chat_id = client.get("/circle/sen_rosa").json()["chat_id"]
    resp = client.post(
        "/webhooks/linq",
        json={
            "event": "message.received",
            "data": {
                "chat": {"id": chat_id},
                "direction": "inbound",
                "sender_handle": {"handle": "+16175550142"},
                "parts": [{"type": "text", "value": "got it"}],
            },
        },
    )
    assert "acknowledged" in resp.json()["actions"]


def test_silence_escalates_to_the_second_caregiver(client):
    _enroll(client)
    client.post("/checkins", json={"senior_id": "sen_rosa", "text": "Me falta el aire y tengo nausea, y estoy sudando frio.", "language": "es", "source": "text"})
    alert = client.get("/circle/sen_rosa/alerts").json()[0]

    escalated = client.post(f"/circle/alerts/{alert['id']}/escalate").json()
    assert escalated["escalations"] == 1
    assert len(escalated["notified"]) == 2
    assert escalated["resolved"] is False

    types = [e["type"] for e in client.get("/seniors/sen_rosa/timeline").json()["entries"]]
    assert "alert.escalated" in types


def test_an_exhausted_chain_leaves_the_alert_open(client):
    """Pretending an unanswered level-3 is resolved is the worst failure here."""
    _enroll(client)
    client.post("/checkins", json={"senior_id": "sen_rosa", "text": "Me falta el aire y tengo nausea, y estoy sudando frio.", "language": "es", "source": "text"})
    alert_id = client.get("/circle/sen_rosa/alerts").json()[0]["id"]
    client.post(f"/circle/alerts/{alert_id}/escalate")
    final = client.post(f"/circle/alerts/{alert_id}/escalate").json()
    assert final["resolved"] is False
    assert final["pending_order"] == []
    assert final["escalate_after"] is None


def test_a_level_one_day_starts_no_escalation_clock(client):
    """Nobody gets woken up for not having read good news."""
    _enroll(client)
    client.post("/checkins", json={"senior_id": "sen_rosa", "text": "Hoy me siento bien.", "language": "es", "source": "text"})
    alerts = client.get("/circle/sen_rosa/alerts").json()
    if alerts:
        assert all(a["escalate_after"] is None for a in alerts if a["level"] == 1)


def test_where_is_dad_gets_an_answer(client):
    _enroll(client)
    client.post("/checkins", json={"senior_id": "sen_rosa", "text": "Tengo mareo fuerte cuando me levanto.", "language": "es", "source": "text"})
    chat_id = client.get("/circle/sen_rosa").json()["chat_id"]
    resp = client.post(
        "/webhooks/linq",
        json={
            "event": "message.received",
            "data": {
                "chat": {"id": chat_id},
                "direction": "inbound",
                "sender_handle": {"handle": "+16175550142"},
                "parts": [{"type": "text", "value": "where is mom?"}],
            },
        },
    )
    body = resp.json()
    assert body["intent"] == "status"
    assert "status_sent" in body["actions"]


def test_stop_withdraws_consent_in_the_thread(client):
    _enroll(client)
    chat_id = client.get("/circle/sen_rosa").json()["chat_id"]
    client.post(
        "/webhooks/linq",
        json={
            "event": "message.received",
            "data": {
                "chat": {"id": chat_id},
                "direction": "inbound",
                "sender_handle": {"handle": "+16175550142"},
                "parts": [{"type": "text", "value": "STOP"}],
            },
        },
    )
    senior = client.get("/seniors/sen_rosa").json()
    priya = next(c for c in senior["caregivers"] if c["phone_e164"] == "+16175550142")
    assert senior["consent"][f"share_with:{priya['id']}"] is False


# --------------------------------------------------------------------------
# Follow-up and teach-back
# --------------------------------------------------------------------------
def test_an_ed_level_checkin_schedules_the_configured_follow_up(client):
    """The cadence is FOLLOWUP_HOURS and the code does not care how long it
    is; it ships as [24], which is the check that carries the teach-back."""
    client.post("/checkins", json={"senior_id": "sen_rosa", "text": "Me falta el aire y tengo nausea, y estoy sudando frio.", "language": "es", "source": "text"})
    jobs = client.get("/seniors/sen_rosa/followups").json()
    assert [j["hours_after"] for j in jobs] == get_settings().followup_hours
    assert jobs[0]["hours_after"] == 24.0
    assert jobs[0]["kind"] == "teachback"


def test_only_one_follow_up_ships_by_default(client):
    """The 72-hour check was cut: it bought nothing the 24-hour check does
    not, and it depended on state surviving three days in a process that does
    not. FOLLOWUP_HOURS brings it back without a code change."""
    assert get_settings().followup_hours == [24.0]
    client.post("/checkins", json={"senior_id": "sen_rosa", "text": "Me falta el aire y tengo nausea, y estoy sudando frio.", "language": "es", "source": "text"})
    jobs = client.get("/seniors/sen_rosa/followups").json()
    assert len(jobs) == 1
    assert jobs[0]["kind"] == "teachback"


def test_a_longer_cadence_still_works_if_configured(client, monkeypatch):
    """Nothing about the cut is hard-coded; the mechanism stayed generic."""
    monkeypatch.setenv("FOLLOWUP_HOURS", "[24, 72]")
    get_settings.cache_clear()
    try:
        client.post("/checkins", json={"senior_id": "sen_chen", "text": "I fell in the bathroom this morning and hit my head.", "language": "en", "source": "text"})
        jobs = client.get("/seniors/sen_chen/followups").json()
        assert [j["hours_after"] for j in jobs] == [24.0, 72.0]
        assert [j["kind"] for j in jobs] == ["teachback", "checkin"]
    finally:
        get_settings.cache_clear()


def test_a_second_ed_evaluation_does_not_stack_another_set(client):
    """The family does not need two texts for one episode."""
    expected = len(get_settings().followup_hours)
    for _ in range(2):
        client.post("/checkins", json={"senior_id": "sen_rosa", "text": "Me falta el aire y tengo nausea, y estoy sudando frio.", "language": "es", "source": "text"})
    assert len(client.get("/seniors/sen_rosa/followups").json()) == expected


def test_the_clock_can_be_advanced_for_the_demo(client):
    """FOLLOWUP_TIME_SCALE collapses 24h/72h for the stage, through the same
    code path. Here the jobs are simply dated into the past, so the assertion
    is about the tick rather than about how fast the wall clock moved."""
    client.post("/checkins", json={"senior_id": "sen_rosa", "text": "Me falta el aire y tengo nausea, y estoy sudando frio.", "language": "es", "source": "text"})
    for job in followup.jobs_for("sen_rosa"):
        job.due_at = now() - timedelta(seconds=1)
        store.followups[job.id] = job

    result = client.post("/demo/tick").json()
    assert len(result["followups_sent"]) == len(get_settings().followup_hours)
    assert all(j["status"] == "sent" for j in client.get("/seniors/sen_rosa/followups").json())


def test_a_due_job_is_only_ever_claimed_once(client):
    """The background scheduler and a manual tick must not both send it --
    the family getting the same text twice is its own kind of alarm."""
    client.post("/checkins", json={"senior_id": "sen_rosa", "text": "Me falta el aire y tengo nausea, y estoy sudando frio.", "language": "es", "source": "text"})
    for job in followup.jobs_for("sen_rosa"):
        job.due_at = now() - timedelta(seconds=1)
        store.followups[job.id] = job

    first = followup.claim_due()
    second = followup.claim_due()
    assert len(first) == len(get_settings().followup_hours)
    assert second == []


def test_a_claim_that_never_completed_is_retried(client):
    """A process that died mid-send must not leave a follow-up silently stuck."""
    client.post("/checkins", json={"senior_id": "sen_rosa", "text": "Me falta el aire y tengo nausea, y estoy sudando frio.", "language": "es", "source": "text"})
    for job in followup.jobs_for("sen_rosa"):
        job.due_at = now() - timedelta(seconds=followup.CLAIM_TIMEOUT_S + 10)
        job.status = "sending"
        store.followups[job.id] = job

    assert len(followup.claim_due()) == len(get_settings().followup_hours)


def test_teach_back_passes_when_the_plan_comes_back(client):
    resp = client.post(
        "/teachback",
        json={
            "senior_id": "sen_rosa",
            "language": "en",
            "spoken": (
                "I take the water pill in the morning and not at night. I stand up "
                "slowly and sit back down if the room spins. I weigh myself every "
                "morning and write the number down. I call the clinic if I gain "
                "three pounds in a day."
            ),
        },
    )
    body = resp.json()
    assert body["passed"] is True
    assert body["missed"] == []
    assert body["caregiver_alerted"] is False


def test_a_missed_instruction_alerts_the_caregiver(client):
    _enroll(client)
    resp = client.post(
        "/teachback",
        json={
            "senior_id": "sen_rosa",
            "language": "en",
            "spoken": "I take my pills. I think that is it.",
        },
    )
    body = resp.json()
    assert body["passed"] is False
    assert body["missed"]
    assert body["caregiver_alerted"] is True
    types = [e["type"] for e in client.get("/seniors/sen_rosa/timeline").json()["entries"]]
    assert "teachback.completed" in types


def test_no_instructions_on_file_is_not_a_pass(client):
    """An empty plan means we have nothing to check, not that they understood."""
    client.put(
        "/seniors/sen_walter/care-plan",
        json={"senior_id": "sen_walter", "instructions": []},
    )
    body = client.post(
        "/teachback",
        json={"senior_id": "sen_walter", "spoken": "anything at all"},
    ).json()
    assert body["passed"] is False


def test_teach_back_ignores_accents_and_case():
    """Deepgram returns accented Spanish; a senior typing it often does not."""
    items = followup.score_teach_back(
        "Tomo la medicina por la manana y me peso cada dia",
        ["Tome la medicina por la mañana", "Pésese cada día"],
    )
    assert all(i.covered for i in items), [i.model_dump() for i in items]


def test_the_teach_back_prompt_is_in_their_language(client):
    body = client.get("/teachback/prompt/sen_rosa?language=es").json()
    assert body["language"] == "es"
    assert body["prompt"].startswith("Digame")
    assert body["instructions"]


def test_a_followup_answer_is_compared_against_their_own_baseline(client):
    """Not "is this bad" -- "is this worse than this person's normal"."""
    client.post("/checkins", json={"senior_id": "sen_rosa", "text": "Me falta el aire y tengo nausea, y estoy sudando frio.", "language": "es", "source": "text"})
    for job in followup.jobs_for("sen_rosa"):
        job.status = "sent"
        store.followups[job.id] = job
    client.post("/checkins", json={"senior_id": "sen_rosa", "text": "Me falta el aire y tengo nausea, y estoy sudando frio.", "language": "es", "source": "text"})
    answered = [j for j in client.get("/seniors/sen_rosa/followups").json() if j["status"] == "answered"]
    assert answered
    assert answered[0]["result_level"] is not None


# --------------------------------------------------------------------------
# Posture
# --------------------------------------------------------------------------
def test_linq_status_reports_the_truth_about_mock_mode(client):
    body = client.get("/linq/status").json()
    assert body["mock_mode"] is True
    assert body["enabled"] is False
    assert "message.received" in body["subscribed_events"]
    assert "reaction.added" in body["subscribed_events"]


def test_a_messaging_outage_never_breaks_a_checkin(client, monkeypatch):
    """The whole point of the result-object style in linq.py."""
    async def boom(*args, **kwargs):
        raise RuntimeError("linq is down")

    monkeypatch.setattr(linq, "send_to_chat", boom)
    monkeypatch.setattr(linq, "send_direct", boom)
    monkeypatch.setattr(circle, "raise_alert", lambda *a, **k: _raise_none())

    resp = client.post(
        "/checkins",
        json={"senior_id": "sen_walter", "text": "I feel fine today.", "language": "en", "source": "text"},
    )
    assert resp.status_code == 200


async def _raise_none():
    return None



# --------------------------------------------------------------------------
# Conformance with the published Linq v3 contract
# --------------------------------------------------------------------------
def test_an_opening_message_carries_no_url():
    """Linq rejects the first message to a new chat if it contains a link
    (400, code 1005). Mock mode hid this completely -- every real circle
    creation would have failed."""
    link = "https://carepath.test/c/sen_rosa"
    opening, url = linq.split_link(caretone.welcome_body("Rosa", ["Priya (daughter)"], link))
    assert url == link
    assert "http" not in opening
    assert opening.endswith(".")
    assert "Details:" not in opening


def test_splitting_a_link_leaves_a_sentence_not_a_stub():
    opening, url = linq.split_link(caretone.alert_body("Rosa", 3, "https://x.test/c/a"))
    assert url == "https://x.test/c/a"
    assert "Details" not in opening
    assert " ." not in opening
    assert opening.count("..") == 0


def test_a_body_with_no_link_is_unchanged():
    body = "Care team here. Nothing to do today."
    assert linq.split_link(body) == (body, None)


def test_a_dissatisfied_tapback_is_not_an_acknowledgement():
    """A thumbs-down or a question mark means the caregiver saw it and is not
    happy. Treating that as "handled" would stop the escalation clock at
    exactly the wrong moment."""
    for bad in ("dislike", "question", "laugh"):
        assert bad not in linq.ACK_REACTIONS
    for good in ("like", "love", "emphasize", "✅"):
        assert good in linq.ACK_REACTIONS


def test_a_check_mark_is_sent_as_a_custom_tapback():
    """iMessage has no check-mark tapback; Linq carries it as type custom."""
    assert linq.ACK_TAPBACK not in linq.BUILTIN_REACTIONS
    assert linq.ACK_TAPBACK in linq.ACK_REACTIONS
