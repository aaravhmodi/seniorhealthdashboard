"""The risk explanation layer: percentages, drivers, and the action each earns.

These tests pin the two properties that make the panel safe to show a patient:
a concern only appears when something in the check-in actually raised it, and
the number can move the action up but never down.
"""
from __future__ import annotations

import pytest

from app import risk
from app.ladder import evaluate
from app.schemas import (
    ActionLevel,
    BaselineSummary,
    CheckIn,
    CheckInSource,
    Medication,
    Senior,
    Symptom,
    Vitals,
)
from app.store import new_id, now


def _senior(age: int = 79, meds: list[Medication] | None = None) -> Senior:
    return Senior(
        id="sen_test",
        display_name="Test Senior",
        date_of_birth="1946-01-01",
        age=age,
        medications=meds or [],
    )


def _checkin(text: str, symptoms: list[str], vitals: Vitals | None = None) -> CheckIn:
    return CheckIn(
        id=new_id("chk"),
        senior_id="sen_test",
        created_at=now(),
        source=CheckInSource.TEXT,
        language="en",
        raw_text=text,
        symptoms=[Symptom(label=label) for label in symptoms],
        vitals=vitals,
    )


def _assess(checkin: CheckIn, senior: Senior, level: ActionLevel = ActionLevel.LOG):
    from app.rules import evaluate_rules

    flags = evaluate_rules(checkin, senior)
    return risk.assess(checkin, senior, BaselineSummary(), flags, level)


# -- bands -----------------------------------------------------------------
@pytest.mark.parametrize(
    "percent,expected",
    [(0, "monitor"), (14.9, "monitor"), (15, "today"), (39.9, "today"),
     (40, "emergency"), (69.9, "emergency"), (70, "now"), (100, "now")],
)
def test_bands_cover_the_whole_range_without_a_gap(percent, expected):
    assert risk.band_for(percent).band == expected


def test_band_thresholds_are_contiguous():
    for lower, upper in zip(risk.BANDS, risk.BANDS[1:]):
        assert lower.upper_percent == upper.lower_percent


# -- concerns only appear when something raised them -----------------------
def test_a_quiet_checkin_raises_no_concerns():
    assessment = _assess(_checkin("I feel fine today.", []), _senior())
    assert assessment.concerns == []
    assert assessment.overall_percent is None
    assert assessment.follow_up.generic is True
    assert assessment.follow_up.question


def test_a_concerning_checkin_always_gets_a_specific_follow_up():
    assessment = _assess(_checkin("I fell and hit my head.", ["fall"]), _senior())
    assert assessment.follow_up.generic is False
    assert assessment.follow_up.concern_code in {c.code for c in assessment.concerns}
    assert assessment.follow_up.question


def test_every_concern_names_what_triggered_it():
    checkin = _checkin("I fell and hit my head on the sink.", ["fall"])
    assessment = _assess(checkin, _senior())
    assert assessment.concerns
    for concern in assessment.concerns:
        assert concern.matched_on, f"{concern.code} fired with nothing to point at"


def test_a_plain_fall_is_the_fracture_concern_not_the_bleed_concern():
    codes = {c.code for c in _assess(_checkin("I slipped and fell.", ["fall"]), _senior()).concerns}
    assert "fracture" in codes
    assert "head_bleed" not in codes


def test_a_head_strike_in_patient_words_raises_the_bleed_concern():
    """The NEISS narrative lists say "struck head"; patients say "hit my head"."""
    assessment = _assess(_checkin("I fell and hit my head.", ["fall"]), _senior())
    assert "head_bleed" in {c.code for c in assessment.concerns}


def test_an_anticoagulant_raises_the_bleed_concern_from_a_plain_fall():
    senior = _senior(meds=[Medication(id="m1", name="Warfarin", ingredient="warfarin")])
    assessment = _assess(_checkin("I slipped and fell.", ["fall"]), senior)
    bleed = next(c for c in assessment.concerns if c.code == "head_bleed")
    assert any("warfarin" in m.lower() for m in bleed.matched_on)


# -- the arithmetic --------------------------------------------------------
def test_probabilities_stay_inside_zero_and_one_hundred():
    senior = _senior(age=92, meds=[Medication(id="m1", name="Warfarin", ingredient="warfarin")])
    checkin = _checkin(
        "I fell down the stairs, hit my head and blacked out.",
        ["fall", "confusion"],
        Vitals(systolic=88, heart_rate=126, spo2=88, temp_c=39.2),
    )
    for concern in _assess(checkin, senior).concerns:
        assert 0 < concern.probability_percent < 100


def test_a_driver_delta_is_the_difference_it_actually_makes():
    """delta_points must be `with it` minus `without it`, not a share of a score."""
    senior = _senior(meds=[Medication(id="m1", name="Warfarin", ingredient="warfarin")])
    bleed = next(
        c for c in _assess(_checkin("I fell and hit my head.", ["fall"]), senior).concerns
        if c.code == "head_bleed"
    )
    anticoagulant = next(d for d in bleed.drivers if "Warfarin" in d.label)
    without = bleed.probability_percent - anticoagulant.delta_points
    assert 0 < without < bleed.probability_percent
    # A driver that raises the odds must raise the probability.
    assert anticoagulant.multiplier > 1 and anticoagulant.delta_points > 0


