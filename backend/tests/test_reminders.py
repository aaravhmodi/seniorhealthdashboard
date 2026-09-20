from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.store import now, store


def test_scheduled_reminder_is_delivered_by_the_scheduler():
    with TestClient(app) as client:
        client.post("/demo/reset")
        response = client.post(
            "/seniors/sen_rosa/reminders",
            json={
                "senior_id": "sen_rosa",
                "kind": "meds",
                "recipient": "self",
                "scheduled_for": (now() + timedelta(hours=1)).isoformat(),
            },
        )
        assert response.status_code == 201
        reminder_id = response.json()["id"]

        # Move the persisted job into the due window, then advance the same
        # scheduler tick used by the running service and the demo controls.
        store.reminder_jobs[reminder_id].scheduled_for = now() - timedelta(seconds=1)
        tick = client.post("/demo/tick")

        assert tick.status_code == 200
        assert reminder_id in tick.json()["reminders_sent"]
        assert client.get(f"/seniors/sen_rosa/reminders").json()[0]["status"] == "sent"
        assert next(iter(store.reminders.values()))["recipient"] == "+16175550100"
