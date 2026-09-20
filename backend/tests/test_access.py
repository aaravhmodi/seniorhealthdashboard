"""Who can read what.

The PHI design is one sentence: the text carries a status word and a link, the
detail lives behind the link. Before this, `/c/sen_rosa` was a guess and
`GET /seniors/sen_rosa` needed no credentials at all -- so the careful,
PHI-free texting was undone by the thing it pointed at.

Two halves, and both are needed. Signing the link is theatre while the
underlying endpoint is open, because an attacker skips the link.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import auth, links
from app.config import get_settings
from app.main import app


ED_TEXT = "Me falta el aire y tengo nausea, y estoy sudando frio."


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.post("/demo/reset")
        yield c


@pytest.fixture
def locked(monkeypatch):
    """Turn API auth on, the way production would have it."""
    monkeypatch.setenv("API_SHARED_SECRET", "s3cret-for-tests")
    get_settings.cache_clear()
    yield "s3cret-for-tests"
    get_settings.cache_clear()


# --------------------------------------------------------------------------
# The signed link
# --------------------------------------------------------------------------
def test_a_link_round_trips(client):
    token = links.mint("sen_rosa", "eval_00001")
    claims = links.verify(token)
    assert claims.senior_id == "sen_rosa"
    assert claims.evaluation_id == "eval_00001"
    assert not claims.expired


def test_a_senior_id_is_not_a_valid_link(client):
    """The exact hole this closes: /c/sen_rosa used to be the whole credential."""
    with pytest.raises(links.LinkError):
        links.verify("sen_rosa")
    assert client.get("/caregiver/sen_rosa").status_code == 401


def test_a_tampered_link_is_refused(client):
    token = links.mint("sen_rosa")
    payload, signature = token.split(".")
    # Same signature, different senior.
    forged = links.mint("sen_walter").split(".")[0] + "." + signature
    with pytest.raises(links.LinkError):
        links.verify(forged)
    # And a flipped signature.
    with pytest.raises(links.LinkError):
        links.verify(f"{payload}.{signature[:-2]}xx")


def test_a_link_signed_with_another_key_is_refused(client, monkeypatch):
    token = links.mint("sen_rosa")
    monkeypatch.setenv("CAREGIVER_LINK_SECRET", "a-different-secret")
    get_settings.cache_clear()
    try:
        with pytest.raises(links.LinkError):
            links.verify(token)
    finally:
        get_settings.cache_clear()


def test_an_expired_link_stops_working(client):
    token = links.mint("sen_rosa", ttl_days=-1)
    with pytest.raises(links.LinkError) as exc:
        links.verify(token)
    assert "expired" in str(exc.value)
    assert client.get(f"/caregiver/{token}").status_code == 401


def test_expiry_is_checked_after_the_signature(client):
    """An expiry read out of an unverified payload is one the attacker chose."""
    import base64, json

    claims = {"sid": "sen_rosa", "exp": int(time.time()) + 10**9}
    payload = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")
    with pytest.raises(links.LinkError) as exc:
        links.verify(f"{payload}.not-a-real-signature")
    assert "not valid" in str(exc.value)


def test_the_link_in_a_text_is_signed(client):
    """What actually ships in the message."""
    from app import circle

    link = circle.detail_link("sen_rosa", "eval_1")
    token = link.rsplit("/c/", 1)[1]
    assert "sen_rosa" not in link.rsplit("/c/", 1)[0] + "/c/"
    assert links.verify(token).senior_id == "sen_rosa"


def test_without_a_signing_secret_no_guessable_link_is_minted(client, monkeypatch):
    """Failing closed: a link we cannot protect is not sent at all."""
    from app import circle

    monkeypatch.setenv("CAREGIVER_LINK_SECRET", "")
    get_settings.cache_clear()
    try:
        link = circle.detail_link("sen_rosa")
        assert "/c/" not in link
        assert "sen_rosa" not in link
    finally:
        get_settings.cache_clear()


# --------------------------------------------------------------------------
# What the caregiver page gets
# --------------------------------------------------------------------------
def test_the_caregiver_view_opens_with_a_valid_link(client):
    client.post("/checkins", json={"senior_id": "sen_rosa", "text": ED_TEXT,
                                   "language": "es", "source": "text"})
    token = links.mint("sen_rosa")
    body = client.get(f"/caregiver/{token}").json()
    assert body["senior"]["display_name"] == "Rosa Mendez"
    assert body["evaluation"]["level"] >= 3


def test_the_caregiver_view_never_returns_phone_numbers(client):
    """A forwarded link should not hand out the family's contact details."""
    client.post("/circle/enroll", json={
        "senior_id": "sen_rosa", "include_patient": False,
        "caregivers": [{"name": "Priya Mendez", "phone_e164": "+16175550142",
                        "relationship": "daughter"}]})
    token = links.mint("sen_rosa")
    raw = client.get(f"/caregiver/{token}").text
    assert "+16175550142" not in raw
    assert "phone" not in raw.lower()


def test_a_token_cannot_reach_another_senior(client):
    """An evaluation id belonging to someone else is a forged token, however
    good its signature looks."""
    client.post("/checkins", json={"senior_id": "sen_walter",
                                   "text": "My face is drooping and my arm is weak on one side.",
                                   "language": "en", "source": "text"})
    walters = client.get("/seniors/sen_walter/latest-evaluation").json()["id"]
    token = links.mint("sen_rosa", walters)
    body = client.get(f"/caregiver/{token}").json()
    assert body["senior"]["id"] == "sen_rosa"
    if body["evaluation"]:
        assert body["evaluation"]["senior_id"] == "sen_rosa"


