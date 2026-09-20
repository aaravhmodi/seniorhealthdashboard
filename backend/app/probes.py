"""The question we ask back, chosen by the same math that made the numbers.

Every check-in gets a follow-up. Not from a script keyed on one symptom, but
from this rule: **a probe exists only where a modifier in `risk.py` is sitting
unused because nobody has asked.** `sharpens` is that modifier's odds
multiplier, so the question we put to someone is the one whose answer could
move their number furthest.

That keeps the conversation honest in both directions. We never ask a question
whose answer we would not use, and when the most useful thing to ask would not
move the number at all -- "when did you last change a medicine" tells the
pharmacist where to look, and changes no percentage -- the probe says so with
`sharpens == 1.0` and the UI prints that instead of implying otherwise.

A probe stays *needed* when the answer was no. No head strike leaves the
narrative without one, exactly as never having asked does. So the caller passes
`asked_recently`, and without it an honest "no" would be met with the same
question every morning until the person stopped answering.

Deliberately not an LLM call. This renders while someone is standing at a
kiosk waiting, and a question that rewords itself every day is one people stop
reading. The phrasings are fixed, translated in place, and lint-clean against
`persona.py` -- which the tests enforce.

Typing note: `ctx` is `risk._Ctx`, annotated loosely on purpose. Importing the
real class would make `risk` and `probes` import each other, and this module
only ever reads the handful of attributes named below.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .schemas import FollowUp, RiskConcern


@dataclass(frozen=True)
class Probe:
    code: str
    # Patient-facing, one per supported language.
    question: dict[str, str]
    # What the answer changes, said plainly enough to show the person.
    why: str
    # True when the answer is still unknown, so the question is worth asking.
    needed: Callable[[Any], bool]
    # The odds multiplier the answer would put in play. 1.0 means the number
    # does not move and we are asking for the handoff, not for the math.
    sharpens: float = 1.0


# --------------------------------------------------------------------------
# Predicates
# --------------------------------------------------------------------------
def _has_vitals(ctx: Any) -> bool:
    v = ctx.vitals
    return any(x is not None for x in
               (v.spo2, v.systolic, v.heart_rate, v.temp_c, v.glucose_mgdl))


def _no_severity(label: str) -> Callable[[Any], bool]:
    """They reported this symptom but never put a number on it."""
    def check(ctx: Any) -> bool:
        symptom = ctx.symptom(label)
        return symptom is not None and symptom.severity is None
    return check


def _absent(label: str) -> Callable[[Any], bool]:
    return lambda ctx: label not in ctx.labels


def _always(ctx: Any) -> bool:
    return True


# --------------------------------------------------------------------------
# The catalog
# --------------------------------------------------------------------------
# Concerns whose modifiers actually read vitals. Asking someone with a
# suspected fracture for a blood pressure reading would be theatre, because
# `_fracture_modifiers` never looks at it.
VITALS_CONCERNS = frozenset({
    "cardiac", "infection_delirium", "breathing", "sepsis", "dehydration",
    "stroke",
})

VITALS_PROBE = Probe(
    code="vitals",
    question={
        "en": "Do you have a blood pressure cuff or thermometer nearby? Any reading helps.",
        "es": "Tiene cerca un tensiometro o un termometro? Cualquier lectura ayuda.",
        "fr": "Avez-vous un tensiometre ou un thermometre pres de vous? Toute mesure aide.",
        "pt": "Tem um medidor de pressao ou um termometro por perto? Qualquer leitura ajuda.",
        "zh": "您身边有血压计或体温计吗？任何读数都有帮助。",
        "hi": "क्या आपके पास ब्लड प्रेशर मशीन या थर्मामीटर है? कोई भी रीडिंग मदद करती है।",
    },
    why="One vital sign out of range is among the largest movers we have, and "
        "right now we are working without any.",
    needed=lambda ctx: not _has_vitals(ctx),
    sharpens=1.55,
)

# Keyed by concern code. Where two probes sharpen equally, the earlier wins.
PROBES: dict[str, tuple[Probe, ...]] = {
    "head_bleed": (
        Probe(
            code="head_strike",
            question={
                "en": "Did your head hit anything when you fell, even lightly?",
                "es": "Se golpeo la cabeza al caer, aunque fuera suave?",
                "fr": "Votre tete a-t-elle heurte quelque chose en tombant, meme legerement?",
                "pt": "Sua cabeca bateu em algo na queda, mesmo de leve?",
                "zh": "摔倒时您的头有没有碰到东西，哪怕很轻？",
                "hi": "गिरते समय क्या आपका सिर कहीं लगा, चाहे हल्का ही?",
            },
            why="Whether the head took the impact more than doubles this "
                "number, and nothing else on file tells us.",
            needed=lambda ctx: not ctx.narrative["head_strike"],
            sharpens=2.4,
        ),
        Probe(
            code="loss_of_consciousness",
            question={
                "en": "Did you black out or lose consciousness, even for a moment?",
                "es": "Perdio el conocimiento, aunque fuera un momento?",
                "fr": "Avez-vous perdu connaissance, meme un instant?",
                "pt": "Voce desmaiou ou perdeu a consciencia, mesmo por um instante?",
                "zh": "您有没有昏过去或失去意识，哪怕只有一瞬间？",
                "hi": "क्या आप बेहोश हुए, चाहे एक पल के लिए ही?",
            },
            why="Blacking out after a knock to the head doubles the odds of "
                "bleeding inside the skull.",
            needed=lambda ctx: not ctx.narrative["loss_of_consciousness"],
            sharpens=2.0,
        ),
    ),
    "fracture": (
        Probe(
            code="weight_bearing",
            question={
                "en": "From 0 to 10, how bad is the pain? Can you put weight on it?",
                "es": "Del 0 al 10, que tan fuerte es el dolor? Puede apoyar el peso?",
                "fr": "De 0 a 10, quelle est la douleur? Pouvez-vous poser le poids dessus?",
                "pt": "De 0 a 10, qual a intensidade da dor? Consegue apoiar o peso?",
                "zh": "从0到10，疼痛有多重？能不能受力站立？",
                "hi": "0 से 10 में दर्द कितना है? क्या आप उस पर वजन डाल सकते हैं?",
            },
            why="Severity as you rate it feeds the number directly, and a hip "
                "that cannot take weight changes the answer by itself.",
            needed=_no_severity("fall"),
            sharpens=1.5,
        ),
    ),
    "cardiac": (
        Probe(
            code="cardiac_companions",
            question={
                "en": "Are you also short of breath, sweating, or feeling sick?",
                "es": "Tambien le falta el aire, esta sudando o tiene nauseas?",
                "fr": "Etes-vous aussi essouffle, en sueur, ou nauseeux?",
                "pt": "Voce tambem esta sem folego, suando ou enjoado?",
                "zh": "您是否同时气短、出冷汗或恶心？",
                "hi": "क्या आपको सांस फूलना, पसीना या मतली भी है?",
            },
            why="In older adults these three, not chest pain, are how a heart "
                "attack usually announces itself.",
            needed=_absent("shortness of breath"),
            sharpens=1.6,
        ),
        Probe(
            code="chest_severity",
            question={
                "en": "From 0 to 10, how bad is the chest pain right now?",
                "es": "Del 0 al 10, que tan fuerte es el dolor de pecho ahora?",
                "fr": "De 0 a 10, quelle est la douleur dans la poitrine maintenant?",
                "pt": "De 0 a 10, qual a intensidade da dor no peito agora?",
                "zh": "从0到10，现在胸痛有多重？",
                "hi": "0 से 10 में अभी सीने का दर्द कितना है?",
            },
            why="Severity as you rate it, not as anyone interprets it.",
            needed=_no_severity("chest pain"),
            sharpens=1.5,
        ),
    ),
    "infection_delirium": (
        Probe(
            code="urinary",
            question={
                "en": "Any burning when you pass water, or going much more often?",
                "es": "Siente ardor al orinar, o va mucho mas seguido?",
                "fr": "Une brulure en urinant, ou des passages bien plus frequents?",
                "pt": "Sente ardencia ao urinar, ou vai muito mais vezes?",
                "zh": "小便时有灼痛感吗，或者次数明显变多？",
                "hi": "पेशाब में जलन है, या बहुत बार जाना पड़ रहा है?",
            },
            why="New confusion with urinary symptoms is the commonest way a "
                "urine infection shows itself at this age.",
            needed=_absent("urinary symptoms"),
            sharpens=1.7,
        ),
        Probe(
            code="fever_check",
            question={
                "en": "Do you feel hot or shivery? A thermometer reading would help.",
                "es": "Siente calor o escalofrios? Una lectura del termometro ayudaria.",
                "fr": "Avez-vous chaud ou des frissons? Une mesure de temperature aiderait.",
                "pt": "Sente calor ou calafrios? Uma medicao de temperatura ajudaria.",
                "zh": "您感觉发热或发冷吗？测一下体温会有帮助。",
                "hi": "क्या गर्मी या ठंड लग रही है? थर्मामीटर की रीडिंग मदद करेगी।",
            },
            why="Fever alongside new confusion widens this to any source of "
                "infection, which is a different search.",
            needed=lambda ctx: "fever" not in ctx.labels and ctx.vitals.temp_c is None,
            sharpens=1.5,
        ),
    ),
    "breathing": (
        Probe(
            code="leg_swelling",
            question={
                "en": "Are your ankles or legs more swollen than usual?",
                "es": "Tiene los tobillos o las piernas mas hinchados que de costumbre?",
                "fr": "Vos chevilles sont-elles plus enflees que d'habitude?",
                "pt": "Seus tornozelos ou pernas estao mais inchados que o normal?",
                "zh": "您的脚踝或腿比平时更肿吗？",
                "hi": "क्या आपके टखने या पैर सामान्य से ज्यादा सूजे हैं?",
            },
            why="Breathlessness with leg swelling points at the heart rather "
                "than the lungs, which is a different urgency.",
            needed=_absent("swelling legs"),
            sharpens=1.6,
        ),
    ),
    "dehydration": (
        Probe(
            code="on_standing",
            question={
                "en": "Does the dizziness come on when you stand up?",
                "es": "El mareo aparece cuando se pone de pie?",
                "fr": "Le vertige survient-il quand vous vous levez?",
                "pt": "A tontura aparece quando voce se levanta?",
                "zh": "头晕是在您站起来的时候出现的吗？",
                "hi": "क्या चक्कर खड़े होने पर आता है?",
            },
            why="Dizziness only on standing is blood pressure dropping, which "
                "is usually fixable the same day.",
            needed=_always,
        ),
    ),
    "medication_effect": (
        Probe(
            code="med_change",
            question={
                "en": "When did you last start or change any of your medicines?",
                "es": "Cuando fue la ultima vez que empezo o cambio algun medicamento?",
                "fr": "Quand avez-vous commence ou change un medicament pour la derniere fois?",
                "pt": "Quando voce comecou ou mudou algum medicamento pela ultima vez?",
                "zh": "您最近一次开始服用或更换药物是什么时候？",
                "hi": "आपने आखिरी बार कोई दवा कब शुरू की या बदली?",
            },
            why="This does not change the percentage. It tells the pharmacist "
                "where to start looking, which is what fixes it.",
            needed=_always,
        ),
    ),
    "spinal_cord": (
        Probe(
            code="back_severity",
            question={
                "en": "From 0 to 10, how bad is the back pain? Did it start suddenly?",
                "es": "Del 0 al 10, que tan fuerte es el dolor de espalda? Empezo de repente?",
                "fr": "De 0 a 10, quelle est la douleur au dos? Est-elle apparue soudainement?",
                "pt": "De 0 a 10, qual a intensidade da dor nas costas? Comecou de repente?",
                "zh": "从0到10，背痛有多重？是突然开始的吗？",
                "hi": "0 से 10 में कमर दर्द कितना है? क्या यह अचानक शुरू हुआ?",
            },
            why="Severity as you rate it, and a sudden start is its own signal.",
            needed=_no_severity("back pain"),
            sharpens=1.5,
        ),
    ),
}

# Fired when nothing above needs asking -- including the common case where the
# check-in raised no concern at all. Rotated, so a daily user is not asked
# about their sleep four mornings running.
GENERIC_PROBES: tuple[Probe, ...] = (
    Probe(
        code="generic_change",
        question={
            "en": "Has anything changed since yesterday, in your sleep, appetite or energy?",
            "es": "Ha cambiado algo desde ayer, en su sueno, apetito o energia?",
            "fr": "Quelque chose a-t-il change depuis hier, dans votre sommeil ou appetit?",
            "pt": "Algo mudou desde ontem, no seu sono, apetite ou energia?",
            "zh": "和昨天相比，您的睡眠、食欲或精力有变化吗？",
            "hi": "कल से आपकी नींद, भूख या ऊर्जा में कुछ बदला है?",
        },
        why="Change against your own normal is the signal no national dataset "
            "can supply, and it is what tomorrow gets compared against.",
        needed=_always,
    ),
    Probe(
        code="generic_function",
        question={
            "en": "Are you managing your usual routine today, washing and getting about?",
            "es": "Puede con su rutina de siempre hoy, lavarse y moverse?",
            "fr": "Gerez-vous votre routine habituelle aujourd'hui, toilette et deplacements?",
            "pt": "Consegue fazer sua rotina de sempre hoje, banho e andar pela casa?",
            "zh": "今天您能照常洗漱、穿衣和走动吗？",
            "hi": "क्या आज आप रोज के काम कर पा रहे हैं, नहाना और चलना-फिरना?",
        },
        why="What someone can still do for themselves moves before almost any "
            "symptom does, and it is the first thing a nurse asks.",
        needed=_always,
    ),
    Probe(
        code="generic_meds",
        question={
            "en": "Have you taken all your medicines today as usual?",
            "es": "Ha tomado hoy todos sus medicamentos como siempre?",
            "fr": "Avez-vous pris tous vos medicaments aujourd'hui comme d'habitude?",
            "pt": "Voce tomou todos os seus medicamentos hoje como sempre?",
            "zh": "今天您照常吃了所有的药吗？",
            "hi": "क्या आपने आज अपनी सारी दवाएं रोज की तरह ली हैं?",
        },
        why="A missed dose explains a surprising share of bad days, and it is "
            "the cheapest thing on this list to put right.",
        needed=_always,
    ),
    Probe(
        code="generic_else",
        question={
            "en": "Is there anything else bothering you that you have not mentioned?",
            "es": "Hay algo mas que le moleste y que no haya mencionado?",
            "fr": "Y a-t-il autre chose qui vous gene et dont vous n'avez pas parle?",
            "pt": "Ha mais alguma coisa incomodando que voce nao mencionou?",
            "zh": "还有别的不舒服，您还没有说到的吗？",
            "hi": "कुछ और परेशानी है जो आपने नहीं बताई?",
        },
        why="People report what they think we want to hear about, and the one "
            "they leave out is often the one that matters.",
        needed=_always,
    ),
)

# --------------------------------------------------------------------------
# Gates: questions whose answer would open a concern that has not fired
# --------------------------------------------------------------------------
# The probes above sharpen a concern that already fired. These are the other
# half, and the more important one: a concern that cannot fire *because* the
# question has not been asked.
#
# A plain fall is the fracture concern. It becomes the bleed concern on a head
# strike -- so a fall where nobody mentioned their head sits at "fracture" not
# because the head is fine, but because nobody asked. Ranking that question
# only against fracture's own probes buries it under a pain score, which is
# how a bleed gets missed by a system that was working exactly as written.
#
# So a gate is ranked as what it could open, and it outranks any probe that
# merely sharpens something already on screen.
@dataclass(frozen=True)
class Gate:
    probe: Probe
    opens: str                                  # concern code a yes would open
    applies: Callable[[Any, frozenset[str]], bool]


GATES: tuple[Gate, ...] = (
    Gate(
        probe=PROBES["head_bleed"][0],          # head_strike
        opens="head_bleed",
        applies=lambda ctx, fired: (
            "fall" in ctx.labels and not ctx.narrative["head_strike"]
        ),
    ),
    Gate(
        probe=PROBES["head_bleed"][1],          # loss_of_consciousness
        opens="head_bleed",
        applies=lambda ctx, fired: (
            "fall" in ctx.labels and not ctx.narrative["loss_of_consciousness"]
        ),
    ),
    Gate(
        probe=Probe(
            code="back_red_flags",
            question={
                "en": "Any leg weakness or numbness, or trouble controlling your bladder?",
                "es": "Tiene debilidad o adormecimiento en las piernas, o problemas para controlar la vejiga?",
                "fr": "Une faiblesse ou un engourdissement des jambes, ou du mal a controler la vessie?",
                "pt": "Tem fraqueza ou dormencia nas pernas, ou dificuldade para controlar a bexiga?",
                "zh": "您的腿是否无力或麻木，或者无法控制小便？",
                "hi": "क्या पैरों में कमजोरी या सुन्नपन है, या पेशाब पर नियंत्रण में दिक्कत?",
            },
            why="These are the only back-pain signs that cannot wait. A yes "
                "moves this from an ache to a same-hour problem.",
            needed=_always,
            sharpens=2.2,
        ),
        opens="spinal_cord",
        applies=lambda ctx, fired: "back pain" in ctx.labels,
    ),
)


ALL_PROBES: dict[str, Probe] = {
    probe.code: probe
    for group in (*PROBES.values(), GENERIC_PROBES, (VITALS_PROBE,),
                  tuple(gate.probe for gate in GATES))
    for probe in group
}


def phrase(probe: Probe, language: str) -> str:
    return probe.question.get(language) or probe.question["en"]


def _open_gates(
    ctx: Any, fired: frozenset[str], asked_recently: frozenset[str]
) -> list[Gate]:
    return [
        gate for gate in GATES
        if gate.opens not in fired
        and gate.probe.code not in asked_recently
        and gate.probe.needed(ctx)
        and gate.applies(ctx, fired)
    ]


def choose(
    ctx: Any,
    concerns: Iterable[RiskConcern],
    language: str = "en",
    asked_recently: frozenset[str] = frozenset(),
    concern_labels: dict[str, str] | None = None,
) -> FollowUp:
    """The one question worth asking next. Never None.

    Order of preference:

    1. A **gate** -- a question whose answer could open a concern we are not
       currently showing. Unasked beats unsharpened, always.
    2. The highest-sharpening **probe** on the most urgent concern that fired,
       walking concerns in the order they are ranked.
    3. A **generic** probe, rotated, so a quiet day still ends in a question.
    """
    concerns = list(concerns)
    fired = frozenset(c.code for c in concerns)
    # Passed in rather than imported: `risk` owns the catalog, and importing it
    # here would make the two modules import each other.
    concern_labels = concern_labels or {}

    gates = _open_gates(ctx, fired, asked_recently)
    if gates:
        gate = max(gates, key=lambda g: g.probe.sharpens)
        return FollowUp(
            code=gate.probe.code,
            question=phrase(gate.probe, language),
            why=gate.probe.why,
            concern_code=gate.opens,
            concern_label=concern_labels.get(gate.opens),
            sharpens=gate.probe.sharpens,
            opens=True,
        )

    for concern in concerns:
        candidates = [
            probe for probe in PROBES.get(concern.code, ())
            if probe.code not in asked_recently and probe.needed(ctx)
        ]
        if (concern.code in VITALS_CONCERNS
                and VITALS_PROBE.code not in asked_recently
                and VITALS_PROBE.needed(ctx)):
            candidates.append(VITALS_PROBE)
        if not candidates:
            continue
        probe = max(candidates, key=lambda p: p.sharpens)
        return FollowUp(
            code=probe.code,
            question=phrase(probe, language),
            why=probe.why,
            concern_code=concern.code,
            concern_label=concern.label,
            sharpens=probe.sharpens,
        )

    # Nothing specific left. Rotate the general ones on the check-in count.
    pool = [p for p in GENERIC_PROBES if p.code not in asked_recently]
    pool = pool or list(GENERIC_PROBES)
    probe = pool[ctx.baseline.checkin_count % len(pool)]
    return FollowUp(
        code=probe.code,
        question=phrase(probe, language),
        why=probe.why,
        generic=True,
    )
