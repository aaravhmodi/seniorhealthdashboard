"""Robustness: retries, replays, loops, and failures that must not stay silent.

The family side has one promise -- a text that must arrive, arrives -- and
everything here is about the ways that promise breaks quietly. Mock mode says
"ok" to everything, so these tests put a fake transport in front of the real
client and make it misbehave on purpose.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import linq, linq_events
from app.config import get_settings
from app.main import app
from app.store import now, store


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.post("/demo/reset")
        linq_events.forget_deliveries()
        yield c


def _enroll(c, senior_id="sen_rosa"):
    return c.post(
        "/circle/enroll",
        json={
            "senior_id": senior_id,
            "include_patient": True,
            "patient_phone_e164": "+16175550100",
            "caregivers": [
                {"name": "Priya Mendez", "phone_e164": "+16175550142",
                 "relationship": "daughter", "escalation_order": 0},
                {"name": "Marco Mendez", "phone_e164": "+16175550178",
                 "relationship": "son", "escalation_order": 1},
            ],
        },
    )


ED_TEXT = "Me falta el aire y tengo nausea, y estoy sudando frio."
STEADY = "Care team here. All steady."


def _ed_checkin(c, senior_id="sen_rosa"):
    return c.post(
        "/checkins",
        json={"senior_id": senior_id, "text": ED_TEXT, "language": "es", "source": "text"},
    )


# --------------------------------------------------------------------------
# A fake transport, so retry behaviour is observable rather than assumed
# --------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text
        self.content = b"x" if payload is not None else b""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, headers=None, json=None):
        self.calls += 1
        item = self.responses.pop(0) if self.responses else FakeResponse(500, None)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def live(monkeypatch):
    """Mock mode off, a fake transport in front, and no real sleeping."""
    def build(responses):
        monkeypatch.setenv("MOCK_MODE", "false")
        monkeypatch.setenv("LINQ_API_KEY", "test-key")
        get_settings.cache_clear()
        fake = FakeClient(responses)
        monkeypatch.setattr(linq.httpx, "AsyncClient", fake)

        async def no_sleep(_seconds):
            return None

        monkeypatch.setattr(linq.asyncio, "sleep", no_sleep)
        return fake

    yield build
    get_settings.cache_clear()


OK_SEND = {"chat_id": "chat-1", "message": {"id": "msg-1"}}


def test_a_rate_limit_is_retried_after_the_delay_linq_asks_for(live):
    fake = live([
        FakeResponse(429, {
            "success": False, "trace_id": "trace_abc",
            "error": {"status": 429, "code": 1007, "message": "Rate limited.", "retry_after": 2},
        }),
        FakeResponse(202, OK_SEND),
    ])
    result = asyncio.run(linq.send_to_chat("chat-1", STEADY))
    assert result.ok
    assert result.message_id == "msg-1"
    assert result.attempts == 2
    assert fake.calls == 2


def test_a_bad_request_is_not_retried(live):
    """Retrying a 400 is how one mistake turns into a rate limit."""
    fake = live([FakeResponse(400, {
        "success": False, "trace_id": "trace_x",
        "error": {"status": 400, "code": 1003, "message": "Invalid request body"},
    })] * 3)
    result = asyncio.run(linq.send_to_chat("chat-1", STEADY))
    assert not result.ok
    assert result.code == 1003
    assert result.trace_id == "trace_x"
    assert fake.calls == 1


def test_an_error_code_is_parsed_not_sniffed_out_of_a_string(live):
    """A trace id containing '1005' must not be mistaken for error 1005."""
    live([FakeResponse(400, {
        "trace_id": "req_1005_deadbeef",
        "error": {"status": 400, "code": 1003, "message": "nope"},
    })])
    result = asyncio.run(linq.send_to_chat("chat-1", STEADY))
    assert result.code == 1003
    assert "1005" in (result.trace_id or "")


def test_a_server_fault_is_retried(live):
    fake = live([FakeResponse(503, None, text="upstream"), FakeResponse(202, OK_SEND)])
    assert asyncio.run(linq.send_to_chat("chat-1", STEADY)).ok
    assert fake.calls == 2


def test_a_network_fault_is_retried(live):
    fake = live([RuntimeError("connection reset"), FakeResponse(202, OK_SEND)])
    assert asyncio.run(linq.send_to_chat("chat-1", STEADY)).ok
    assert fake.calls == 2


def test_attempts_are_bounded(live):
    fake = live([FakeResponse(503, None)] * 10)
    result = asyncio.run(linq.send_to_chat("chat-1", STEADY))
    assert not result.ok
    assert fake.calls == get_settings().linq_max_attempts


def test_we_do_not_hold_a_request_open_for_a_long_rate_limit(live):
    """A two-minute Retry-After must not block a check-in; the outbound retry
    picks it up instead."""
    fake = live([FakeResponse(429, {
        "error": {"status": 429, "code": 1007, "message": "slow down", "retry_after": 120},
    })] * 2)
    result = asyncio.run(linq.send_to_chat("chat-1", STEADY))
    assert not result.ok
    assert result.retry_after == 120
    assert fake.calls == 1


def test_the_retry_after_header_is_read_when_the_body_omits_it(live):
    fake = live([
        FakeResponse(429, {"error": {"code": 1007}}, headers={"Retry-After": "3"}),
        FakeResponse(202, OK_SEND),
    ])
    assert asyncio.run(linq.send_to_chat("chat-1", STEADY)).ok
    assert fake.calls == 2


# --------------------------------------------------------------------------
# Loops and replays
# --------------------------------------------------------------------------
def test_our_own_message_coming_back_is_ignored():
    """A handler that answers its own sends is how two bots end up texting a
    family in a loop -- Linq names it as the usual cause of a 1007."""
    assert linq_events.normalize({
        "event": "message.received",
        "data": {"chat": {"id": "c"}, "sender_handle": {"handle": "+1415", "is_me": True},
                 "parts": [{"type": "text", "value": "where is mom"}]},
    }) is None


def test_our_own_tapback_is_not_the_family_acknowledging():
    assert linq_events.normalize({
        "event": "reaction.added",
        "data": {"chat_id": "c", "reaction_type": "like", "from_handle": {"is_me": True}},
    }) is None


def test_a_replayed_webhook_is_not_acted_on_twice(client):
    """Linq delivers at-least-once and retries a non-2xx for ~25 minutes, so
    duplicates are routine. Answering "where is Dad?" twice texts an anxious
    family twice."""
    _enroll(client)
    client.post("/checkins", json={
        "senior_id": "sen_rosa", "text": "Tengo mareo fuerte cuando me levanto.",
        "language": "es", "source": "text"})
    chat_id = client.get("/circle/sen_rosa").json()["chat_id"]
    event = {
        "event": "message.received",
        "data": {"chat": {"id": chat_id}, "direction": "inbound",
                 "sender_handle": {"handle": "+16175550142"},
                 "parts": [{"type": "text", "value": "where is mom?"}]},
    }
    headers = {"webhook-id": "evt_replay_1"}
    first = client.post("/webhooks/linq", json=event, headers=headers).json()
    second = client.post("/webhooks/linq", json=event, headers=headers).json()
    assert "status_sent" in first["actions"]
    assert second["handled"] is False
    assert second["reason"] == "duplicate delivery"


def test_a_different_delivery_is_still_acted_on(client):
    """Dedupe must key on the delivery, not on the content -- a caregiver may
    genuinely ask twice."""
    _enroll(client)
    _ed_checkin(client)
    chat_id = client.get("/circle/sen_rosa").json()["chat_id"]
    event = {
        "event": "message.received",
        "data": {"chat": {"id": chat_id}, "direction": "inbound",
                 "sender_handle": {"handle": "+16175550142"},
                 "parts": [{"type": "text", "value": "where is mom?"}]},
    }
    a = client.post("/webhooks/linq", json=event, headers={"webhook-id": "evt_a"}).json()
    b = client.post("/webhooks/linq", json=event, headers={"webhook-id": "evt_b"}).json()
    assert "status_sent" in a["actions"]
    assert "status_sent" in b["actions"]


# --------------------------------------------------------------------------
# An alert that never went out
# --------------------------------------------------------------------------
@pytest.fixture
def linq_down(monkeypatch):
    async def down(*args, **kwargs):
        return linq.LinqResult(ok=False, status=503, error="503: upstream")

    monkeypatch.setattr(linq, "send_to_chat", down)
    monkeypatch.setattr(linq, "send_direct", down)
    return monkeypatch


def test_a_failed_alert_is_retried_not_forgotten(client, linq_down):
    """A thirty-second outage must not swallow a level-3 alert."""
    _enroll(client)
    _ed_checkin(client)

    alert = client.get("/circle/sen_rosa/alerts").json()[0]
    assert alert["delivery"] == "pending"
    assert alert["next_retry_at"] is not None

    async def up(*args, **kwargs):
        return linq.LinqResult(ok=True, chat_id="c", message_id="m2")

    linq_down.setattr(linq, "send_to_chat", up)
    linq_down.setattr(linq, "send_direct", up)

    stored = store.alerts[alert["id"]]
    stored.next_retry_at = now() - timedelta(seconds=1)
    store.alerts[stored.id] = stored

    result = client.post("/demo/tick").json()
    assert alert["id"] in result["alerts_redelivered"]
    after = client.get("/circle/sen_rosa/alerts").json()[0]
    assert after["delivery"] == "sent"
    assert after["message_id"] == "m2"


def test_an_undeliverable_alert_is_marked_failed_not_quietly_dropped(client, linq_down):
    _enroll(client)
    _ed_checkin(client)
    alert_id = client.get("/circle/sen_rosa/alerts").json()[0]["id"]

    for _ in range(get_settings().alert_max_delivery_attempts + 2):
        stored = store.alerts[alert_id]
        if stored.delivery != "pending":
            break
        stored.next_retry_at = now() - timedelta(seconds=1)
        store.alerts[alert_id] = stored
        client.post("/demo/tick")

    assert store.alerts[alert_id].delivery == "failed"
    # Never resolved: an alert the family never saw is not one they answered.
    assert store.alerts[alert_id].resolved is False


def test_the_escalation_clock_starts_only_once_someone_was_told(client, linq_down):
    """Escalating for silence on a message that never arrived blames the
    family for our outage."""
    _enroll(client)
    _ed_checkin(client)
    assert client.get("/circle/sen_rosa/alerts").json()[0]["escalate_after"] is None


def test_a_redelivered_alert_then_starts_its_clock(client, linq_down):
    _enroll(client)
    _ed_checkin(client)
    alert_id = client.get("/circle/sen_rosa/alerts").json()[0]["id"]

    async def up(*args, **kwargs):
        return linq.LinqResult(ok=True, chat_id="c", message_id="m2")

    linq_down.setattr(linq, "send_to_chat", up)
    linq_down.setattr(linq, "send_direct", up)
    stored = store.alerts[alert_id]
    stored.next_retry_at = now() - timedelta(seconds=1)
    store.alerts[alert_id] = stored
    client.post("/demo/tick")

    assert store.alerts[alert_id].escalate_after is not None


def test_a_check_in_still_succeeds_while_linq_is_down(client, linq_down):
    """The whole reason linq.py returns results instead of raising."""
    assert _ed_checkin(client).status_code == 200


# --------------------------------------------------------------------------
# Sleeping hosts
# --------------------------------------------------------------------------
def test_overdue_work_catches_up_on_wake(client):
    """Render stops the process after ~15 minutes idle and Fly suspends on the
    same idea, so nothing time-triggered fires while it is down. What must be
    true is that the backlog runs on the first tick after it wakes -- that is
    what makes the outage "late" rather than "never"."""
    _enroll(client)
    _ed_checkin(client)

    # The process was asleep for three hours. Everything is long overdue.
    for job in list(store.followups.values()):
        job.due_at = now() - timedelta(hours=3)
        store.followups[job.id] = job
    for alert in list(store.alerts.values()):
        if alert.escalate_after:
            alert.escalate_after = now() - timedelta(hours=3)
            store.alerts[alert.id] = alert

    woke = client.post("/demo/tick").json()
    assert woke["followups_sent"], "the overdue follow-up did not go out on wake"
    assert woke["alerts_escalated"], "the overdue escalation did not fire on wake"

    assert all(j["status"] == "sent" for j in client.get("/seniors/sen_rosa/followups").json())
    alert = client.get("/circle/sen_rosa/alerts").json()[0]
    assert alert["escalations"] == 1
    assert len(alert["notified"]) == 2


def test_a_long_sleep_does_not_fire_the_same_job_twice(client):
    """Catching up must not mean sending the backlog once per tick."""
    _enroll(client)
    _ed_checkin(client)
    for job in list(store.followups.values()):
        job.due_at = now() - timedelta(hours=3)
        store.followups[job.id] = job

    first = client.post("/demo/tick").json()["followups_sent"]
    second = client.post("/demo/tick").json()["followups_sent"]
    assert first
    assert second == []
