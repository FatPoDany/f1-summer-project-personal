"""What counts as a notable difference, once the driver's own steadiness counts.

The four ``NOTABLE_*`` constants have always claimed in a comment to sit "above
ordinary lap-to-lap scatter", and until now nobody had measured anybody's. On
the first fifteen study laps the claim is false for the one that matters most:
minimum speed is gated at 3 km/h and the median corner wanders 8 km/h, so the
channel that could not help firing was also, because the threshold doubles as
the divisor that ranks channels, the one that won.

These tests are mostly about what the bar must refuse to do -- fall below the
fixed threshold, move between the moment advice is given and the moment somebody
is held to it, or be read off a corner grid other than the one the comparison
was measured on.
"""

from dataclasses import replace

import pytest

from f1coach_core import load_sample_session
from f1coach_core.adherence import (
    adherence,
    fastest,
    prescriptions,
    run_prescriptions,
)
from f1coach_core.coach import _opportunity_bar, opportunity_catalog
from f1coach_core.debrief import DebriefPoint, review_points
from f1coach_core.features import (
    NOTABLE_BRAKE_POINT_M,
    NOTABLE_MIN_SPEED_KMH,
    NOTABLE_THRESHOLDS,
    build_evidence_summary,
    corner_patterns,
    corner_scatter,
    detect_corners,
    notable_bar,
)
from f1coach_core.lap import Lap

BRAKE = "brake_point_m"
SPEED = "min_speed_kmh"


@pytest.fixture(scope="module")
def session():
    return load_sample_session()


def fact(corner: str = "T1", **gaps: float) -> dict:
    """One corner carrying only the channels under test, at a chosen gap.

    Hand-built for the same reason ``test_patterns`` builds its own: the claim
    is about a threshold, and deriving the numbers from a lap would test corner
    detection instead.
    """
    row: dict = {"corner": corner, "apex_m": 300.0, "span_m": [100.0, 500.0]}
    for metric, gap in gaps.items():
        row[metric] = 500.0 + gap
        row[f"ref_{metric}"] = 500.0
    row["time_lost_s"] = 0.4
    return row


def said(points: list) -> list[str]:
    return [point.metric for point in points if point.metric]


# -- what is measured --------------------------------------------------------


def test_the_spread_is_read_on_one_lap_s_corner_grid(session):
    """``detect_corners`` numbers apexes positionally, so T4 is not portable.

    Measuring each lap's spread on its own detection and matching by name is
    the bug this shares with ``adherence`` and ``reference``: one extra dip on
    one lap renames everything after it, and the spread of T4 is then quietly
    the spread of two different corners.
    """
    anchor = session.best_lap
    spread = corner_scatter(list(session.laps), anchor)

    assert {corner for corner, _ in spread} <= {
        zone["corner"] for zone in detect_corners(anchor)
    }
    assert {metric for _, metric in spread} <= set(NOTABLE_THRESHOLDS)


def test_one_lap_has_no_spread_to_be_read_against(session):
    """Empty, not zero: a lap driven once has no repeat to differ from."""
    assert corner_scatter([session.best_lap], session.best_lap) == {}
    assert corner_scatter([], session.best_lap) == {}


def test_a_channel_only_one_lap_recorded_is_absent_rather_than_steady(session):
    """A corner nobody braked at twice is not a corner braked identically twice.

    Zero would be the widest possible claim of consistency, made from a single
    observation, and it would drop the bar back to the fixed threshold while
    looking like it had been checked.
    """
    spread = corner_scatter(list(session.laps), session.best_lap)
    measured = {
        (corner, metric)
        for corner in {corner for corner, _ in spread}
        for metric in NOTABLE_THRESHOLDS
    }

    assert spread.keys() < measured, "the sample has channels only one lap recorded"
    assert all(value >= 0.0 for value in spread.values())


# -- the bar -----------------------------------------------------------------


def test_the_bar_rises_to_the_driver_but_never_falls_below_the_threshold():
    """The asymmetry that makes a three-lap sd safe to use at all.

    It carries roughly half its own size in error. Taking the larger means that
    error can only ever cost a real observation, never manufacture one -- and
    a driver who happened to repeat a corner tightly three times does not get
    told about every 0.4 km/h for the rest of the session.
    """
    wanders = {("T1", SPEED): 8.0}
    repeats = {("T1", SPEED): 0.4}

    assert notable_bar(SPEED, "T1", wanders) == 8.0
    assert notable_bar(SPEED, "T1", repeats) == NOTABLE_MIN_SPEED_KMH
    assert notable_bar(SPEED, "T1", None) == NOTABLE_MIN_SPEED_KMH
    assert notable_bar(SPEED, "T9", wanders) == NOTABLE_MIN_SPEED_KMH  # another corner


