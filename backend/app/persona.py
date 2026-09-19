"""The voice: how this system talks to an older adult.

This is not decoration. Every LLM prompt, every TTS setting and every piece of
patient-facing copy goes through here, and `lint()` is enforced in the tests.

What the evidence says (sources in docs/PERSONA.md):

* **Elderspeak backfires.** Higher pitch, sing-song prosody, diminutives
  ("sweetie", "good girl"), collective pronouns ("how are WE feeling today")
  and baby-simple grammar are read as patronizing. Older adults withdraw, and
  in dementia care elderspeak measurably increases resistance to care. It also
  confers no comprehension benefit. So: plain adult speech, never cute.
* **Slow the delivery, not the respect.** Around 135 wpm for spoken output, and
  a ~4 second silence timeout before assuming the person is done -- older
  speakers pause mid-sentence more, and a 1.5s barge-in cuts them off.
* **Age-related hearing loss hits consonants and high frequencies first.** A
  lower-pitched voice carries better than a bright one. Short sentences, one
  idea each, and the important word at the end where it is not clipped.
* **Confirm anything irreversible, out loud.** And always offer a non-voice
  path, because a meaningful share of older users distrust voice input.
* **Give control of the pace**: repeat, slow down, and start over must always
  work, in any language, at any point.
* **Teach-back beats "do you understand?"** Asking someone to say the plan back
  reveals a misunderstanding that a yes/no question hides.

None of this means talking down. The rule of thumb: speak the way you would to
a competent adult who is across the room and a little hard of hearing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Speech delivery
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class VoiceProfile:
    """Deepgram (and any other TTS) settings, in one place."""

    speech_rate_wpm: int = 135          # vs ~160 default; slower, not sluggish
    tts_speed: float = 0.85             # multiplier for the provider's default
    endpointing_ms: int = 4000          # silence before we assume they finished
    utterance_end_ms: int = 2000        # mid-turn pause tolerance
    interim_results: bool = True        # so the UI shows we are listening
    prefer_lower_pitch: bool = True     # consonant clarity with presbycusis
    max_sentence_words: int = 14
    max_reply_sentences: int = 3

    def deepgram_listen_params(self, language: str) -> dict:
        """Params for Deepgram streaming STT."""
        return {
            "model": "nova-3",
            "language": DEEPGRAM_STT_LANGUAGE.get(language, "multi"),
            "smart_format": True,
            "punctuate": True,
            "interim_results": self.interim_results,
            "endpointing": self.endpointing_ms,
            "utterance_end_ms": self.utterance_end_ms,
            "vad_events": True,
        }

    def deepgram_speak_params(self, language: str) -> dict | None:
        """Params for Deepgram TTS, or None when we must fall back to text.

        Deepgram's spoken output covers far fewer languages than its listening
        does. Returning None is the signal to render text instead of pretending.
        """
        voice = DEEPGRAM_TTS_VOICE.get(language)
        if not voice:
            return None
        return {"model": voice, "encoding": "linear16", "sample_rate": 24000}


VOICE = VoiceProfile()

# Listening is broad; speaking is not. Verify both at the Deepgram booth before
# promising a language on stage.
DEEPGRAM_STT_LANGUAGE: dict[str, str] = {
    "en": "en-US", "es": "es", "fr": "fr", "pt": "pt", "zh": "zh", "hi": "hi",
}

DEEPGRAM_TTS_VOICE: dict[str, str] = {
    "en": "aura-2-asteria-en",
    "es": "aura-2-celeste-es",
    # fr / zh / hi have no confirmed Aura voice: text fallback, and the UI says so.
}

SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "es", "fr", "zh", "pt", "hi")

LANGUAGE_NAMES: dict[str, str] = {
    "en": "English", "es": "Spanish", "fr": "French",
    "zh": "Chinese", "pt": "Portuguese", "hi": "Hindi",
}


def voice_output_available(language: str) -> bool:
    return language in DEEPGRAM_TTS_VOICE


# --------------------------------------------------------------------------
# Style contract -- shared by the LLM prompts and by our own copy
# --------------------------------------------------------------------------
STYLE_RULES: tuple[str, ...] = (
    "Speak to a capable adult. Never use pet names, diminutives, or baby talk.",
    "Never say 'we' about something only they are doing ('how are we feeling').",
    "One idea per sentence. Fourteen words or fewer. Three sentences or fewer.",
    "Use everyday words. Say 'heart attack', not 'myocardial infarction'.",
    "Say the action first, then the reason. 'Call your clinic today. Your "
    "dizziness is getting worse.'",
    "Never name a diagnosis and never say what is wrong with them. You describe "
    "what to do and why it is worth doing.",
    "Never promise that something is nothing. If unsure, say you are being careful.",
    "No emoji, no exclamation marks, no cheerleading.",
    "Say numbers plainly: 'nine one one', not '911'.",
)

# Teach-back belongs with a plan, not with "nothing to do today" -- asking
# someone to repeat back that nothing is wrong is the kind of hollow ritual
# that makes a system feel like a form. Levels 2 and above only.
TEACH_BACK_RULE = (
    "Close by asking them to say the plan back to you, so a misunderstanding "
    "surfaces."
)

# Patterns that mean we slipped into elderspeak. Tested, not aspirational.
ELDERSPEAK_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(sweetie|honey|dear|dearie|sweetheart|good (girl|boy)|young lady)\b",
     "pet name"),
    (r"\bhow are we\b|\bare we feeling\b|\blet's take our\b|\bour medicine\b",
     "collective pronoun"),
    (r"\b(silly|oopsie|uh-oh|there you go|good job|well done)\b", "baby talk"),
    (r"!{1,}", "exclamation mark"),
    (r"[\U0001F300-\U0001FAFF☀-➿]", "emoji"),
)

# Words a patient-facing string should not contain. Clinician strings may.
JARGON: tuple[tuple[str, str], ...] = (
    ("myocardial infarction", "heart attack"),
    ("cerebrovascular accident", "stroke"),
    ("syncope", "fainting"),
    ("dyspnea", "trouble breathing"),
    ("diaphoresis", "sweating"),
    ("hypotension", "low blood pressure"),
    ("polypharmacy", "your list of medicines"),
    ("delirium", "sudden confusion"),
    ("acuity", "how urgent this is"),
    ("differential", "possible causes"),
    ("triage", "sorting by urgency"),
)

SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|(?<=[。！？])")


@dataclass
class LintResult:
    ok: bool
    problems: list[str] = field(default_factory=list)


def lint(text: str, language: str = "en", strict_length: bool = True) -> LintResult:
    """Check a patient-facing string against the style contract.

    Used in tests and available at runtime to reject a bad LLM generation
    before it ever reaches a speaker.
    """
    problems: list[str] = []
    low = text.lower()

    for pattern, why in ELDERSPEAK_PATTERNS:
        if re.search(pattern, low):
            problems.append(f"elderspeak ({why})")

    for term, plain in JARGON:
        if term in low:
            problems.append(f"jargon '{term}' -- say '{plain}'")

    if strict_length and language in ("en", "es", "fr", "pt"):
        sentences = [s for s in SENTENCE_SPLIT.split(text.strip()) if s.strip()]
        if len(sentences) > VOICE.max_reply_sentences + 1:
            problems.append(f"{len(sentences)} sentences (limit {VOICE.max_reply_sentences + 1})")
        for s in sentences:
            words = len(s.split())
            if words > VOICE.max_sentence_words + 6:
                problems.append(f"{words}-word sentence: '{s[:40]}...'")

    return LintResult(ok=not problems, problems=problems)


# --------------------------------------------------------------------------
# Pace controls -- must work in every language, at any point in the call
# --------------------------------------------------------------------------
CONTROL_PHRASES: dict[str, dict[str, list[str]]] = {
    "repeat": {
        "en": ["say that again", "repeat", "what did you say", "pardon"],
        "es": ["repita", "otra vez", "como dijo", "mande"],
        "fr": ["repetez", "repete", "encore", "pardon", "comment"],
        "zh": ["再说一遍", "重复", "什么"],
        "pt": ["repita", "de novo", "como"],
        "hi": ["फिर से", "दोबारा"],
    },
    "slower": {
        "en": ["slow down", "slower", "too fast"],
        "es": ["mas despacio", "mas lento", "muy rapido"],
        "fr": ["moins vite", "plus lentement", "trop vite"],
        "zh": ["慢一点", "太快"],
        "pt": ["mais devagar", "muito rapido"],
        "hi": ["धीरे", "आहिस्ता"],
    },
    "human": {
        "en": ["talk to a person", "real person", "human", "nurse", "my daughter"],
        "es": ["una persona", "hablar con alguien", "enfermera", "mi hija"],
        "fr": ["une personne", "parler a quelqu'un", "infirmiere", "ma fille"],
        "zh": ["找人", "护士", "真人"],
        "pt": ["uma pessoa", "enfermeira", "minha filha"],
        "hi": ["किसी से बात", "नर्स"],
    },
    "start_over": {
        "en": ["start over", "cancel", "never mind", "go back"],
        "es": ["empezar de nuevo", "cancelar", "olvidelo", "regresar"],
        "fr": ["recommencer", "annuler", "laissez tomber", "retour"],
        "zh": ["重新开始", "取消"],
        "pt": ["comecar de novo", "cancelar"],
        "hi": ["फिर से शुरू", "रद्द"],
    },
}


def detect_control(text: str, language: str = "en") -> str | None:
    """Pace and escape hatches beat everything else, including symptom parsing.

    If someone says "slow down" while describing chest pain, we slow down and
    keep the chest pain. The caller handles the control, then continues.
    """
    low = (text or "").lower()
    for intent, per_language in CONTROL_PHRASES.items():
        for phrase in per_language.get(language, []) + per_language["en"]:
            if phrase in low:
                return intent
    return None


# --------------------------------------------------------------------------
# Teach-back and confirmation
# --------------------------------------------------------------------------
TEACH_BACK: dict[str, str] = {
    "en": "Tell me what you are going to do next, in your own words.",
    "es": "Digame que va a hacer ahora, en sus propias palabras.",
    "fr": "Dites-moi ce que vous allez faire maintenant, avec vos mots.",
    "zh": "请用您自己的话说一遍，接下来您要做什么。",
    "pt": "Diga o que voce vai fazer agora, com suas palavras.",
    "hi": "अब आप क्या करेंगे, अपने शब्दों में बताइए।",
}

CONFIRM_BEFORE_SENDING: dict[str, str] = {
    "en": "I will tell {name} about this. Is that alright?",
    "es": "Voy a avisar a {name}. Le parece bien?",
    "fr": "Je vais prevenir {name}. Est-ce que cela vous convient ?",
    "zh": "我会告诉{name}。可以吗？",
    "pt": "Vou avisar {name}. Tudo bem?",
    "hi": "मैं {name} को बता दूँगी। ठीक है?",
}

TEXT_FALLBACK_NOTE: dict[str, str] = {
    "en": "You can also type instead of speaking.",
    "es": "Tambien puede escribir en lugar de hablar.",
    "fr": "Vous pouvez aussi ecrire au lieu de parler.",
    "zh": "您也可以打字，不用说话。",
    "pt": "Voce tambem pode escrever em vez de falar.",
    "hi": "आप बोलने के बजाय लिख भी सकते हैं।",
}


# --------------------------------------------------------------------------
# The system prompt every LLM call inherits
# --------------------------------------------------------------------------
def system_prompt(language: str, senior_name: str | None = None) -> str:
    name = senior_name.split()[0] if senior_name else "the person"
    rules = "\n".join(f"- {r}" for r in STYLE_RULES)
    return f"""You are the voice of a daily check-in service used by older adults.
You are speaking with {name}. Reply only in {LANGUAGE_NAMES.get(language, 'English')}.

How you speak:
{rules}

What you are not:
- You are not a doctor and you never diagnose. A nurse reviews anything urgent.
- You never decide how urgent something is. That decision is made before you
  are called, and it is given to you. Your job is to say it clearly.
- You never invent a number, a statistic or a fact about their history. If it
  is not in the context you were given, you do not say it.

If they ask you to repeat, slow down, start over, or reach a person, do that
first and do it without comment."""
