"""Voice and language tests.

Two jobs:
1. Everything we say to a senior obeys the style contract (persona.py).
2. The same complaint in English, Spanish and French reaches the same rung.
   A patient must not get a different answer for speaking French.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.explain import render_explanation
from app.extraction import extract
from app.main import app
from app.persona import (
    DEEPGRAM_TTS_VOICE,
    SUPPORTED_LANGUAGES,
    VOICE,
    detect_control,
    lint,
    system_prompt,
    voice_output_available,
)
from app.schemas import ActionLevel


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.post("/demo/reset")
        yield c


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("zh", "\u6211\u547c\u5438\u56f0\u96be\uff0c\u6076\u5fc3\uff0c\u8fd8\u5728\u51fa\u51b7\u6c57\u3002"),
        ("hi", "\u092e\u0941\u091d\u0947 \u0938\u093e\u0902\u0938 \u0932\u0947\u0928\u0947 \u092e\u0947\u0902 \u0924\u0915\u0932\u0940\u092b, \u092e\u0924\u0932\u0940 \u0914\u0930 \u0920\u0902\u0921\u093e \u092a\u0938\u0940\u0928\u093e \u0906 \u0930\u0939\u093e \u0939\u0948."),
    ],
)
def test_native_script_urgent_symptoms_are_extracted(language, text):
    labels = {symptom.label for symptom in extract(text, language)}
    assert {"shortness of breath", "nausea"} <= labels


def test_native_script_cardiac_scenario_reaches_emergency_department(client):
    cases = [
        ("zh", "\u6211\u547c\u5438\u56f0\u96be\uff0c\u6076\u5fc3\uff0c\u8fd8\u5728\u51fa\u51b7\u6c57\u3002"),
        ("hi", "\u092e\u0941\u091d\u0947 \u0938\u093e\u0902\u0938 \u0932\u0947\u0928\u0947 \u092e\u0947\u0902 \u0924\u0915\u0932\u0940\u092b, \u092e\u0924\u0932\u0940 \u0914\u0930 \u0920\u0902\u0921\u093e \u092a\u0938\u0940\u0928\u093e \u0906 \u0930\u0939\u093e \u0939\u0948."),
    ]
    for language, text in cases:
        response = client.post(
            "/checkins",
            json={"senior_id": "sen_rosa", "language": language, "text": text},
        )
        assert response.status_code == 200
        assert response.json()["evaluation"]["level"] >= ActionLevel.GO_TO_ER


# -- the linter itself has to work before it can guard anything -----------
@pytest.mark.parametrize(
    "bad",
    [
        "Okay sweetie, let's take our medicine now.",
        "How are we feeling today?",
        "Good job! You did it!",
        "You may be experiencing dyspnea and diaphoresis.",
        "Great work today 😊",
    ],
)
def test_lint_catches_elderspeak_and_jargon(bad):
    assert not lint(bad).ok, f"linter missed: {bad}"


@pytest.mark.parametrize(
    "good",
    [
        "Please call your clinic today. Your dizziness is getting worse.",
        "Go to the emergency department now. Do not drive yourself.",
        "Nothing here needs a call today.",
    ],
)
def test_lint_passes_plain_adult_speech(good):
    result = lint(good)
    assert result.ok, result.problems


# -- every string we can emit, in every language ---------------------------
@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
@pytest.mark.parametrize("level", list(ActionLevel))
def test_every_explanation_obeys_the_style_contract(language, level):
    said, english = render_explanation(
        level=level, language=language, symptoms=["dizziness"], flags=[], evidence=[]
    )
    assert lint(said, language).ok, lint(said, language).problems
    assert lint(english, "en").ok, lint(english, "en").problems
    assert said.strip() and english.strip()


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_urgent_advice_leads_with_the_action(language):
    """Action first, reason second. A senior who stops listening after the
    first sentence must still have heard what to do."""
    said, _ = render_explanation(
        level=ActionLevel.CALL_911, language=language, symptoms=[], flags=[], evidence=[]
    )
    first = said.split(".")[0].lower()
    assert any(
        w in first
        for w in ("call", "llame", "appelez", "拨打", "ligue", "फ़ोन")
    ), f"{language}: first sentence does not say what to do -- {first!r}"


# -- pace controls ---------------------------------------------------------
@pytest.mark.parametrize(
    "text,language,expected",
    [
        ("Can you say that again please", "en", "repeat"),
        ("Repita por favor", "es", "repeat"),
        ("Repetez s'il vous plait", "fr", "repeat"),
        ("please slow down", "en", "slower"),
        ("mas despacio por favor", "es", "slower"),
        ("moins vite s'il vous plait", "fr", "slower"),
        ("I want to talk to a person", "en", "human"),
        ("quiero hablar con una persona", "es", "human"),
        ("je veux parler a quelqu'un", "fr", "human"),
        ("start over", "en", "start_over"),
        ("I have chest pain", "en", None),
        ("Me falta el aire", "es", None),
    ],
)
def test_pace_controls_are_detected(text, language, expected):
    assert detect_control(text, language) == expected


def test_control_words_win_but_symptoms_survive():
    """'Slow down, my chest hurts' must still register the chest pain."""
    text = "slow down, I have chest pain"
    assert detect_control(text, "en") == "slower"
    assert "chest pain" in {s.label for s in extract(text, "en")}


# -- language parity -------------------------------------------------------
PARALLEL_COMPLAINTS = [
    (
        "fall with a head strike",
        {
            "en": "I fell in the kitchen and hit my head.",
            "es": "Me cai en la cocina y me golpee la cabeza.",
            "fr": "Je suis tombee dans la cuisine et je me suis cogne la tete.",
        },
        "fall",
    ),
    (
        "dizziness and no appetite",
        {
            "en": "I am dizzy and I have no appetite.",
            "es": "Tengo mareo y no tengo hambre.",
            "fr": "J'ai des vertiges et je n'ai pas faim.",
        },
        "dizziness",
    ),
    (
        "breathless with nausea",
        {
            "en": "I am short of breath and I have nausea.",
            "es": "Me falta el aire y tengo nausea.",
            "fr": "Je suis essouffle et j'ai des nausees.",
        },
        "shortness of breath",
    ),
]


@pytest.mark.parametrize("name,texts,expect_label", PARALLEL_COMPLAINTS)
def test_the_same_complaint_extracts_the_same_in_three_languages(
    name, texts, expect_label
):
    for language, text in texts.items():
        labels = {s.label for s in extract(text, language)}
        assert expect_label in labels, f"{name} in {language}: got {labels}"


@pytest.mark.parametrize("name,texts,_label", PARALLEL_COMPLAINTS)
def test_the_same_complaint_reaches_the_same_level_in_three_languages(
    client, name, texts, _label
):
    """The clinical answer cannot depend on which language you speak."""
    levels = {}
    for language, text in texts.items():
        client.post("/demo/reset")
        levels[language] = client.post(
            "/checkins",
            json={
                "senior_id": "sen_henriette",
                "source": "voice",
                "language": language,
                "text": text,
                "transcript_confidence": 0.93,
            },
        ).json()["evaluation"]["level"]
    assert len(set(levels.values())) == 1, f"{name}: level differs by language {levels}"


def test_french_intake_returns_french_to_the_patient_and_english_to_staff(client):
    ev = client.post(
        "/checkins",
        json={
            "senior_id": "sen_henriette",
            "source": "voice",
            "language": "fr",
            "text": "Je suis tombee dans la cuisine et je me suis cogne la tete.",
            "transcript_confidence": 0.91,
        },
    ).json()["evaluation"]

    assert ev["level"] == ActionLevel.GO_TO_ER
    assert "urgences" in ev["explanation"]
    assert "emergency department" in ev["explanation_en"]
    assert lint(ev["explanation"], "fr").ok


# -- accented transcripts --------------------------------------------------
# Deepgram returns properly accented text. The phrase lists are unaccented, so
# every one of these silently returned nothing before extraction.fold() existed
# -- a bug that would only ever have shown up on stage, with a live microphone.
ACCENTED = [
    ("es", "Me falta el aire y tengo náusea, y estoy sudando frío.",
     {"shortness of breath", "nausea"}),
    ("es", "Estoy muy mareada y con náuseas.", {"dizziness", "nausea"}),
    ("es", "Me caí en el baño y me golpeé la cabeza.", {"fall"}),
    ("fr", "Je suis tombée et je me suis cogné la tête.", {"fall"}),
    ("fr", "J'ai des vertiges et je n'ai pas d'appétit.",
     {"dizziness", "poor appetite"}),
    ("fr", "J'ai de la fièvre depuis hier.", {"fever"}),
]


@pytest.mark.parametrize("language,text,expected", ACCENTED)
def test_accented_transcripts_extract_the_same_as_unaccented(language, text, expected):
    labels = {s.label for s in extract(text, language)}
    assert expected <= labels, f"{text!r} -> {labels}"


def test_accents_do_not_change_the_action_level(client):
    """The same sentence, with and without accents, must land on the same rung."""
    plain = "Me falta el aire y tengo nausea, y estoy sudando frio."
    accented = "Me falta el aire y tengo náusea, y estoy sudando frío."

    levels = []
    for text in (plain, accented):
        client.post("/demo/reset")
        levels.append(
            client.post(
                "/checkins",
                json={"senior_id": "sen_rosa", "source": "voice", "language": "es",
                      "text": text, "transcript_confidence": 0.99},
            ).json()["evaluation"]["level"]
        )
    assert levels[0] == levels[1] == 3


def test_folding_leaves_non_latin_scripts_alone():
    from app.extraction import fold

    assert fold("请现在去急诊") == "请现在去急诊"
    assert fold("नमस्ते") == "नमस्ते"
    assert fold("NÁUSEA") == "nausea"


# -- Deepgram wiring -------------------------------------------------------
def test_listen_params_slow_the_endpointing_for_older_speakers():
    params = VOICE.deepgram_listen_params("fr")
    assert params["endpointing"] == 4000, "older speakers pause mid-sentence"
    assert params["language"] == "fr"
    assert params["interim_results"] is True


@pytest.mark.parametrize("language", ["en", "es", "fr"])
def test_languages_deepgram_can_speak(language):
    """Verified against GET /v1/models: aura-2 covers de en es fr it ja nl."""
    assert voice_output_available(language)
    assert VOICE.deepgram_speak_params(language)["model"] in DEEPGRAM_TTS_VOICE.values()


@pytest.mark.parametrize("language", ["zh", "pt", "hi"])
def test_languages_deepgram_can_hear_but_not_speak(language):
    """We never claim a spoken language Deepgram cannot actually speak."""
    assert not voice_output_available(language)
    assert VOICE.deepgram_speak_params(language) is None
    # ...but we can still listen in it, which is the point of the fallback.
    assert VOICE.deepgram_listen_params(language)["language"]


def test_seed_voice_support_matches_actual_deepgram_coverage(client):
    for senior in client.get("/seniors").json():
        assert senior["voice_output_supported"] == voice_output_available(
            senior["preferred_language"]
        ), f"{senior['id']} claims the wrong voice support"


def test_system_prompt_carries_the_rules_and_the_limits():
    prompt = system_prompt("fr", "Henriette Dubois")
    assert "French" in prompt
    assert "Henriette" in prompt
    assert "never diagnose" in prompt
    assert "never decide how urgent" in prompt.lower()
