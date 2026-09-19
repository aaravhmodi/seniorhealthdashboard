"""Deepgram: speech in, speech out.

The offline tests fake the transport and keep every guard around it. The live
tests at the bottom are a genuine round trip -- we synthesize a sentence with
Aura, send the audio straight back to nova-3, and assert we get the sentence
back -- and they are the only proof that the keys, the model names and the
parameters are actually right. Run them with:

    pytest -m live --live

Nothing above that marker touches the network.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app import voice
from app.config import get_settings
from app.main import app
from app.persona import DEEPGRAM_TTS_VOICE


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.post("/demo/reset")
        yield c


@pytest.fixture
def with_deepgram(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test-not-real")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def fake_transport(monkeypatch, *, json_body=None, content=b"", status=200):
    """Swap httpx.Client, capture what we sent."""
    captured: dict = {}

    class FakeResponse:
        status_code = status

        def raise_for_status(self):
            if status >= 400:
                raise voice.httpx.HTTPStatusError(
                    f"{status}", request=None, response=None
                )

        def json(self):
            return json_body

        @property
        def content(self):
            return content

    class FakeClient:
        def __init__(self, **kw):
            captured["timeout"] = kw.get("timeout")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, params=None, json=None, content=None):
            captured.update(
                url=url, headers=headers, params=params, json=json, body=content
            )
            return FakeResponse()

    monkeypatch.setattr(voice.httpx, "Client", FakeClient)
    return captured


GOOD_RESPONSE = {
    "metadata": {"duration": 3.4},
    "results": {
        "channels": [
            {
                "detected_language": "es",
                "alternatives": [
                    {"transcript": "Me falta el aire y tengo nausea.", "confidence": 0.94}
                ],
            }
        ]
    },
}


# -- parameters ------------------------------------------------------------
def test_listen_params_are_senior_tuned_but_drop_streaming_only_options():
    """Batch transcription rejects the streaming knobs; the model and language
    still have to survive."""
    params = voice.listen_params("es")
    assert params["model"] == "nova-3"
    assert params["language"] == "es"
    assert params["smart_format"] is True
    for streaming_only in ("endpointing", "interim_results", "utterance_end_ms"):
        assert streaming_only not in params


def test_detect_language_replaces_a_fixed_language():
    """The kiosk case: nobody has told us what they are about to speak."""
    params = voice.listen_params("en", detect=True)
    assert params["detect_language"] is True
    assert "language" not in params


# -- transcription ---------------------------------------------------------
def test_transcribe_url_sends_the_link_and_parses_the_result(with_deepgram, monkeypatch):
    captured = fake_transport(monkeypatch, json_body=GOOD_RESPONSE)
    result = voice.transcribe_url("https://example.test/a.wav", "es")

    assert captured["json"] == {"url": "https://example.test/a.wav"}
    assert captured["headers"]["Authorization"] == "Token dg-test-not-real"
    assert captured["params"]["language"] == "es"
    assert result.text == "Me falta el aire y tengo nausea."
    assert result.confidence == pytest.approx(0.94)
    assert result.duration_s == 3.4
    assert result.detected_language == "es"
    assert result.is_low_confidence is False


def test_transcribe_bytes_posts_the_audio_as_the_body(with_deepgram, monkeypatch):
    captured = fake_transport(monkeypatch, json_body=GOOD_RESPONSE)
    voice.transcribe_bytes(b"RIFFfake", content_type="audio/wav", language="es")
    assert captured["body"] == b"RIFFfake"
    assert captured["headers"]["Content-Type"] == "audio/wav"


def test_a_mumbled_transcript_is_flagged_low_confidence(with_deepgram, monkeypatch):
    fake_transport(monkeypatch, json_body={
        "metadata": {},
        "results": {"channels": [{"alternatives": [
            {"transcript": "mmm", "confidence": 0.21}
        ]}]},
    })
    result = voice.transcribe_bytes(b"x", language="en")
    assert result.is_low_confidence, "the ladder escalates a rung on this"


def test_empty_audio_is_rejected_before_we_pay_for_a_call(with_deepgram):
    with pytest.raises(voice.DeepgramError, match="empty audio"):
        voice.transcribe_bytes(b"", language="en")


def test_a_broken_response_shape_raises_rather_than_returning_silence(
    with_deepgram, monkeypatch
):
    fake_transport(monkeypatch, json_body={"results": {}})
    with pytest.raises(voice.DeepgramError, match="unexpected"):
        voice.transcribe_bytes(b"x", language="en")


def test_no_key_raises_instead_of_pretending():
    with pytest.raises(voice.DeepgramError, match="DEEPGRAM_API_KEY"):
        voice.transcribe_url("https://example.test/a.wav")


# -- synthesis -------------------------------------------------------------
def test_speak_picks_the_right_voice_per_language(with_deepgram, monkeypatch):
    captured = fake_transport(monkeypatch, content=b"RIFFaudio")
    audio = voice.speak("Vaya ahora a urgencias.", "es")
    assert audio == b"RIFFaudio"
    assert captured["params"]["model"] == "aura-2-celeste-es"
    assert captured["json"] == {"text": "Vaya ahora a urgencias."}


def test_speak_refuses_a_language_it_cannot_actually_speak(with_deepgram):
    with pytest.raises(voice.DeepgramError, match="no Deepgram voice"):
        voice.speak("请现在去急诊。", "zh")


def test_coverage_is_honest_about_the_asymmetry():
    cover = voice.coverage()
    assert set(cover["speak"]) == {"en", "es", "fr"}
    assert set(cover["text_only"]) == {"zh", "pt", "hi"}
    assert set(cover["speak"]) & set(cover["text_only"]) == set()
    assert set(cover["speak"]) <= set(cover["listen"]), "we can hear all we speak"


# -- endpoints -------------------------------------------------------------
def test_coverage_endpoint(client):
    body = client.get("/voice/coverage").json()
    assert body["stt_model"] == "nova-3"
    assert body["tts_voices"]["fr"] == "aura-2-agathe-fr"
    assert body["enabled"] is False, "tests run with Deepgram switched off"


def test_transcribe_endpoint_without_a_key_is_503(client):
    resp = client.post(
        "/voice/transcribe",
        files={"file": ("a.wav", io.BytesIO(b"RIFFfake"), "audio/wav")},
        data={"language": "es"},
    )
    assert resp.status_code == 503


def test_transcribe_endpoint_returns_what_we_heard(client, with_deepgram, monkeypatch):
    fake_transport(monkeypatch, json_body=GOOD_RESPONSE)
    resp = client.post(
        "/voice/transcribe",
        files={"file": ("a.wav", io.BytesIO(b"RIFFfake"), "audio/wav")},
        data={"language": "es"},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["text"] == "Me falta el aire y tengo nausea."
    assert body["confidence"] == 0.94
    assert body["low_confidence"] is False


def test_speak_endpoint_409s_where_there_is_no_voice(client, with_deepgram):
    resp = client.post("/voice/speak", json={"text": "请现在去急诊。", "language": "zh"})
    assert resp.status_code == 409
    assert "text" in resp.json()["detail"]


def test_speak_endpoint_returns_audio(client, with_deepgram, monkeypatch):
    fake_transport(monkeypatch, content=b"RIFFaudio")
    resp = client.post("/voice/speak", json={"text": "Go now.", "language": "en"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.content == b"RIFFaudio"


def test_audio_url_checkin_transcribes_then_runs_the_ladder(
    client, with_deepgram, monkeypatch
):
    """The whole point: audio in one end, an action level out the other."""
    fake_transport(monkeypatch, json_body=GOOD_RESPONSE)
    body = client.post(
        "/checkins",
        json={
            "senior_id": "sen_rosa",
            "language": "es",
            "audio_url": "https://example.test/rosa.wav",
        },
    ).json()

    assert body["checkin"]["raw_text"] == "Me falta el aire y tengo nausea."
    assert body["checkin"]["source"] == "voice"
    assert body["checkin"]["transcript_confidence"] == pytest.approx(0.94)
    assert body["evaluation"]["level"] >= 3, "breathless + nausea is the cardiac path"


def test_a_failed_transcription_is_a_502_not_a_quiet_level_one(
    client, with_deepgram, monkeypatch
):
    fake_transport(monkeypatch, json_body={"results": {}})
    resp = client.post(
        "/checkins",
        json={"senior_id": "sen_rosa", "language": "es",
              "audio_url": "https://example.test/rosa.wav"},
    )
    assert resp.status_code == 502


# -- live round trip -------------------------------------------------------
@pytest.mark.live
@pytest.mark.parametrize(
    "language,sentence,expect_word",
    [
        ("en", "I fell in the kitchen and hit my head.", "fell"),
        ("es", "Me falta el aire y tengo nausea.", "aire"),
        ("fr", "Je suis tombee et je me suis cogne la tete.", "tomb"),
    ],
)
def test_live_aura_to_nova_round_trip(monkeypatch, language, sentence, expect_word):
    """Synthesize it, hear it back, and check the words survived the trip.

    This is the test that would have caught the wrong voice names.
    """
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    get_settings.cache_clear()
    if not voice.is_enabled():
        pytest.skip("no DEEPGRAM_API_KEY configured")

    audio = voice.speak(sentence, language)
    assert len(audio) > 1000, "Aura returned no audio"

    heard = voice.transcribe_bytes(audio, content_type="audio/wav", language=language)
    assert expect_word.lower() in heard.text.lower(), (
        f"{language}: said {sentence!r}, heard {heard.text!r}"
    )
    assert heard.confidence > 0.5


@pytest.mark.live
def test_live_every_configured_voice_actually_exists(monkeypatch):
    """Each name in DEEPGRAM_TTS_VOICE must be a model the account can call."""
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    get_settings.cache_clear()
    if not voice.is_enabled():
        pytest.skip("no DEEPGRAM_API_KEY configured")

    for language in DEEPGRAM_TTS_VOICE:
        audio = voice.speak("Test.", language)
        assert len(audio) > 500, f"{language} produced no audio"
