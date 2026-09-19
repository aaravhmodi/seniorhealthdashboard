"""OpenAI calls, with the deterministic path always underneath.

Three rules hold everywhere in this file:

1. **The model never sets the level.** It extracts and it phrases. `ladder.py`
   decides. A compromised or confused model cannot make someone stay home.
2. **Extraction is additive.** LLM symptoms are unioned with the lexicon's, never
   substituted for them. The model can find what the lexicon missed; it cannot
   remove what the lexicon caught, so a red flag cannot be argued away.
3. **Every generation is linted and every failure falls back.** No key, timeout,
   bad JSON, elderspeak, a language the model drifted out of -- all of it lands
   on the template. The demo cannot be broken by an API.

Set OPENAI_API_KEY in backend/.env to turn this on; with it empty the whole
system still runs, just deterministically.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import httpx

from .config import get_settings
from .extraction import extract as lexicon_extract
from .persona import LANGUAGE_NAMES, STYLE_RULES, lint, system_prompt
from .retrieval import RetrievedContext, build_context
from .schemas import ActionLevel, EvidenceCard, RedFlag, Symptom

log = logging.getLogger(__name__)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
TIMEOUT_S = 25.0  # reasoning models are slower; the fallback covers a miss


@dataclass
class LLMResult:
    """Every call reports whether the model was actually used, so the UI can
    show it and the tests can assert the fallback happened."""

    value: object
    used_model: bool
    fallback_reason: str | None = None


def is_enabled() -> bool:
    return bool(get_settings().openai_api_key)


# Newer reasoning models renamed the token budget and refuse a custom
# temperature. They also spend tokens thinking before they answer, so the
# budget has to cover the reasoning or the content comes back empty.
REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _is_reasoning_model(model: str) -> bool:
    return model.lower().startswith(REASONING_PREFIXES)


def _chat(messages: list[dict], json_mode: bool = False, max_tokens: int = 400) -> str | None:
    settings = get_settings()
    if not settings.openai_api_key:
        return None
    model = settings.openai_model
    payload: dict = {"model": model, "messages": messages}
    if _is_reasoning_model(model):
        payload["max_completion_tokens"] = max(max_tokens * 4, 1200)
    else:
        payload["max_tokens"] = max_tokens
        payload["temperature"] = 0.2
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        with httpx.Client(timeout=TIMEOUT_S) as client:
            resp = client.post(
                OPENAI_URL,
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"].get("content")
            # A reasoning model that burned its whole budget thinking returns an
            # empty string. Treat that as a failure so we fall back cleanly.
            return content or None
    except Exception as exc:
        log.warning("openai call failed, falling back: %s", exc)
        return None


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------
EXTRACT_SYSTEM = """You turn what an older adult said into structured data.

Return JSON only:
{"symptoms": [{"label": "...", "severity": 0-10 or null,
               "onset": "short phrase or null", "is_new": true/false/null}],
 "vitals": {"systolic": null, "diastolic": null, "heart_rate": null,
            "temp_c": null, "spo2": null, "glucose_mgdl": null},
 "meds_taken_today": ["..."]}

Rules:
- Labels are plain English, lowercase, even when the person spoke another
  language: chest pain, shortness of breath, dizziness, confusion, weakness one
  side, fall, fever, nausea, poor appetite, swelling legs, urinary symptoms,
  trouble sleeping, back pain, headache, bleeding. Add others if needed.
- Record only what they said. Never infer a symptom they did not mention and
  never soften one they did.