# -- the harm it fixes -------------------------------------------------------


def test_a_difference_inside_the_driver_s_own_wandering_is_not_a_finding():
    """5 km/h down at a corner they vary 8 km/h through is not a fact about them.

    It is the fact that they drove it twice. Reported as a finding it sends a
    participant to work on a corner the telemetry has nothing to say about, and
    in a study it enters the manipulation check as advice they can be judged on.
    """
    facts = [fact(**{SPEED: -5.0})]

    assert said(review_points(facts, compared=True)) == [SPEED]
    assert said(review_points(facts, compared=True, scatter={("T1", SPEED): 8.0})) == []


def test_the_channel_that_wins_a_corner_is_not_decided_by_the_smallest_constant():
    """The threshold is also the divisor that ranks channels against each other.

    One set too small does not merely admit noise, it wins: braking is gated at
    10 m and minimum speed at 3 km/h, so 9 km/h scores three while 20 m scores
    two, and the corner is reported as a speed problem. Against the driver's own
    spread through that corner the ranking is between two comparable numbers.
    """
    facts = [fact(**{BRAKE: -20.0, SPEED: -9.0})]

    assert said(review_points(facts, compared=True)) == [SPEED]
    assert said(
        review_points(facts, compared=True, scatter={("T1", SPEED): 9.0})
    ) == [BRAKE]


def test_a_habit_cannot_be_assembled_from_corners_nobody_repeats():
    """A pattern is a claim about most of the circuit, so the bar matters more.

    Below a driver's own wandering it does not admit one noisy corner: it
    manufactures the agreement the claim is made of.
    """
    facts = [fact(f"T{i}", **{BRAKE: -12.0}) for i in range(1, 6)]
    steady = {(f"T{i}", BRAKE): 2.0 for i in range(1, 6)}
    wanders = {(f"T{i}", BRAKE): 15.0 for i in range(1, 6)}

    assert corner_patterns(facts, steady), "12 m at five corners is a habit"
    assert corner_patterns(facts, wanders) == []


# -- one bar, from the ask to the account of it ------------------------------


def test_what_the_debrief_asks_for_is_never_less_than_what_counts_as_doing_it(
    session,
):
    """The pair has to be coherent or nobody could have satisfied it.

    While the ask was gated on the fixed threshold and compliance on the larger
    of the threshold and the participant's own spread, the software could tell
    somebody to find 4 km/h through a corner they wander 8 km/h in and then
    decline to count 4 km/h as having found it. Every corner where that happened
    entered the manipulation check as a participant who was told something and
    did not do it.
    """
    laps = list(session.laps)
    report = adherence(
        run_prescriptions(laps),
        before=laps,
        after=laps,
        anchor=fastest(laps),
    )

    assert report.shifts, "the sample session must ask for something"
    for shift in report.shifts:
        assert abs(shift.prescription.gap) + 1e-3 >= shift.required, shift.prescription


def test_the_bar_that_admitted_a_difference_is_the_one_recorded_on_it(session):
    """Carried on the point, not recomputed later from whatever laps are to hand.

    Adherence is measured months later, on a different machine, against advice
    that was given once. Recomputing the bar there would let it drift with the
    laps that happen to be loaded.
    """
    laps = list(session.laps)
    asked = run_prescriptions(laps)
    spread = corner_scatter(laps, fastest(laps))

    assert asked
    for item in asked:
        assert item.threshold == pytest.approx(
            notable_bar(item.metric, item.corner, spread), abs=1e-3
        )


def test_a_point_from_before_any_of_this_still_becomes_an_ask():
    """Points carry no bar until something measured one; the floor stands in.

    A debrief restored from an audit written before per-corner bars existed has
    to keep working, and the fixed threshold is exactly what it was judged on.
    """
    bare = DebriefPoint(
        corner="T1",
        apex_m=300.0,
        span_m=(100.0, 500.0),
        time_lost_s=0.4,
        difference="Braked 30 m earlier",
        detail="",
        category="braking",
        metric=BRAKE,
        gap=-30.0,
    )

    assert bare.threshold is None
    assert prescriptions([bare])[0].threshold == NOTABLE_BRAKE_POINT_M
    assert prescriptions([replace(bare, threshold=25.0)])[0].threshold == 25.0


