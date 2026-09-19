"""Deepgram: real speech in, real speech out.

Everything here talks to the actual API. The coverage tables in persona.py were
checked against `GET https://api.deepgram.com/v1/models` on this account, not
against the marketing page:

    listen (nova-3): en es fr de it ja nl pt hi zh ru + many more
    speak  (aura-2): de en es fr it ja nl  -- and nothing else

That asymmetry is the whole reason `voice_output_available()` exists. A patient
whose language we can hear but not speak gets text, and the UI says so.

Two transcription paths, because the demo needs both:
  transcribe_url    - the UI uploaded audio somewhere and hands us a link
  transcribe_bytes  - the browser posts the recording straight to us

Both are prerecorded (batch). Live streaming runs over the Voice Agent socket,
whose Settings frame is built in llm.voice_agent_config.

Failure is never silent. A failed transcription raises, the endpoint turns it
into a 502, and the UI can fall back to typing -- which is a path we already
offer every patient anyway.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .config import get_settings
from .persona import VOICE, DEEPGRAM_STT_LANGUAGE, DEEPGRAM_TTS_VOICE

log = logging.getLogger(__name__)

LISTEN_URL = "https://api.deepgram.com/v1/listen"
SPEAK_URL = "https://api.deepgram.com/v1/speak"
TIMEOUT_S = 45.0

# Below this, we do not trust what we heard. The ladder already escalates a
# rung on low confidence, so a bad transcript makes us more careful, not less.
LOW_CONFIDENCE = 0.6


class DeepgramError(RuntimeError):
    """Raised so the caller can fall back to text rather than guess."""


@dataclass
class Transcript:
    text: str
    confidence: float
    language: str
    model: str
    duration_s: float | None = None
    detected_language: str | None = None

    @property
    def is_low_confidence(self) -> bool:
        return self.confidence < LOW_CONFIDENCE


def is_enabled() -> bool:
    return bool(get_settings().deepgram_api_key)


def _headers() -> dict[str, str]:
    key = get_settings().deepgram_api_key
    if not key:
        raise DeepgramError("DEEPGRAM_API_KEY is not set")
    return {"Authorization": f"Token {key}"}


def listen_params(language: str, detect: bool = False) -> dict:
    """Senior-tuned STT parameters. See persona.VoiceProfile for the why."""
    params = VOICE.deepgram_listen_params(language)
    # The batch endpoint has no use for streaming-only options.
    for streaming_only in ("interim_results", "endpointing", "utterance_end_ms",
                           "vad_events"):
        params.pop(streaming_only, None)
    if detect:
        # Let Deepgram decide when we do not know what they will speak -- the
        # kiosk case, where nobody has picked a language yet.
        params.pop("language", None)
        params["detect_language"] = True
    return params


def _parse(payload: dict, language: str, model: str) -> Transcript:
    try:
        channel = payload["results"]["channels"][0]
        best = channel["alternatives"][0]
    except (KeyError, IndexError) as exc:
        raise DeepgramError(f"unexpected Deepgram response shape: {exc}") from exc

    return Transcript(
        text=(best.get("transcript") or "").strip(),
        confidence=float(best.get("confidence") or 0.0),
        language=language,
        model=model,
        duration_s=(payload.get("metadata") or {}).get("duration"),
        detected_language=channel.get("detected_language"),
    )


def transcribe_url(audio_url: str, language: str = "en", detect: bool = False) -> Transcript:
    params = listen_params(language, detect)
    try:
        with httpx.Client(timeout=TIMEOUT_S) as client:
            resp = client.post(
                LISTEN_URL,
                headers={**_headers(), "Content-Type": "application/json"},
                params=params,
                json={"url": audio_url},
            )
            resp.raise_for_status()
            return _parse(resp.json(), language, params.get("model", "nova-3"))
    except httpx.HTTPError as exc:
        raise DeepgramError(f"transcription failed: {exc}") from exc


def transcribe_bytes(
    audio: bytes,
    content_type: str = "audio/wav",
    language: str = "en",
    detect: bool = False,
) -> Transcript:
    if not audio:
        raise DeepgramError("empty audio")
    params = listen_params(language, detect)
    try:
        with httpx.Client(timeout=TIMEOUT_S) as client:
            resp = client.post(
                LISTEN_URL,
                headers={**_headers(), "Content-Type": content_type},
                params=params,
                content=audio,
            )
            resp.raise_for_status()
            return _parse(resp.json(), language, params.get("model", "nova-3"))
    except httpx.HTTPError as exc:
        raise DeepgramError(f"transcription failed: {exc}") from exc


def speak(text: str, language: str = "en", encoding: str = "linear16") -> bytes:
    """Synthesize the reply. Raises when the language has no Aura voice.

    The caller is expected to have checked `voice_output_available()` first --
    this raise is the backstop, not the interface.
    """
    voice = DEEPGRAM_TTS_VOICE.get(language)
    if not voice:
        raise DeepgramError(
            f"no Deepgram voice for {language!r}; send text instead "
            f"(speakable: {sorted(DEEPGRAM_TTS_VOICE)})"
        )
    params = {"model": voice, "encoding": encoding}
    if encoding == "linear16":
        params["sample_rate"] = 24000
    try:
        with httpx.Client(timeout=TIMEOUT_S) as client:
            resp = client.post(
                SPEAK_URL,
                headers={**_headers(), "Content-Type": "application/json"},
                params=params,
                json={"text": text},
            )
            resp.raise_for_status()
            return resp.content
    except httpx.HTTPError as exc:
        raise DeepgramError(f"speech synthesis failed: {exc}") from exc


def coverage() -> dict:
    """What we can actually hear and say. Sourced from the models endpoint."""
    return {
        "listen": sorted(DEEPGRAM_STT_LANGUAGE),
        "speak": sorted(DEEPGRAM_TTS_VOICE),
        "text_only": sorted(set(DEEPGRAM_STT_LANGUAGE) - set(DEEPGRAM_TTS_VOICE)),
        "stt_model": "nova-3",
        "tts_voices": dict(sorted(DEEPGRAM_TTS_VOICE.items())),
        "enabled": is_enabled(),
    }