- "is_new" is true only if they said it is new, sudden, or the first time.
- If they mention no symptom at all, return an empty list.
- No diagnosis, no advice, no commentary. JSON only."""


def extract_symptoms(text: str | None, language: str = "en") -> LLMResult:
    """Lexicon first, then the model, then the union of both."""
    baseline = lexicon_extract(text, language)
    if not text or not is_enabled():
        return LLMResult(baseline, used_model=False,
                         fallback_reason="no api key" if text else "no text")

    raw = _chat(
        [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user", "content": f"Language: {language}\nThey said: {text}"},
        ],
        json_mode=True,
        max_tokens=500,
    )
    if raw is None:
        return LLMResult(baseline, used_model=False, fallback_reason="call failed")

    try:
        data = json.loads(raw)
        model_symptoms = [
            Symptom(
                label=str(s["label"]).strip().lower(),
                severity=s.get("severity"),
                onset=s.get("onset"),
                is_new=s.get("is_new"),
            )
            for s in data.get("symptoms", [])
            if s.get("label")
        ]
    except Exception as exc:
        return LLMResult(baseline, used_model=False, fallback_reason=f"bad json: {exc}")

    # Union, lexicon wins on conflict: it is the layer the rules were written
    # against, so it must never be silently replaced.
    merged = {s.label: s for s in model_symptoms}
    merged.update({s.label: s for s in baseline})
    return LLMResult(list(merged.values()), used_model=True)


def extract_vitals_and_meds(text: str | None, language: str = "en") -> dict:
    """Optional structured extras. Absent is always a safe answer here."""
    if not text or not is_enabled():
        return {}
    raw = _chat(
        [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user", "content": f"Language: {language}\nThey said: {text}"},
        ],
        json_mode=True,
    )
    try:
        data = json.loads(raw or "{}")
        return {
            "vitals": {k: v for k, v in (data.get("vitals") or {}).items() if v is not None},
            "meds_taken_today": data.get("meds_taken_today") or [],
        }
    except Exception:
        return {}


# --------------------------------------------------------------------------
# Explanation
# --------------------------------------------------------------------------
LEVEL_INSTRUCTION = {
    1: "Tell them nothing here needs a call today.",
    2: "Tell them to call their clinic or pharmacist today. Say it is not an emergency.",
    3: "Tell them to go to the emergency department now, and not to drive themselves.",
    4: "Tell them to call nine one one now and stay where they are.",
}


def explain(
    level: ActionLevel,
    language: str,
    senior_name: str,
    flags: list[RedFlag],
    evidence: list[EvidenceCard],
    template_fallback: str,
    context: RetrievedContext | None = None,
) -> LLMResult:
    """Rephrase the decision in the senior's language. Never change it.

    Returns the template unchanged unless the model produced something that is
    in the right language, obeys the style contract, and still says the same
    thing.
    """
    if not is_enabled():
        return LLMResult(template_fallback, used_model=False, fallback_reason="no api key")

    reasons = "; ".join(f.label for f in flags) or "no specific red flag"
    facts = "\n".join(
        f"- {c.title}: {c.detail} (source: {c.source})" for c in evidence[:3]
    ) or "- none"
    block = context.as_prompt_block() if context and not context.is_empty() else "(none)"

    raw = _chat(
        [
            {"role": "system", "content": system_prompt(language, senior_name)},
            {
                "role": "user",
                "content": (
                    f"Decision already made, do not change it: level {int(level)}.\n"
                    f"{LEVEL_INSTRUCTION[int(level)]}\n"
                    f"What triggered it: {reasons}\n"
                    f"Supporting facts you may refer to:\n{facts}\n"
                    f"Retrieved context you may refer to:\n{block}\n\n"
                    f"Write the message. Reply only in "
                    f"{LANGUAGE_NAMES.get(language, 'English')}. "
                    f"Do not add anything that is not above."
                ),
            },
        ],
        max_tokens=220,
    )
    if not raw:
        return LLMResult(template_fallback, used_model=False, fallback_reason="call failed")

    candidate = raw.strip()
    result = lint(candidate, language)
    if not result.ok:
        log.warning("llm explanation rejected by persona lint: %s", result.problems)
        return LLMResult(
            template_fallback,
            used_model=False,
            fallback_reason=f"style: {result.problems[0]}",
        )
    if not _says_the_same_thing(candidate, level, language):
        return LLMResult(
            template_fallback, used_model=False, fallback_reason="drifted off the action"
        )
    return LLMResult(candidate, used_model=True)


# The action word must survive, in the language we asked for. This is a cheap
# guard against the model answering in English, or hedging a 911 into a "maybe".
ACTION_WORDS: dict[int, dict[str, tuple[str, ...]]] = {
    1: {"en": ("no call", "nothing", "usual"), "es": ("no hace falta", "rutina"),
        "fr": ("rien", "habitudes"), "zh": ("不需要", "平常"),
        "pt": ("nada", "rotina"), "hi": ("ज़रूरत नहीं", "दिनचर्या")},
    2: {"en": ("call",), "es": ("llame", "llamar"), "fr": ("appelez", "appeler"),
        "zh": ("打电话", "电话"), "pt": ("ligue", "ligar"), "hi": ("फ़ोन",)},
    3: {"en": ("emergency", "hospital"), "es": ("urgencias", "hospital"),
        "fr": ("urgences", "hopital"), "zh": ("急诊", "医院"),
        "pt": ("pronto-socorro", "hospital"), "hi": ("इमरजेंसी", "अस्पताल")},
    4: {"en": ("nine one one", "911"), "es": ("nueve uno uno", "911"),
        "fr": ("neuf un un", "911"), "zh": ("九一一", "911"),
        "pt": ("nove um um", "911"), "hi": ("नौ एक एक", "911")},
}


def _says_the_same_thing(text: str, level: ActionLevel, language: str) -> bool:
    words = ACTION_WORDS[int(level)].get(language) or ACTION_WORDS[int(level)]["en"]
    low = text.lower()
    return any(w.lower() in low for w in words)


# --------------------------------------------------------------------------
# Deepgram Voice Agent
# --------------------------------------------------------------------------
def voice_agent_config(senior, last_checkins=None) -> dict:
    """Settings frame for Deepgram's Voice Agent API.

    The agent listens in the senior's language, thinks with our system prompt
    and their retrieved history, and speaks only where a voice exists. Note the
    endpointing: older speakers pause mid-sentence and a default 1s window talks
    over them.
    """
    from .persona import VOICE, TEXT_FALLBACK_NOTE, voice_output_available

    language = senior.preferred_language
    context = build_context(
        query="recent symptoms and medicines",
        senior_id=senior.id,
        language=language,
        k=6,
    )
    settings = get_settings()

    speak = VOICE.deepgram_speak_params(language)
    return {
        "type": "Settings",
        "audio": {
            "input": {"encoding": "linear16", "sample_rate": 16000},
            "output": {"encoding": "linear16", "sample_rate": 24000} if speak else None,
        },
        "agent": {
            "language": language,
            "listen": {"provider": {"type": "deepgram",
                                    **VOICE.deepgram_listen_params(language)}},
            "think": {
                "provider": {"type": "open_ai", "model": settings.openai_model},
                "prompt": (
                    system_prompt(language, senior.display_name)
                    + "\n\nYou may use only these facts about them:\n"
                    + context.as_prompt_block()
                ),
            },
            "speak": {"provider": {"type": "deepgram", **speak}} if speak else None,
            "greeting": _greeting(senior, voice_output_available(language),
                                  TEXT_FALLBACK_NOTE.get(language, "")),
        },
        "_meta": {
            "voice_output": bool(speak),
            "context_chunks": len(context.chunks),
            "citations": context.citations,
            "style_rules": list(STYLE_RULES),
        },
    }


GREETINGS = {
    "en": "Good morning {name}. How are you doing today?",
    "es": "Buenos dias {name}. Como se siente hoy?",
    "fr": "Bonjour {name}. Comment allez-vous aujourd'hui ?",
    "zh": "{name}，早上好。您今天怎么样？",
    "pt": "Bom dia {name}. Como voce esta hoje?",
    "hi": "नमस्ते {name}। आज आप कैसे हैं?",
}


def _greeting(senior, has_voice: bool, text_note: str) -> str:
    name = senior.display_name.split()[0]
    line = GREETINGS.get(senior.preferred_language, GREETINGS["en"]).format(name=name)
    return line if has_voice else f"{line} {text_note}".strip()
