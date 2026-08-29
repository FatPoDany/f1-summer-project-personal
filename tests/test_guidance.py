"""The sentences a participant reads, and the hardware they can be acted on with.

The prose was the only part of the coaching pipeline nothing tested. Every
number is a schema constant checked against the telemetry, and then the words
around it were fifteen strings nobody had ever asserted anything about --
including that they asked for a pedal in a study whose own TORCS runtime binds
the throttle to the Up Arrow.

So these are mostly negative: what a sentence must never contain. A finding
that reaches the strict contract with a digit in its prose is rejected in front
of the participant, and one that asks a keyboard driver to squeeze a pedal is
rejected by nobody at all -- which is worse, because it is delivered.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from f1coach_core import load_sample_session
from f1coach_core.coach import EVIDENCE_METRICS, MockCoach, opportunity_catalog
from f1coach_core.features import build_evidence_summary
from f1coach_core.guidance import ANALOGUE, DIGITAL, SINGLE_LAP, guidance_for
from f1coach_core.input_device import (
    ANALOGUE as ANALOGUE_DEVICE,
)
from f1coach_core.input_device import (
    KEYBOARD,
    MIN_MOVING_SAMPLES,
    UNKNOWN,
    detect_input_device,
)
from f1coach_core.lap import Lap

# What the study's own drivers/human/preferences.xml binds. Named here so a
# change to the shipped runtime breaks a test rather than a participant's
# understanding of a sentence about a key they are not holding.
BOUND_KEYS = ("Up Arrow", "Down Arrow")

# The register the old templates were written in: the fact-checker's, not the
# driver's. Every one of these appeared in the strings this table replaced.
JARGON = (
    "cited",
    "reference marker",
    "displaced",
    "telemetry",
    "the zone",
    "input is interrupted",
    "needs review",
)

# Words that describe modulating a pedal. On a key there is no such thing.
PRESSURE = (
    "pressure",
    "squeeze",
    "squeezing",
    "smoothly",
    "smooth",
    "progressively",
    "pedal",
    "harder",
    "softer",
)


def sentences(table) -> list[tuple[str, str]]:
    """Every (metric, sentence) in a guidance table, for per-string assertions."""
    return [
        (metric, text)
        for metric, spoken in table.items()
        for text in (spoken.issue, spoken.cause, spoken.action)
    ]


def analogue_lap() -> Lap:
    """A lap whose steering never moves at the same rate twice.

    Not a claim about what a wheel looks like -- it is the complement of the
    thing actually being detected. The keyboard signature is a *constant* rate,
    because a held key is ramped by the game, and this is a lap that has no
    constant rate to find.
    """
    rng = np.random.default_rng(7)
    dist = np.arange(0.0, 2000.0, 5.0)
    speed = np.full_like(dist, 40.0)
    steer = np.cumsum(rng.normal(0.0, 0.05, size=len(dist)))
    steer = np.clip(steer / (np.abs(steer).max() or 1.0), -1.0, 1.0)
    t = np.concatenate([[0.0], np.cumsum(np.diff(dist) / speed[:-1])])
    return Lap(
        pd.DataFrame(
            {
                "t": t,
                "dist": dist,
                "speed": speed,
                "throttle": np.full_like(dist, 0.5),
                "brake": np.zeros_like(dist),
                "steer": steer,
                "gear": np.full_like(dist, 4),
            }
        ),
        Path("analogue.csv"),
        schema_version=1,
        dist_derived=False,
    )


# -- what every sentence must satisfy ----------------------------------------


@pytest.mark.parametrize("table", [ANALOGUE, DIGITAL, SINGLE_LAP])
def test_no_sentence_contains_a_digit(table):
    """``_require_measurement_free_prose`` rejects the whole finding for one.

    The measurements belong in the citation, where they are checked against the
    lap. A digit that reached prose would fail validation at the moment a
    participant was waiting for an answer.
    """
    for metric, text in sentences(table):
        assert not any(ch.isdigit() for ch in text), (metric, text)


@pytest.mark.parametrize("table", [ANALOGUE, DIGITAL, SINGLE_LAP])
def test_no_sentence_is_written_in_the_fact_checker_s_register(table):
    """"The cited brake-onset position is displaced toward the approach."

    True, checkable, and of no use to somebody who has driven a racing game a
    few times and has two laps left to do something about it.
    """
    for metric, text in sentences(table):
        lowered = text.lower()
        for word in JARGON:
            assert word not in lowered, (metric, word, text)


def test_the_keyboard_wording_never_asks_for_a_pedal():
    """The whole reason this register exists.

    A key has no pressure. Advice that asks for some is not hard to follow, it
    is impossible to follow, and the participant who could not follow it is
    recorded by ``adherence`` as one who did not.
    """
    for metric, text in sentences(DIGITAL):
        lowered = text.lower()
        for word in PRESSURE:
            assert word not in lowered, (metric, word, text)


def test_the_keyboard_wording_names_only_keys_the_study_actually_binds():
    """Naming the wrong key is worse than naming none."""
    for metric, text in sentences(DIGITAL):
        if "Arrow" in text:
            assert any(key in text for key in BOUND_KEYS), (metric, text)


def test_every_metric_a_model_may_cite_has_something_to_say_about_it():
    """A missing entry is silent: the model's own prose survives ungrounded.

    ``_ground_model_prose`` skips a citation it has no guidance for, and the
    sentence the model wrote is then published without ever being replaced --
    which is the one thing this whole layer exists to prevent.
    """
    missing = [metric for metric in EVIDENCE_METRICS if metric not in ANALOGUE]

    assert missing == [], missing


def test_the_two_providers_say_the_same_thing_about_the_same_metric():
    """One table, so a metric cannot mean two things by which backend answered.

    They used to be two hand-maintained copies, and they had already drifted:
    the same corner produced different advice from the mock and from Granite.
    """
    for metric in ANALOGUE:
        assert guidance_for(metric) is ANALOGUE[metric]


# -- picking a register ------------------------------------------------------


def test_a_driver_on_keys_is_told_about_keys():
    spoken = guidance_for("min_speed", device=KEYBOARD)

    assert "Down Arrow" in spoken.cause or "Down Arrow" in spoken.action
    assert spoken != guidance_for("min_speed")


def test_an_unmeasured_device_keeps_the_wording_that_was_already_shipping():
    """No verdict, no change. Guessing 'keyboard' would name a key at random."""
    for device in ("", UNKNOWN, ANALOGUE_DEVICE):
        assert guidance_for("min_speed", device=device) is ANALOGUE["min_speed"]


def test_a_metric_with_no_guidance_returns_nothing_rather_than_something_wrong():
    assert guidance_for("not_a_metric") is None


def test_the_findings_a_participant_reads_carry_the_register_end_to_end():
    """Through the real provider, not just the table."""
    session = load_sample_session()
    summary = build_evidence_summary(session.laps[0], session.best_lap)
    coach = MockCoach()
    coach.device = KEYBOARD

    findings = coach.generate(summary).to_dict()["findings"]

    assert findings
    spoken = " ".join(f["action"] for f in findings)
    assert "Arrow" in spoken
    assert "squeeze" not in spoken.lower()


# -- reading the device off the driving --------------------------------------


def test_every_real_lap_so_far_was_driven_on_a_keyboard():
    """The measurement the register switch depends on, on the only data there is.

    The share is not marginal: a held key is ramped by the game at one rate, so
    nineteen steering changes in twenty share it. Nothing a hand does looks
    like that, which is why the threshold can sit far below what was measured.
    """
    for lap in load_sample_session().laps:
        device = detect_input_device(lap)

        assert device.kind == KEYBOARD
        assert device.steady_rate_share > 0.9
        assert device.is_digital


def test_steering_that_never_repeats_a_rate_is_not_called_a_keyboard():
    device = detect_input_device(analogue_lap())

    assert device.kind == ANALOGUE_DEVICE
    assert device.steady_rate_share < 0.5


def test_a_lap_that_barely_steered_gets_no_verdict_rather_than_a_guess():
    """Silence, not a coin flip: the wording falls back to what it was."""
    lap = analogue_lap()
    still = Lap(
        lap.df.assign(steer=np.zeros(len(lap.df))),
        Path("still.csv"),
        schema_version=1,
        dist_derived=False,
    )

    device = detect_input_device(still)

    assert device.kind == UNKNOWN
    assert device.moving_samples < MIN_MOVING_SAMPLES
    assert guidance_for("min_speed", device=device.kind) is ANALOGUE["min_speed"]


def test_a_lap_with_no_steering_channel_is_not_a_lap_with_no_steering():
    lap = analogue_lap()
    without = Lap(
        lap.df.drop(columns=["steer"]),
        Path("nosteer.csv"),
        schema_version=1,
        dist_derived=False,
    )

    assert detect_input_device(without).kind == UNKNOWN


def test_the_verdict_travels_with_the_measurement_behind_it():
    """A researcher who doubts the call should not have to take it on trust."""
    device = detect_input_device(load_sample_session().best_lap)

    assert device.moving_samples > 1000
    assert 0.0 <= device.steady_rate_share <= 1.0


# -- what a real opportunity turns into --------------------------------------


def test_the_single_lap_advice_a_keyboard_driver_gets_is_about_their_keys():
    """Single-lap mode led fourteen of fifteen real reports (see doc §9)."""
    session = load_sample_session()
    summary = build_evidence_summary(session.best_lap)
    catalog = opportunity_catalog(summary)

    assert catalog, "the sample's best lap must raise a single-lap opportunity"
    for (_, metric), _ in catalog.items():
        spoken = guidance_for(metric, device=KEYBOARD, single_lap=True)
        assert spoken is not None, metric
        assert not any(word in spoken.action.lower() for word in PRESSURE), metric
