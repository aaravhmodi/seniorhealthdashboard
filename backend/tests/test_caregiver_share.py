from fastapi.testclient import TestClient

from app.main import app


def test_checkin_can_be_shared_with_consenting_caregiver():
    with TestClient(app) as client:
        client.post("/demo/reset")
        created = client.post(
            "/checkins",
            json={
                "senior_id": "sen_rosa",
                "source": "text",
                "language": "en",
                "text": "I feel okay today.",
            },
        )
        assert created.status_code == 200
        checkin_id = created.json()["checkin"]["id"]

        shared = client.post(f"/checkins/{checkin_id}/caregiver")

        assert shared.status_code == 200
        body = shared.json()
        assert body["to"] == "cg_priya"
        assert body["status"] == "mocked"
        assert "I feel okay today" not in body["body"]
        assert "/c/" in body["body"]

