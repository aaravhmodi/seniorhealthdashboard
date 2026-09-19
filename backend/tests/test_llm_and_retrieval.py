"""The LLM and retrieval layer.

The point of these tests is not that the model is clever. It is that when the
model is wrong, slow, offline, or talking like a children's TV host, the system
still gives the patient the right instruction. Every test here is a way the
model can fail.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import llm, retrieval
from app.config import get_settings
from app.main import app
from app.persona import lint
from app.schemas import ActionLevel, RedFlag, Symptom


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.post("/demo/reset")
        yield c


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def fake_chat(monkeypatch, reply):
    """Replace the transport, keep every guard around it."""
    calls = []

    def _fake(messages, json_mode=False, max_tokens=400):
        calls.append({"messages": messages, "json_mode": json_mode})
        return reply(messages) if callable(reply) else reply

    monkeypatch.setattr(llm, "_chat", _fake)
    return calls


CARDIAC = RedFlag(
    code="cardiac_acs",
    label="Possible cardiac event (incl. atypical presentation)",
    matched_on=["atypical:breathless + nausea/diaphoresis"],
    forces_level=ActionLevel.GO_TO_ER,
)


# -- the model is off ------------------------------------------------------
def test_without_a_key_everything_is_deterministic():
    assert not llm.is_enabled()
    result = llm.explain(
        level=ActionLevel.GO_TO_ER, language="es", senior_name="Rosa Mendez",
        flags=[CARDIAC], evidence=[], template_fallback="TEMPLATE",
    )
    assert result.value == "TEMPLATE"
    assert result.used_model is False
    assert result.fallback_reason == "no api key"


def test_checkin_reports_that_no_model_was_used(client):
    ev = client.post(
        "/checkins",
        json={"senior_id": "sen_rosa", "language": "es", "text": "Tengo mareo."},
    ).json()["evaluation"]
    assert ev["llm_used"] is False
    assert ev["llm_fallback_reason"] == "no api key"


# -- the model is on, and behaving ----------------------------------------
def test_a_good_generation_is_used(with_key, monkeypatch):
    fake_chat(monkeypatch, "Vaya ahora a urgencias. No maneje usted.")
    result = llm.explain(
        level=ActionLevel.GO_TO_ER, language="es", senior_name="Rosa Mendez",
        flags=[CARDIAC], evidence=[], template_fallback="TEMPLATE",
    )
    assert result.used_model is True
    assert "urgencias" in result.value


# -- the model misbehaves, one failure mode per test ----------------------
def test_elderspeak_is_rejected(with_key, monkeypatch):
    fake_chat(monkeypatch, "Okay sweetie! Let's take our medicine and go to urgencias!")
    result = llm.explain(
        level=ActionLevel.GO_TO_ER, language="es", senior_name="Rosa Mendez",
        flags=[CARDIAC], evidence=[], template_fallback="TEMPLATE",
    )
    assert result.value == "TEMPLATE"
    assert "style" in (result.fallback_reason or "")


def test_a_softened_instruction_is_rejected(with_key, monkeypatch):
    """The nightmare case: the model turns 'go now' into 'maybe rest a bit'."""
    fake_chat(monkeypatch, "Descanse un poco y vea como se siente manana.")
    result = llm.explain(
        level=ActionLevel.GO_TO_ER, language="es", senior_name="Rosa Mendez",
        flags=[CARDIAC], evidence=[], template_fallback="TEMPLATE",
    )
    assert result.value == "TEMPLATE"
    assert result.fallback_reason == "drifted off the action"


def test_wrong_language_is_rejected(with_key, monkeypatch):
    """Answering a Chinese speaker in English is a failure, not a near miss."""
    fake_chat(monkeypatch, "Please go to the emergency department now.")
    result = llm.explain(
        level=ActionLevel.GO_TO_ER, language="zh", senior_name="Wei Chen",
        flags=[CARDIAC], evidence=[], template_fallback="TEMPLATE",
    )
    assert result.value == "TEMPLATE"


def test_a_dead_api_falls_back(with_key, monkeypatch):
    fake_chat(monkeypatch, None)
    result = llm.explain(
        level=ActionLevel.CALL_911, language="en", senior_name="Walter Boyd",
        flags=[], evidence=[], template_fallback="TEMPLATE",
    )
    assert result.value == "TEMPLATE"
    assert result.fallback_reason == "call failed"


# -- extraction is additive, never subtractive ----------------------------
def test_the_model_cannot_delete_a_lexicon_symptom(with_key, monkeypatch):
    """A model that returns nothing must not erase a red flag the lexicon saw."""
    fake_chat(monkeypatch, json.dumps({"symptoms": []}))
    result = llm.extract_symptoms("I fell and hit my head", "en")
    assert "fall" in {s.label for s in result.value}


def test_the_model_can_add_what_the_lexicon_missed(with_key, monkeypatch):
    fake_chat(monkeypatch, json.dumps(
        {"symptoms": [{"label": "cold sweating", "severity": None,
                       "onset": None, "is_new": True}]}
    ))
    result = llm.extract_symptoms("I fell and hit my head", "en")
    labels = {s.label for s in result.value}
    assert {"fall", "cold sweating"} <= labels
    assert result.used_model is True


def test_malformed_json_falls_back_to_the_lexicon(with_key, monkeypatch):
    fake_chat(monkeypatch, "not json at all")
    result = llm.extract_symptoms("I fell and hit my head", "en")
    assert "fall" in {s.label for s in result.value}
    assert result.used_model is False
    assert "bad json" in result.fallback_reason


def test_a_hallucinated_symptom_still_cannot_lower_the_level(client, with_key, monkeypatch):
    """Even if the model invents reassuring nonsense, rules own the floor."""
    fake_chat(monkeypatch, json.dumps({"symptoms": [{"label": "feeling great"}]}))
    ev = client.post(
        "/checkins",
        json={
            "senior_id": "sen_walter", "language": "en",
            "text": "My face is drooping and my arm is weak on one side.",
        },
    ).json()["evaluation"]
    assert ev["level"] == ActionLevel.CALL_911


# -- reasoning-model parameter handling -----------------------------------
@pytest.mark.parametrize(
    "model,expected_key",
    [
        ("gpt-5.6-luna", "max_completion_tokens"),
        ("gpt-4o-mini", "max_tokens"),
        ("o3-mini", "max_completion_tokens"),
    ],
)
def test_token_budget_parameter_matches_the_model_family(model, expected_key, monkeypatch):
    """gpt-5.6-luna rejects max_tokens and any temperature but the default."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("OPENAI_MODEL", model)
    get_settings.cache_clear()
    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self): ...

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class FakeClient:
        def __init__(self, **kw): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False

        def post(self, url, headers=None, json=None):
            captured.update(json)
            return FakeResponse()

    monkeypatch.setattr(llm.httpx, "Client", FakeClient)
    llm._chat([{"role": "user", "content": "hi"}])

    assert expected_key in captured
    if expected_key == "max_completion_tokens":
        assert "temperature" not in captured, "this family only accepts the default"
        assert "max_tokens" not in captured