def test_drivers_are_ordered_by_how_much_they_moved_the_number():
    checkin = _checkin("I fell and hit my head.", ["fall"])
    for concern in _assess(checkin, _senior(age=88)).concerns:
        deltas = [d.delta_points for d in concern.drivers]
        assert deltas == sorted(deltas, reverse=True)


def test_every_driver_says_whether_it_was_fitted_or_chosen():
    checkin = _checkin("I fell and hit my head.", ["fall"])
    for concern in _assess(checkin, _senior(age=88)).concerns:
        for driver in concern.drivers:
            assert driver.source, f"{driver.label} has no source"
            assert isinstance(driver.fitted, bool)


# -- the safety property ---------------------------------------------------
def test_the_percentage_never_lowers_the_rung():
    """A low number on a rule-flagged check-in must not walk the level down."""
    senior = _senior()
    checkin = _checkin("I fell and hit my head.", ["fall"])
    evaluation = evaluate(checkin, senior, BaselineSummary())
    assert int(evaluation.level) >= int(ActionLevel.GO_TO_ER)
    for concern in evaluation.risk.concerns:
        assert int(concern.action_level) <= int(evaluation.level)


def test_a_probability_never_calls_an_ambulance_on_its_own():
    """Only a deterministic red flag reaches 911. A number stops at the ED."""
    senior = _senior(age=95, meds=[Medication(id="m1", name="Warfarin", ingredient="warfarin")])
    checkin = _checkin("I fell down the stairs and hit my head hard.", ["fall"])
    assessment = _assess(checkin, senior, ActionLevel.GO_TO_ER)
    assert any(c.probability_percent >= 70 for c in assessment.concerns)
    assert all(int(c.action_level) <= int(ActionLevel.GO_TO_ER) for c in assessment.concerns)


def test_a_rule_that_forces_911_lets_the_concern_say_911():
    senior = _senior()
    checkin = _checkin("My face is drooping and my speech is slurred.",
                       ["weakness one side"])
    evaluation = evaluate(checkin, senior, BaselineSummary())
    assert evaluation.level == ActionLevel.CALL_911
    stroke = next(c for c in evaluation.risk.concerns if c.code == "stroke")
    assert stroke.action_level == ActionLevel.CALL_911


def test_every_concern_carries_an_action_matching_its_level():
    checkin = _checkin("I fell and hit my head. I feel dizzy.", ["fall", "dizziness"])
    for concern in _assess(checkin, _senior()).concerns:
        expected = next(b.action for b in risk.BANDS if b.action_level == concern.action_level)
        assert concern.action == expected


# -- provenance ------------------------------------------------------------
def test_the_assessment_always_explains_where_its_numbers_come_from():
    assessment = _assess(_checkin("I fell and hit my head.", ["fall"]), _senior())
    assert assessment.model_basis
    assert assessment.ladder_note
    assert assessment.bands == risk.BANDS
    for concern in assessment.concerns:
        assert concern.base_rate_detail
        assert 0 < concern.base_rate_percent < 100


def test_the_evaluation_carries_the_assessment_through_the_contract():
    evaluation = evaluate(
        _checkin("I fell and hit my head.", ["fall"]), _senior(), BaselineSummary()
    )
    payload = evaluation.model_dump(mode="json")
    assert payload["risk"]["concerns"], "the UI has nothing to render"
    assert payload["risk"]["bands"]
    top = payload["risk"]["concerns"][0]
    assert {"label", "probability_percent", "action", "drivers"} <= set(top)


# -- dataset provenance ----------------------------------------------------
def test_the_panel_names_every_source_behind_the_numbers():
    assessment = _assess(_checkin("I fell and hit my head.", ["fall"]), _senior())
    names = {d.name for d in assessment.datasets}
    assert {"NEISS", "NHAMCS", "FAERS"} <= names
    for dataset in assessment.datasets:
        assert dataset.role and dataset.detail


def test_only_neiss_is_ever_marked_as_trained_on():
    """NEISS is the only source with an outcome per case, so it is the only one
    we fit a model on. A panel that implied otherwise would be lying about
    what the percentages are."""
    assessment = _assess(_checkin("I fell and hit my head.", ["fall"]), _senior())
    trained = [d.name for d in assessment.datasets if d.trained]
    assert trained in ([], ["NEISS"]), f"claimed a model on {trained}"


def test_a_source_that_is_not_loaded_says_so():
    """The panel is built from what the deployment actually has. If NHAMCS is
    missing the concerns fall back to placeholder base rates, and the card has
    to admit it rather than quietly reading like measured data."""
    assessment = _assess(_checkin("I fell and hit my head.", ["fall"]), _senior())
    for dataset in assessment.datasets:
        if not dataset.loaded:
            assert "not loaded" in dataset.detail.lower()
