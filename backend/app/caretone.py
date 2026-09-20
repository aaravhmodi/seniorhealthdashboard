"""The texting voice: how this system tells a family something they don't want to hear.

`persona.py` is the kiosk voice, speaking to the patient. This is the other
half: the voice in the family group chat, where the news arrives cold, on a
phone, in the middle of a workday, from a number the daughter has never seen.
Nobody is in the room to soften it. So the message itself has to do that work.

The tone we are after is the good charge nurse on the phone at 2am: direct
about what is happening, unhurried, never cheerful about it, and obviously a
person you can just answer. Not a hospital robot, not a friend, not an
apologizer.

## What that is built out of

**SPIKES, folded into one text.** The six-step protocol for breaking bad news
is written for a conversation in a room, but four of its steps survive
compression into a single SMS, and they are the four that matter:

* *Setting* -- say who you are and who this is about, every single time. An
  unexplained "Rosa needs to go to the ER" from an unknown number is a cruelty.
* *Knowledge, preceded by a warning shot* -- one short sentence signalling
  that something is coming ("Quick update on Rosa, and it needs a call today"),
  then the fact. The half-second of warning is most of the kindness.
* *Empathy* -- a NURSE move: name it, normalize it, or say we are staying with
  it. One clause, not a paragraph. Text has no room for a speech and a speech
  in text reads as insincere.
* *Strategy and summary* -- exactly one next step, and the door left open.

**No false reassurance.** "Don't worry" and "it's probably nothing" are the two
phrases that destroy trust fastest, because the family finds out later whether
they were true. We say what is being done instead. Reassurance is carried by
structure -- someone is watching, here is the step, you can reply -- not by
adjectives.

**Always answerable.** Every message ends with a way to respond that costs the
caregiver nothing: a tapback, or a word. The evaluation of VA caregiver SMS
programs found the thing caregivers valued was not information but the sense of
being accompanied, and a message you cannot answer is not company.

**Short and skimmable.** Emergency-alert practice puts the useful ceiling near
150 characters for the urgent line; we allow a little more for the empathy
clause and the link, and cap at 320. One idea per sentence.

**Register rises with the rung, and stops.** Urgency is carried by word choice
and by what we ask for, never by volume: no all-caps, no exclamation marks, no
emoji on levels 2 and up. A system that shouts at level 4 has nowhere left to
go, and a family that has been shouted at once stops reading.

**Still no PHI.** All of the above happens inside the same constraint: status
word and a link, no symptoms, no medicines, no diagnosis. See `notify.py`.

Sources are collected in docs/PERSONA.md alongside the spoken-voice evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_SMS_CHARS = 320
URGENT_LINE_CHARS = 150

# --------------------------------------------------------------------------
# The register ladder
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Register:
    """How the voice changes as the rung goes up. Four rungs, four registers."""

    warning_shot: str      # the half-second of warning before the fact
    status: str            # the fact, in plain words, PHI-free
    empathy: str           # one NURSE clause -- name, normalize, or support
    ask: str               # exactly one next step
    reply_hint: str        # how to answer without effort


REGISTERS: dict[int, Register] = {
    1: Register(
        warning_shot="",
        status="checked in today; no change was found and no action is needed",
        empathy="",
        ask="",
        reply_hint="Reply STATUS any time",
    ),
    2: Register(
        warning_shot="a small update",
        status="checked in, and the recommendation is to call the clinic today. This is not an emergency",
        empathy="",
        ask="Reply CALL to request a nurse call",
        reply_hint="Tap back so we know you saw this",
    ),
    3: Register(
        warning_shot="this one needs you now",
        status="checked in; the recommendation is the emergency department now",
        empathy="",
        ask="Go to the emergency department now; reply CALL to request a nurse call",
        reply_hint="Reply seen after you have read this",
    ),
    4: Register(
        warning_shot="this is urgent",
        status="needs emergency help now",
        empathy="",
        ask="Call nine one one now",
        reply_hint="Reply called after you call nine one one",
    ),
}

# --------------------------------------------------------------------------
# Phrases that undo the whole thing
# --------------------------------------------------------------------------
# False reassurance: the family learns later whether it was true, and if it
# was not, nothing we send afterwards is believed.
FALSE_REASSURANCE: tuple[str, ...] = (
    "don't worry", "dont worry", "no need to worry", "nothing to worry",
    "probably nothing", "i'm sure it's fine", "im sure its fine",
    "no reason for concern", "everything is fine", "it's fine",
)

# Hospital-desk language that reads as a form letter.
BUREAUCRATIC: tuple[tuple[str, str], ...] = (
    ("please be advised", "just so you know"),
    ("at this time", "right now"),
    ("in the event that", "if"),
    ("utilize", "use"),
    ("contact your provider", "call the clinic"),
    ("as per", "following"),
    ("we regret to inform you", "this is hard to send"),
    ("patient", "them"),
)

# Reactions and words we count as "I have seen this and I am on it".
ACK_WORDS: tuple[str, ...] = (
    "ok", "okay", "k", "got it", "gotit", "seen", "ack", "yes", "yep", "yeah",
    "on it", "onit", "thanks", "thank you", "will do", "heading", "on my way",
    "omw", "understood", "done", "listo", "fait", "feito", "completado", "entendido", "vale", "si", "compris", "oui",
)

# What the caregiver can text at us, and what it means. Matched loosely,
# because nobody reads the instructions and "where is dad??" is the real input.
INTENT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("status", r"\b(where is|where'?s|how is|how'?s|hows|status|update|check on|any news)\b"),
    ("call", r"\b(call me|call us|phone me|ring me|^call$|talk to (someone|a person|a nurse))\b"),
    ("stop", r"\b(stop|unsubscribe|opt out|no more texts)\b"),
    ("help", r"\b(help|what can i do|what do i do|commands)\b"),
)


@dataclass
class ToneResult:
    ok: bool
    problems: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------
def _join(*parts: str) -> str:
    """Sentences, one idea each, no double spaces, no empty fragments."""
    out: list[str] = []
    for part in parts:
        text = part.strip().rstrip(".")
        if text:
            out.append(text + ".")
    return " ".join(out)


def alert_body(
    first_name: str,
    level: int,
    link: str,
    *,
    caregiver_name: str | None = None,
    is_repeat: bool = False,
) -> str:
    """The message that lands in the family group chat after a check-in.

    Shape: who this is -> warning shot -> what happened -> one empathy clause
    -> one action -> how to answer. Levels 1 and 2 drop what they don't need;
    level 1 is deliberately boring, because a level-1 text that reads like an
    alert teaches the family to ignore the level-3 one.
    """
    reg = REGISTERS[int(level)]
    opening = "Care team here"
    if caregiver_name and int(level) >= 3:
        # Naming the person you are asking to act makes the ask land on
        # someone, rather than on "the family" in general.
        opening = f"{caregiver_name.split()[0]}, care team here"
    if reg.warning_shot:
        opening += f", and {reg.warning_shot}"
    if is_repeat:
        opening += " (following up on our last message)"

    body = _join(opening, f"{first_name} {reg.status}", reg.empathy, reg.ask)
    return _squeeze(f"{body} Details: {link}. {reg.reply_hint}.")


def escalation_body(first_name: str, missed_name: str, link: str) -> str:
    """Second caregiver, after the first did not answer.

    It says plainly that they are the backup and why they are hearing about it
    now. Being the second call is not an insult, but being the second call
    without being told so is.
    """
    body = _join(
        "Care team here about " + first_name,
        f"We have not received an acknowledgement from {missed_name.split()[0]}",
        "Please open the link and reply CALL if you need a nurse call",
    )
    return _squeeze(f"{body} {link}")


def followup_body(first_name: str, hours: int, link: str) -> str:
    """The post-discharge nudge, sent to the circle.

    Framed as a scheduled thing, not an alarm, so the family does not read a
    routine check as a new emergency.
    """
    when = "yesterday" if hours <= 24 else "a few days ago"
    body = _join(
        f"Scheduled {hours}-hour check on {first_name}, after the visit {when}",
        f"Please ask {first_name} to complete the follow-up in the app",
        "Reply HELP if you need support",
    )
    return _squeeze(f"{body} Follow along here: {link}")


def reminder_body(first_name: str, kind: str, link: str, language: str = "en") -> str:
    """A short, answerable reminder for the senior, with no clinical detail."""
    prompts = {
        "en": {
            "meds": "Good morning. This is a reminder to take your morning pills. Reply DONE after you take them, or HELP if you need support",
            "appointment": "Hi there. This is a reminder about your appointment. Reply DONE after you see this, or HELP if you need the details",
            "refill": "Hi there. Your refill reminder is ready. Reply DONE after you see this, or HELP if you need the details",
            "caregiver_update": "Hi there. Your care team has an update for you. Reply DONE after you see this, or HELP if you need support",
        },
        "es": {"meds": "Buenos dias. Un recordatorio amable para tomar sus pastillas. Responda LISTO cuando las tome"},
        "fr": {"meds": "Bonjour. Un petit rappel pour prendre vos medicaments. Repondez FAIT quand vous les avez pris"},
        "pt": {"meds": "Bom dia. Um lembrete gentil para tomar seus comprimidos. Responda FEITO quando tomar"},
        "zh": {"meds": "早上好。这是一个温和的服药提醒。服用后回复完成"},
        "hi": {"meds": "सुप्रभात। यह आपकी दवा लेने का एक सरल याद दिलाना है। लेने के बाद DONE लिखें"},
    }
    text = prompts.get(language, prompts["en"]).get(kind) or prompts["en"][kind]
    return _squeeze(f"{first_name}, {text}. {link}")


def teachback_miss_body(first_name: str, link: str) -> str:
    """The senior could not say their discharge plan back.

    This is the quietest alarm in the system and the one most worth sending.
    It is not framed as the senior failing -- it is framed as the instructions
    failing, which is almost always what actually happened.
    """
    body = _join(
        "Care team here",
        f"We asked {first_name} to say the plan back in their own words, and part "
        f"of it did not come back",
        "That usually means the instructions were unclear, not that they were not listening",
        "Please review the plan with them today",
    )
    return _squeeze(f"{body} The plan: {link}. Tap back when you have seen this.")


def status_reply_body(first_name: str, level: int, when: str, link: str) -> str:
    """The answer to "where is Dad?" -- the single most-used feature here.

    A caregiver asking that is anxious right now, so the answer leads with the
    state, not with a preamble.
    """
    reg = REGISTERS[int(level)]
    body = _join(
        f"Latest update: they {reg.status}",
        f"The last update was {when}",
        reg.ask if int(level) > 1 else "Reply HELP if you want to talk it through",
    )
    return _squeeze(f"{body} {link}")


def welcome_body(first_name: str, members: list[str], link: str) -> str:
    """The first message in a new circle, which is also the consent notice.

    It says what will and will not be sent here, because a family that does not
    know a channel's rules either over-trusts it or mutes it.
    """
    who = ", ".join(members)
    body = _join(
        f"Care team here for {first_name}. This group is {who}",
        "We will send status updates and changes here, never medical details, "
        "because texting is not a private channel",
        "Anything specific lives behind the link",
        "Reply STATUS any time to ask how they are",
    )
    return _squeeze(f"{body} {link}")


def _squeeze(body: str) -> str:
    """Collapse whitespace and keep the message inside one readable screen."""
    body = re.sub(r"\s+", " ", body).strip()
    body = re.sub(r"\.\s*\.", ".", body)
    return body


# --------------------------------------------------------------------------
# Inbound understanding
# --------------------------------------------------------------------------
def is_acknowledgement(text: str) -> bool:
    """Did the caregiver just tell us they have this?

    Deliberately generous. A false positive means we don't escalate to the
    second caregiver as fast; a false negative means we text a second person
    at midnight for no reason, which is how a family learns to mute us.
    """
    low = re.sub(r"[^\w\s]", " ", (text or "").lower()).strip()
    if not low:
        return False
    if low in ACK_WORDS:
        return True
    words = low.split()
    return len(words) <= 4 and any(w in ACK_WORDS for w in words)


def detect_intent(text: str) -> str | None:
    """What the caregiver wants, from a text they wrote in a hurry."""
    low = (text or "").lower().strip()
    if not low:
        return None
    if is_acknowledgement(low):
        return "ack"
    for intent, pattern in INTENT_PATTERNS:
        if re.search(pattern, low):
            return intent
    return None


# --------------------------------------------------------------------------
# Lint -- the tone contract, enforced in tests
# --------------------------------------------------------------------------
def lint(body: str, level: int = 2) -> ToneResult:
    """Check a family-facing message against everything above.

    Runs in the tests over every composer, and available at runtime to reject
    a generated body before it is sent.
    """
    problems: list[str] = []
    low = body.lower()

    for phrase in FALSE_REASSURANCE:
        if phrase in low:
            problems.append(f"false reassurance: '{phrase}'")

    for term, better in BUREAUCRATIC:
        if term in low:
            problems.append(f"form-letter phrase '{term}' -- say '{better}'")

    if len(body) > MAX_SMS_CHARS:
        problems.append(f"{len(body)} chars (limit {MAX_SMS_CHARS})")

    if int(level) >= 2:
        if "!" in body:
            problems.append("exclamation mark in an alert")
        if re.search(r"[\U0001F300-\U0001FAFF☀-➿]", body):
            problems.append("emoji in an alert")

    # All-caps shouting, ignoring the acronyms we use on purpose.
    for word in re.findall(r"\b[A-Z]{4,}\b", body):
        if word not in {"STATUS", "CALL", "HELP", "STOP"}:
            problems.append(f"shouting: '{word}'")

    if not re.search(r"https?://", body):
        problems.append("no link -- the detail has to live somewhere")

    return ToneResult(ok=not problems, problems=problems)
