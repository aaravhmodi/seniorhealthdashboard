# How this system talks to an older adult

The rules are enforced in code (`backend/app/persona.py`, checked by
`persona.lint()` and `tests/test_persona.py`). This file is the reasoning behind
them, which is also the answer when a judge asks "why does it sound like that?"

## The one-line version

Speak the way you would to a competent adult who is across the room and a little
hard of hearing. Slow the delivery, never the respect.

## What the research says

**Elderspeak backfires.** The patronising register people slip into around older
adults — higher pitch, sing-song prosody, diminutives ("sweetie", "good girl"),
collective pronouns ("how are *we* feeling today"), and simplified grammar — is
perceived as disrespectful, and older adults withdraw in response. In dementia
care it measurably increases resistance to care. It also provides no
comprehension benefit, so it costs trust and buys nothing.
([ASHA](https://leader.pubs.asha.org/doi/10.1044/leader.FTR2.15032010.12),
[elderspeak in hospital dementia care](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10738631/),
[the two faces of elderspeak](https://pmc.ncbi.nlm.nih.gov/articles/PMC13372659/))

**Health literacy work points the same way:** clear, purposeful, individualised
communication and a patient-centred approach that demonstrates acceptance and
respect.
([OJIN](https://ojin.nursingworld.org/table-of-contents/volume-14-2009/number-3-september-2009/health-literacy-in-older-adults/),
[systematic review](https://pmc.ncbi.nlm.nih.gov/articles/PMC5119904/))

**Voice interfaces need retuning for older speakers.** Senior-friendly
configurations use a slower speech rate of around 135 wpm and a long pause
timeout of about 4 seconds; older adults pause mid-sentence more, so a default
~1s endpointing window talks over them. Explicit confirmation on anything
irreversible, and a non-voice path, because a meaningful share of older users
distrust voice input. Users specifically asked for slow-down and repeat
controls.
([CHI 2025 on interruptions and backchannels](https://dl.acm.org/doi/10.1145/3706598.3714228),
[errors in older adults' voice assistant use](https://arxiv.org/pdf/2403.02421),
[usability and emotional needs](https://www.sciencedirect.com/science/article/pii/S2405844023091405),
[voice assistants and learning preferences in health contexts](https://www.sciencedirect.com/science/article/pii/S2949882126000423))

**Presbycusis takes the high frequencies first**, which is where consonants
live. A lower-pitched voice is easier to follow than a bright one, and the
important word should not land at the very end of a trailing sentence.

## The rules we encode

| rule | where |
|---|---|
| No pet names, no diminutives, no baby talk | `ELDERSPEAK_PATTERNS` |
| No collective pronouns ("how are we feeling") | `ELDERSPEAK_PATTERNS` |
| No exclamation marks, no emoji, no cheerleading | `ELDERSPEAK_PATTERNS` |
| Everyday words, never clinical jargon | `JARGON` (with the plain-word swap) |
| One idea per sentence, ≤14 words, ≤3 sentences | `VoiceProfile`, checked by `lint` |
| Action first, then the reason | `explain.py` templates + `test_urgent_advice_leads_with_the_action` |
| Never a diagnosis, never "you have X" | `system_prompt`, `STYLE_RULES` |
| Numbers spoken plainly ("nine one one") | templates, per language |
| Teach-back instead of "do you understand?" | `TEACH_BACK` |
| Confirm before telling the family | `CONFIRM_BEFORE_SENDING` |
| Repeat / slow down / start over / reach a human, always | `detect_control` |
| Offer typing wherever voice is offered | `TEXT_FALLBACK_NOTE` |

## Delivery settings

```python
speech_rate_wpm   = 135     # vs ~160 default
tts_speed         = 0.85
endpointing_ms    = 4000    # silence before we assume they finished
utterance_end_ms  = 2000    # mid-turn pause tolerance
prefer_lower_pitch = True
```

## Honesty about voice coverage

Deepgram listens in far more languages than it speaks. Verified against
`GET https://api.deepgram.com/v1/models` on this account:

| | languages |
|---|---|
| nova-3 hears | en es fr zh pt hi (and ~40 more) |
| aura-2 speaks | de en es fr it ja nl — **and nothing else** |

So of the six languages we support, three can be spoken back and three cannot.
Chinese, Portuguese and Hindi patients get `voice_output_supported: false`, a
`None` from `deepgram_speak_params()`, a 409 from `POST /voice/speak`, and the
"you can also type" line. Wei (Mandarin) is seeded precisely so this path is
visible in the demo rather than discovered on stage.

This table was wrong once — French was marked unspeakable on an assumption, and
Mandarin was marked speakable. `pytest -m live --live` now synthesizes a line in
every configured voice and transcribes it back, so the claim is checked against
the API rather than against memory.

## Why the LLM cannot drift out of this

`llm.explain()` sends `system_prompt()` with every call, then rejects the result
unless it passes `lint()`, stays in the requested language, and still contains
the action word for the level. Any failure falls back to the template. The model
gets to phrase the message; it never gets to change it.