# -- what the real laps say --------------------------------------------------


def test_the_fixed_thresholds_sit_below_real_lap_to_lap_scatter(session):
    """The calibration behind the change, kept where it can fail.

    Minimum speed is the one that matters: it is measurable at every corner, so
    a bar below the noise floor is not an occasional false positive but the
    default reading of every corner on the circuit.
    """
    spread = corner_scatter(list(session.laps), session.best_lap)
    speeds = [value for (_, metric), value in spread.items() if metric == SPEED]

    assert speeds
    wider = [value for value in speeds if value > NOTABLE_MIN_SPEED_KMH]
    assert len(wider) > len(speeds) / 2, (
        "most corners wander further than the 3 km/h that gates them"
    )


def test_the_spread_a_run_is_judged_by_is_the_spread_its_advice_was_set_by(session):
    """``adherence`` computes the same number a second way; they must agree.

    Two computations of one quantity is how the pair silently comes apart, so
    the equality is asserted rather than assumed.
    """
    laps = list(session.laps)
    anchor = fastest(laps)
    spread = corner_scatter(laps, anchor)
    report = adherence(
        run_prescriptions(laps), before=laps, after=laps, anchor=anchor
    )

    for shift in report.shifts:
        key = (shift.prescription.corner, shift.prescription.metric)
        if shift.before_sd is not None and key in spread:
            assert shift.before_sd == pytest.approx(spread[key], abs=1e-3)


def test_a_lap_too_short_to_share_a_grid_does_not_take_the_spread_with_it(
    session, tmp_path
):
    """A truncated capture is normal; losing every bar to it would not be."""
    from f1coach_core import load_telemetry_csv

    stub = tmp_path / "stub.csv"
    stub.write_text(
        "t,speed,throttle,brake,steer,gear\n0.0,10.0,1,0,0,3\n1.0,20.0,1,0,0,3\n",
        encoding="utf-8",
    )
    laps: list[Lap] = [*session.laps, load_telemetry_csv(stub)]

    assert corner_scatter(laps, session.best_lap) == corner_scatter(
        list(session.laps), session.best_lap
    )


# -- the cards, which used to read a different bar ----------------------------


@pytest.mark.parametrize(
    ("metric", "fact_key"),
    [
        ("brake_point", "brake_point_m"),
        ("min_speed", "min_speed_kmh"),
        ("throttle_reapply", "throttle_reapply_m"),
        ("coast_distance", "coast_distance_m"),
    ],
)
def test_a_card_asks_for_the_same_difference_the_debrief_asks_for(metric, fact_key):
    """One bar per channel, not one for the sentence and another for the card.

    The card used to ask 15 m of late throttle where the debrief asked 10, and
    10 m of coasting where the debrief asked 15: the same drive was notable on
    one screen and unremarkable on the next.
    """
    assert _opportunity_bar(metric, "T1", None) == NOTABLE_THRESHOLDS[fact_key]


def test_no_card_is_offered_for_a_difference_inside_the_driver_s_wandering(session):
    comparison = build_evidence_summary(session.laps[0], session.laps[4])
    spread = corner_scatter(list(session.laps), session.laps[0])
    assert spread, "the sample session should have a measurable spread"

    fixed = set(opportunity_catalog(comparison))
    against_the_driver = set(opportunity_catalog(comparison, spread))

    assert against_the_driver < fixed
    # The channel gated below this driver's own noise is the one that goes,
    # which is the whole finding of the measurement in `notable_bar`.
    assert {metric for _corner, metric in fixed - against_the_driver} == {"min_speed"}


def test_a_card_chosen_against_the_spread_still_stands_without_it(session):
    """Why the spread is a parameter and never a field of the evidence packet.

    The bar can only rise, so the catalog measured against a driver is a subset
    of the fixed one. Every reader of a stored report is a reader without the
    session, and this is what lets one still accept it -- while the packet it
    is matched on has not changed a byte.
    """
    comparison = build_evidence_summary(session.laps[0], session.laps[4])
    spread = corner_scatter(list(session.laps), session.laps[0])
    fixed = opportunity_catalog(comparison)
    for citation, item in opportunity_catalog(comparison, spread).items():
        assert fixed[citation] == item
