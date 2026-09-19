"""Synthetic seed data.

Everything here is invented. No real patient data touches this repo.

The seed exists so the UI has a populated dashboard on first load, and so the
demo's baseline card ("dizziness is increasing") has fourteen days of history
to stand on. Backdated check-ins are evaluated through the real ladder, not
stubbed, so the timeline is internally consistent.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

from .extraction import extract
from .ladder import evaluate
from .schemas import (
    Caregiver,
    CheckIn,
    CheckInSource,
    EventType,
    Medication,
    Senior,
    TimelineEntry,
    Vitals,
)
from .store import Store, new_id

SEED_RANDOM = random.Random(20260919)


def _senior_rosa() -> Senior:
    """Primary demo patient: Spanish-speaking, polypharmacy, drifting baseline."""
    return Senior(
        id="sen_rosa",
        display_name="Rosa Mendez",
        date_of_birth=date(1947, 3, 14),
        age=79,
        preferred_language="es",
        voice_output_supported=True,
        conditions=["hypertension", "type 2 diabetes", "osteoarthritis"],
        allergies=["penicillin"],
        medications=[
            Medication(id="med_1", name="Lisinopril", ingredient="lisinopril",
                       dose="10 mg", schedule="每日 / daily"),
            Medication(id="med_2", name="Metformin", ingredient="metformin",
                       dose="500 mg", schedule="twice daily"),
            Medication(id="med_3", name="Furosemide", ingredient="furosemide",
                       dose="20 mg", schedule="daily"),
            Medication(id="med_4", name="Ambien", ingredient="zolpidem",
                       dose="5 mg", schedule="at night"),
        ],
        caregivers=[
            Caregiver(
                id="cg_priya",
                name="Priya Mendez",
                relationship="daughter",
                phone_e164="+16175550142",
                preferred_language="en",
                linq_thread_id="thread_rosa_family",
            )
        ],
        consent={"share_with:cg_priya": True},
    )


def _senior_chen() -> Senior:
    """Second patient so the clinician dashboard is not a list of one."""
    return Senior(
        id="sen_chen",
        display_name="Wei Chen",
        date_of_birth=date(1941, 11, 2),
        age=84,
        preferred_language="zh",
        # Deepgram hears Mandarin but has no Aura voice for it, so Wei reads
        # the reply. This is the text-fallback path, visible in the demo.
        voice_output_supported=False,
        conditions=["atrial fibrillation", "chronic kidney disease stage 3"],
        allergies=[],
        medications=[
            Medication(id="med_5", name="Warfarin", ingredient="warfarin",
                       dose="4 mg", schedule="daily"),
            Medication(id="med_6", name="Amlodipine", ingredient="amlodipine",
                       dose="5 mg", schedule="daily"),
        ],
        caregivers=[
            Caregiver(
                id="cg_ming",
                name="Ming Chen",
                relationship="son",
                phone_e164="+16175550187",
                preferred_language="en",
                linq_thread_id="thread_chen_family",
            )
        ],
        consent={"share_with:cg_ming": True},
    )


def _senior_walter() -> Senior:
    """English-speaking, steady baseline -- the control case in the demo."""
    return Senior(
        id="sen_walter",
        display_name="Walter Boyd",
        date_of_birth=date(1950, 6, 30),
        age=76,
        preferred_language="en",
        conditions=["COPD"],
        allergies=["sulfa"],
        medications=[
            Medication(id="med_7", name="Albuterol", ingredient="albuterol",
                       dose="2 puffs", schedule="as needed"),
        ],
        caregivers=[
            Caregiver(
                id="cg_dana",
                name="Dana Boyd",
                relationship="wife",
                phone_e164="+16175550119",
                linq_thread_id="thread_boyd_family",
            )
        ],
        consent={"share_with:cg_dana": True},
    )


def _senior_henriette() -> Senior:
    """French-speaking, lives alone, son checks in from another city."""
    return Senior(
        id="sen_henriette",
        display_name="Henriette Dubois",
        date_of_birth=date(1944, 8, 21),
        age=82,
        preferred_language="fr",
        voice_output_supported=True,  # aura-2-agathe-fr
        conditions=["osteoporosis", "hypothyroidism"],
        allergies=["codeine"],
        medications=[
            Medication(id="med_8", name="Levothyroxine", ingredient="levothyroxine",
                       dose="75 mcg", schedule="every morning"),
            Medication(id="med_9", name="Zopiclone", ingredient="zolpidem",
                       dose="3.75 mg", schedule="at night"),
        ],
        caregivers=[
            Caregiver(
                id="cg_luc",
                name="Luc Dubois",
                relationship="son",
                phone_e164="+16175550163",
                preferred_language="fr",
                linq_thread_id="thread_dubois_family",
            )
        ],
        consent={"share_with:cg_luc": True},
    )


# Rosa's fourteen days. Days are counted backwards from today; the script is
# written so dizziness and poor appetite climb in the second week, which is what
# the baseline evidence card picks up during the demo.
ROSA_SCRIPT: list[tuple[int, str, str]] = [
    (13, "es", "Todo bien hoy, solo un poco de dolor de espalda."),
    (12, "es", "Dormi bien. Sin problemas."),
    (11, "es", "Un poco de dolor de espalda otra vez, leve."),
    (10, "es", "Hoy me senti bien."),
    (9, "es", "Dolor de espalda leve por la manana."),
    (8, "es", "Todo normal."),
    (7, "es", "Un poco de mareo cuando me levante, muy leve."),
    (6, "es", "Me senti bien, comi normal."),
    (5, "es", "Mareo otra vez al levantarme de la cama."),
    (4, "es", "No tengo hambre hoy, y un poco de mareo."),
    (3, "es", "Mareo por la manana. No tengo hambre."),
    (2, "es", "Sin apetito, me cai casi al ir al bano pero no me cai."),
    (1, "es", "Mareo fuerte hoy y sin apetito."),
    (0, "es", "Mareo al caminar, no tengo hambre desde ayer."),
]

CHEN_SCRIPT: list[tuple[int, str, str]] = [
    (8, "zh", "今天还好，没有不舒服。"),
    (5, "zh", "有点头晕，休息后好了。"),
    (2, "zh", "背痛，不严重。"),
    (0, "zh", "今天还好。"),
]

HENRIETTE_SCRIPT: list[tuple[int, str, str]] = [
    (10, "fr", "Tout va bien, j'ai juste un peu mal au dos."),
    (6, "fr", "J'ai un peu de vertige le matin, ca passe."),
    (3, "fr", "Je n'ai pas faim et j'ai la tete qui tourne."),
    (1, "fr", "Je n'ai pas dormi, et toujours un peu de vertige."),
]

WALTER_SCRIPT: list[tuple[int, str, str]] = [
    (7, "en", "Bit of a cough today, nothing new for me."),
    (4, "en", "Slept fine, no trouble breathing."),
    (1, "en", "Trouble sleeping last night but otherwise steady."),
]


def _vitals_for(day_offset: int, senior: Senior) -> Vitals | None:
    """Occasional self-reported vitals, jittered but inside normal range."""
    if day_offset % 4:
        return None
    r = SEED_RANDOM
    return Vitals(
        systolic=r.randint(118, 138),
        diastolic=r.randint(68, 82),
        heart_rate=r.randint(62, 84),
    )


def _seed_history(store: Store, senior: Senior, script: list[tuple[int, str, str]]) -> None:
    now_utc = datetime.now(timezone.utc)
    for days_ago, lang, text in script:
        created = now_utc - timedelta(days=days_ago)
        if days_ago:
            created = created.replace(
                hour=9, minute=SEED_RANDOM.randint(0, 50), second=0, microsecond=0
            )
        else:
            # Today's entry is "a couple of hours ago", never a future timestamp.
            created = created - timedelta(hours=2)
        checkin = CheckIn(
            id=new_id("chk"),
            senior_id=senior.id,
            created_at=created,
            source=CheckInSource.VOICE if days_ago % 2 else CheckInSource.TEXT,
            language=lang,
            raw_text=text,
            transcript_confidence=round(SEED_RANDOM.uniform(0.82, 0.97), 2)
            if days_ago % 2
            else None,
            symptoms=extract(text, lang),
            vitals=_vitals_for(days_ago, senior),
            extraction_model="seed-lexicon",
        )
        store.put_checkin(checkin)

        previous = store.latest_evaluation(senior.id)
        evaluation = evaluate(
            checkin,
            senior,
            store.baseline(senior.id),
            previous_level=previous.level if previous else None,
        )
        # Keep the seeded timeline chronologically honest.
        evaluation.created_at = created
        store.put_evaluation(evaluation)

        store.add_timeline(
            senior.id,
            TimelineEntry(
                at=created,
                type=EventType.EVALUATION_COMPLETED,
                checkin_id=checkin.id,
                evaluation_id=evaluation.id,
                level=evaluation.level,
                summary=(
                    ", ".join(s.label for s in checkin.symptoms) or "no symptoms reported"
                ),
                detail={"source": checkin.source.value, "seeded": True},
            ),
        )


def seed(store: Store) -> Store:
    if store.seniors:
        return store
    for senior, script in (
        (_senior_rosa(), ROSA_SCRIPT),
        (_senior_chen(), CHEN_SCRIPT),
        (_senior_walter(), WALTER_SCRIPT),
        (_senior_henriette(), HENRIETTE_SCRIPT),
    ):
        store.put_senior(senior)
        _seed_history(store, senior, script)
    return store
