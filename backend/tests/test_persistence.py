"""Durable state: what survives a restart, and what must never break a write.

The failure this exists to prevent: Render's free tier sleeps after fifteen
minutes idle, the process restarts, and every care circle, unacknowledged
alert and scheduled follow-up is gone. An alert with an escalation clock still
running is the worst of them -- lose it and a family that never answered is
never escalated to, and nobody ever finds out.

Supabase is faked here. What is tested is our half of the contract: that the
right rows are queued, that a restart puts them back, and that a database
having a bad day cannot fail a check-in.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import persistence
from app.config import get_settings
from app.main import app
from app.store import Store, stable_id, store


ED_TEXT = "Me falta el aire y tengo nausea, y estoy sudando frio."


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.post("/demo/reset")
        yield c


@pytest.fixture
def configured(monkeypatch):
    """Turn persistence on, without a real Supabase behind it."""
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-test-key")
    get_settings.cache_clear()
    for rows in persistence._dirty.values():
        rows.clear()
    yield
    for rows in persistence._dirty.values():
        rows.clear()
    get_settings.cache_clear()


# --------------------------------------------------------------------------
# Off by default
# --------------------------------------------------------------------------
def test_unconfigured_is_a_no_op(client):
    """With Supabase unset this is exactly the dict it always was."""
    assert persistence.enabled() is False
    client.post("/checkins", json={"senior_id": "sen_rosa", "text": ED_TEXT,
                                   "language": "es", "source": "text"})
    assert persistence.pending() == 0


def test_status_reports_the_truth(client):
    body = client.get("/datasets/status").json()["persistence"]
    assert body["enabled"] is False
    assert "alerts" in body["tables"]


# --------------------------------------------------------------------------
# The right things get queued
# --------------------------------------------------------------------------
def test_the_family_side_state_is_queued_for_persistence(client, configured):
    client.post("/circle/enroll", json={
        "senior_id": "sen_rosa", "include_patient": True,
        "patient_phone_e164": "+16175550100",
        "caregivers": [{"name": "Priya Mendez", "phone_e164": "+16175550142",
                        "relationship": "daughter", "escalation_order": 0}],
    })
    client.post("/checkins", json={"senior_id": "sen_rosa", "text": ED_TEXT,
                                   "language": "es", "source": "text"})

    queued = {t: len(rows) for t, rows in persistence._dirty.items() if rows}
    # The four that a restart must not lose.
    for table in ("circles", "alerts", "followups", "seniors"):
        assert queued.get(table), f"{table} was not queued: {queued}"
    assert queued.get("checkins")
    assert queued.get("evaluations")


def test_writing_through_a_plain_dict_assignment_still_persists(configured):
    """circle.py and followup.py assign into these dicts directly. Routing
    that through methods would mean trusting everyone to remember."""
    fresh = Store()
    from app.schemas import ActionLevel, Alert
    from app.store import now

    alert = Alert(id="alrt_x", senior_id="sen_rosa", level=ActionLevel.GO_TO_ER,
                  created_at=now(), body="Care team here.")
    fresh.alerts[alert.id] = alert
    assert "alrt_x" in persistence._dirty["alerts"]


def test_a_row_is_written_once_per_flush_however_often_it_changed(configured):
    """These are whole-row upserts of current state, not a change log."""
    from app.schemas import ActionLevel, Alert
    from app.store import now

    fresh = Store()
    for i in range(5):
        fresh.alerts["alrt_y"] = Alert(
            id="alrt_y", senior_id="sen_rosa", level=ActionLevel.GO_TO_ER,
            created_at=now(), body="Care team here.", escalations=i,
        )
    assert len(persistence._dirty["alerts"]) == 1
    assert persistence._dirty["alerts"]["alrt_y"]["data"]["escalations"] == 4


def test_a_timeline_id_is_stable_across_processes():
    """Python's hash() is salted per process; using it would mint a new id for
    the same entry after every restart and duplicate the row."""
    a = stable_id("sen_rosa", "2026-09-20T00:00:00+00:00", "Alerted the care circle")
    b = stable_id("sen_rosa", "2026-09-20T00:00:00+00:00", "Alerted the care circle")
    assert a == b
    assert a != stable_id("sen_rosa", "2026-09-20T00:00:00+00:00", "something else")


def test_rows_are_json_safe(client, configured):
    """jsonb will not take a datetime or an enum, and pydantic has to be able
    to read back whatever we wrote."""
    import json

    client.post("/checkins", json={"senior_id": "sen_rosa", "text": ED_TEXT,
                                   "language": "es", "source": "text"})
    for table, rows in persistence._dirty.items():
        for row in rows.values():
            json.dumps(row)  # raises if anything is not JSON-safe


# --------------------------------------------------------------------------
# Failure must not reach the request path
# --------------------------------------------------------------------------
def test_a_database_having_a_bad_day_cannot_fail_a_check_in(client, configured, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("supabase is down")

    monkeypatch.setattr(persistence, "mark", explode)
    resp = client.post("/checkins", json={"senior_id": "sen_rosa", "text": ED_TEXT,
                                          "language": "es", "source": "text"})
    assert resp.status_code == 200


def test_a_failed_flush_puts_the_rows_back(configured, monkeypatch):
    """Silently dropping a write would defeat the entire point."""
    from app.schemas import ActionLevel, Alert
    from app.store import now

    fresh = Store()
    fresh.alerts["alrt_z"] = Alert(id="alrt_z", senior_id="sen_rosa",
                                   level=ActionLevel.GO_TO_ER, created_at=now(),
                                   body="Care team here.")
    assert persistence.pending() == 1

    class Boom:
        def __call__(self, *a, **k):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **k):
            raise RuntimeError("connection reset")

    monkeypatch.setattr(persistence.httpx, "AsyncClient", Boom())
    written = asyncio.run(persistence.flush())
    assert written == {}
    assert persistence.pending() == 1, "a failed write must be retried, not lost"


def test_an_unreadable_row_does_not_stop_the_rest_hydrating(configured, monkeypatch):
    from app.schemas import Senior

    good = Senior(id="sen_ok", display_name="Ada Ok", date_of_birth="1945-01-01", age=81)
    rows = {
        "seniors": [
            {"id": "sen_bad", "data": {"nope": True}},
            {"id": "sen_ok", "data": good.model_dump(mode="json")},
        ],
    }

    async def fake_select(_client, table):
        return rows.get(table, [])

    monkeypatch.setattr(persistence, "_select", fake_select)

    class Fake:
        def __call__(self, *a, **k):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(persistence.httpx, "AsyncClient", Fake())

    fresh = Store()
    loaded = asyncio.run(persistence.hydrate(fresh))
    assert loaded["seniors"] == 1
    assert "sen_ok" in fresh.seniors
    assert "sen_bad" not in fresh.seniors


# --------------------------------------------------------------------------
# The restart
# --------------------------------------------------------------------------
def test_a_restart_brings_back_the_escalation_clock(client, configured, monkeypatch):
    """The row that matters most: an alert still waiting to be answered."""
    client.post("/circle/enroll", json={
        "senior_id": "sen_rosa", "include_patient": False,
        "caregivers": [
            {"name": "Priya Mendez", "phone_e164": "+16175550142",
             "relationship": "daughter", "escalation_order": 0},
            {"name": "Marco Mendez", "phone_e164": "+16175550178",
             "relationship": "son", "escalation_order": 1},
        ],
    })
    client.post("/checkins", json={"senior_id": "sen_rosa", "text": ED_TEXT,
                                   "language": "es", "source": "text"})

    alert_rows = list(persistence._dirty["alerts"].values())
    circle_rows = list(persistence._dirty["circles"].values())
    senior_rows = list(persistence._dirty["seniors"].values())
    assert alert_rows and circle_rows

    before = list(store.alerts.values())[0]
    assert before.escalate_after is not None
    assert before.pending_order, "there must still be someone to escalate to"

    # The process dies here. A new one comes up and hydrates.
    async def fake_select(_client, table):
        return {"alerts": alert_rows, "circles": circle_rows,
                "seniors": senior_rows}.get(table, [])

    monkeypatch.setattr(persistence, "_select", fake_select)

    class Fake:
        def __call__(self, *a, **k):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(persistence.httpx, "AsyncClient", Fake())

    revived = Store()
    asyncio.run(persistence.hydrate(revived))

    after = revived.alerts[before.id]
    assert after.escalate_after == before.escalate_after
    assert after.pending_order == before.pending_order
    assert after.resolved is False
    assert revived.circles["sen_rosa"].chat_id == store.circles["sen_rosa"].chat_id


def test_a_restart_brings_back_a_scheduled_follow_up(client, configured, monkeypatch):
    client.post("/checkins", json={"senior_id": "sen_rosa", "text": ED_TEXT,
                                   "language": "es", "source": "text"})
    rows = list(persistence._dirty["followups"].values())
    assert rows

    async def fake_select(_client, table):
        return rows if table == "followups" else []

    monkeypatch.setattr(persistence, "_select", fake_select)

    class Fake:
        def __call__(self, *a, **k):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(persistence.httpx, "AsyncClient", Fake())

    revived = Store()
    asyncio.run(persistence.hydrate(revived))
    job = list(revived.followups.values())[0]
    assert job.status == "scheduled"
    assert job.hours_after == 24.0