# -- retrieval -------------------------------------------------------------
def test_patient_history_is_private_to_its_owner(client):
    client.post("/retrieval/reindex")
    rosa = retrieval.build_context("dizziness", senior_id="sen_rosa", language="es")
    walter = retrieval.build_context("dizziness", senior_id="sen_walter", language="en")

    owners = {c.senior_id for c in rosa.chunks if c.senior_id}
    assert owners in ({"sen_rosa"}, set())
    assert all(c.senior_id in (None, "sen_walter") for c in walter.chunks)


def test_retrieval_finds_the_relevant_guideline(client):
    client.post("/retrieval/reindex")
    results = client.get(
        "/retrieval/search",
        params={"q": "fell and hit head while on a blood thinner",
                "senior_id": "sen_chen", "k": 5},
    ).json()
    assert results["indexed_chunks"] > 0
    assert any("blood thinner" in r["text"] for r in results["results"])


def test_every_indexed_stat_carries_its_sample_size():
    """A number without an n never enters the index, so the agent cannot quote
    a bare percentage at a patient."""
    added = retrieval.ingest_cohort_stats([
        {"id": "good", "text": "48% admitted", "source": "NEISS", "n": 5387},
        {"id": "bare", "text": "48% admitted", "source": "NEISS"},
    ])
    assert added == 1


def test_context_block_is_numbered_and_cited(client):
    client.post("/retrieval/reindex")
    context = retrieval.build_context("dizziness", senior_id="sen_rosa", language="es")
    block = context.as_prompt_block()
    assert block.startswith("[1]")
    assert "source:" in block
    assert context.citations


def test_empty_index_degrades_quietly():
    retrieval.store.clear()
    context = retrieval.build_context("anything", senior_id="sen_rosa")
    assert context.is_empty()
    assert context.as_prompt_block() == "(no context retrieved)"


# -- voice agent config ----------------------------------------------------
def test_voice_agent_config_is_tuned_for_older_speakers(client):
    config = client.get("/voice/agent-config/sen_rosa").json()
    listen = config["agent"]["listen"]["provider"]
    assert listen["endpointing"] == 4000
    assert config["agent"]["language"] == "es"
    assert config["_meta"]["voice_output"] is True
    assert "Rosa" in config["agent"]["greeting"]


def test_voice_agent_falls_back_to_text_where_deepgram_has_no_voice(client):
    """Mandarin can be heard but not spoken: Wei reads the reply."""
    config = client.get("/voice/agent-config/sen_chen").json()
    assert config["_meta"]["voice_output"] is False
    assert config["agent"]["speak"] is None
    assert "打字" in config["agent"]["greeting"], "must offer the text path"


def test_voice_agent_speaks_french(client):
    config = client.get("/voice/agent-config/sen_henriette").json()
    assert config["_meta"]["voice_output"] is True
    assert config["agent"]["speak"]["provider"]["model"] == "aura-2-agathe-fr"


def test_voice_agent_prompt_is_limited_to_retrieved_facts(client):
    client.post("/retrieval/reindex")
    config = client.get("/voice/agent-config/sen_rosa").json()
    prompt = config["agent"]["think"]["prompt"]
    assert "You may use only these facts" in prompt
    assert "never diagnose" in prompt
    assert config["_meta"]["context_chunks"] > 0


# -- live, opt-in ----------------------------------------------------------
@pytest.mark.live
def test_live_openai_round_trip(monkeypatch):
    """pytest -m live --live. Confirms the configured model still works."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    if not llm.is_enabled():
        pytest.skip("no OPENAI_API_KEY configured")

    result = llm.explain(
        level=ActionLevel.GO_TO_ER, language="es", senior_name="Rosa Mendez",
        flags=[CARDIAC], evidence=[], template_fallback="TEMPLATE",
    )
    assert result.used_model, result.fallback_reason
    assert lint(result.value, "es").ok