def test_a_caregiver_can_acknowledge_from_the_page(client):
    client.post("/circle/enroll", json={
        "senior_id": "sen_rosa", "include_patient": False,
        "caregivers": [{"name": "Priya Mendez", "phone_e164": "+16175550142",
                        "relationship": "daughter"}]})
    client.post("/checkins", json={"senior_id": "sen_rosa", "text": ED_TEXT,
                                   "language": "es", "source": "text"})
    token = links.mint("sen_rosa")
    assert client.get(f"/caregiver/{token}").json()["open_alert"] is not None

    assert client.post(f"/caregiver/{token}/ack").json()["acknowledged"] is True
    assert client.get(f"/caregiver/{token}").json()["open_alert"] is None


# --------------------------------------------------------------------------
# The other half: the endpoints behind the link
# --------------------------------------------------------------------------
def test_patient_data_is_open_when_no_auth_is_configured(client):
    """Documenting the dev default, so it is a decision rather than a surprise."""
    assert auth.mode() == "open"
    assert client.get("/seniors/sen_rosa").status_code == 200
    assert client.get("/healthz").json()["auth_mode"] == "open"


def test_patient_data_needs_credentials_once_auth_is_on(client, locked):
    """Signing the link is theatre while this endpoint is open, because an
    attacker skips the link entirely."""
    assert client.get("/seniors/sen_rosa").status_code == 401
    assert client.get("/seniors/sen_rosa/timeline").status_code == 401
    assert client.get("/seniors/sen_rosa/checkins").status_code == 401
    assert client.post("/checkins", json={"senior_id": "sen_rosa", "text": "hi",
                                          "language": "en", "source": "text"}).status_code == 401
    assert client.get("/circle/sen_rosa/alerts").status_code == 401


def test_a_valid_credential_gets_through(client, locked):
    headers = {"Authorization": f"Bearer {locked}"}
    assert client.get("/seniors/sen_rosa", headers=headers).status_code == 200
    assert client.get("/healthz").json()["auth_mode"] == "shared-secret"


def test_a_wrong_credential_does_not(client, locked):
    for header in ({"Authorization": "Bearer nope"},
                   {"Authorization": locked},          # missing the scheme
                   {"Authorization": "Basic " + locked}):
        assert client.get("/seniors/sen_rosa", headers=header).status_code == 401


def test_the_caregiver_link_still_works_when_the_api_is_locked(client, locked):
    """The daughter has no account and never will. The link is her credential."""
    headers = {"Authorization": f"Bearer {locked}"}
    client.post("/checkins", headers=headers,
                json={"senior_id": "sen_rosa", "text": ED_TEXT,
                      "language": "es", "source": "text"})
    token = links.mint("sen_rosa")
    assert client.get(f"/caregiver/{token}").status_code == 200


def test_the_webhook_and_health_stay_reachable_when_locked(client, locked):
    """Linq does not carry our bearer, and a health check that 401s reads as
    an outage to the platform."""
    assert client.get("/healthz").status_code == 200
    assert client.post("/webhooks/linq", json={"event": "message.delivered",
                                               "data": {}}).status_code == 200


# --------------------------------------------------------------------------
# The PHI gate and the signed link have to coexist
# --------------------------------------------------------------------------
def test_a_signed_link_never_trips_the_phi_gate(client):
    """The bug this pins: the token is base64, and about one in seventeen
    happens to contain "mg". Checking the raw body meant the gate refused the
    send -- a family silently not told, because of a coincidence in a
    signature."""
    from app import caretone, circle, linq

    tripped = [
        i for i in range(400)
        if linq.contains_phi(caretone.alert_body("Rosa", 3, circle.detail_link(f"sen_{i}")))
    ]
    assert tripped == []


def test_the_phi_gate_still_catches_real_clinical_detail(client):
    """Ignoring the URL must not mean ignoring the message."""
    from app import linq

    link = "https://carepath.test/c/abc.def"
    assert linq.contains_phi(f"Rosa has chest pain. Details: {link}")
    assert linq.contains_phi(f"Take 10 mg twice daily. Details: {link}")
    assert linq.contains_phi(f"New confusion today. Details: {link}")
    assert not linq.contains_phi(f"Rosa checked in and needs a call. Details: {link}")


def test_the_composed_alert_carries_a_signed_link(client):
    """compose() used to build /c/<senior_id> by hand, bypassing the signing."""
    from app import notify

    senior = client.get("/seniors/sen_rosa").json()
    client.post("/checkins", json={"senior_id": "sen_rosa", "text": ED_TEXT,
                                   "language": "es", "source": "text"})
    from app.store import store

    evaluation = store.latest_evaluation("sen_rosa")
    body = notify.compose(store.get_senior("sen_rosa"), evaluation, store.get_senior("sen_rosa").caregivers[0])
    assert "/c/sen_rosa" not in body
    token = body.split("/c/")[1].split(".")[0] + "." + body.split("/c/")[1].split(".")[1].split(" ")[0].rstrip(".")
    assert links.verify(token).senior_id == "sen_rosa"
