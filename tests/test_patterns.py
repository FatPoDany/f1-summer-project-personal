"""Cross-corner patterns: the sentence the per-corner findings cannot say.

Findings are ranked by time lost and cut to three, so a driver who brakes early
everywhere gets three cards that each say it about one corner. These tests are
about the claim that spans them, and mostly about what it must refuse to say:
the rule is only useful if a banner appearing means something.

The corner dicts here are hand-built rather than measured. A pattern is a claim
about how many corners agree, and the honest way to test "four of six" is to
write four of six down; deriving them from a lap would test corner detection
instead, which has its own tests.
"""

import pytest

from f1coach_core import load_sample_session
from f1coach_core.features import (
    MIN_PATTERN_AGREEING,
    MIN_PATTERN_CORNERS,
    MIN_PATTERN_SHARE,
    NOTABLE_BRAKE_POINT_M,
    NOTABLE_MIN_SPEED_KMH,
    PATTERN_METRICS,
    _corner_facts,
    corner_patterns,
)

BRAKE = "brake_point_m"
SPEED = "min_speed_kmh"


def corners(metric: str, *gaps: float | None, base: float = 500.0) -> list[dict]:
    """One corner per gap, carrying only the metric under test.

    ``None`` is a corner where the channel was never measured -- not a corner
    where the difference was zero. The two have to stay distinguishable: the
    first cannot count toward a share and the second must.
    """
    rows = []
    for i, gap in enumerate(gaps, start=1):
        row: dict = {"corner": f"T{i}", "apex_m": 100.0 * i, "span_m": [0.0, 50.0]}
        if gap is not None:
            row[metric] = base + gap
            row[f"ref_{metric}"] = base
        rows.append(row)
    return rows


def only(patterns: list[dict]) -> dict:
    assert len(patterns) == 1, [p["headline"] for p in patterns]
    return patterns[0]


# -- what makes a habit ------------------------------------------------------


def test_the_same_mistake_at_most_corners_is_reported_as_one_thing():
    """The claim the three findings between them cannot make."""
    pattern = only(corner_patterns(corners(BRAKE, -30, -25, -20, -15, 0, 2)))

    assert pattern["headline"] == "Braking earlier than the reference at most corners"
    assert pattern["corners"] == ["T1", "T2", "T3", "T4"]
    assert pattern["agreeing"] == 4
    assert pattern["measured"] == 6
    assert pattern["median"] == -22.5
    assert "4 of 6 corners" in pattern["detail"]
    assert "22 m earlier" in pattern["detail"]


def test_corners_nobody_wrote_a_finding_about_still_count():
    """The share is over every corner measured, not over the coached ones.

    This is the whole point of the aggregate. Counting only the corners that
    earned a card would let three cards vote for the pattern they are already
    three copies of, and the claim would be true of itself by construction.
    """
    thin = corner_patterns(corners(BRAKE, -30, -30, -30, 0, 0, 0, 0, 0, 0, 0))

    assert thin == []


def test_a_corner_inside_the_threshold_is_measured_but_does_not_agree():
    """Neither for nor against: the corner where the habit did not show."""
    pattern = only(corner_patterns(corners(BRAKE, -30, -30, -30, -9.9)))

    assert pattern["agreeing"] == 3
    assert pattern["measured"] == 4
    assert pattern["against"] == 0


def test_a_corner_the_channel_never_recorded_is_not_a_corner_that_agreed():
    """A missing measurement is not a measurement of zero.

    Counting it as measured would let a driver whose brake channel dropped out
    at half the corners fall under the share and lose a habit they have; leaving
    it out of both is the only reading the telemetry supports.
    """
    pattern = only(corner_patterns(corners(BRAKE, -30, -30, -30, -30, None, None)))

    assert pattern["measured"] == 4
    assert pattern["agreeing"] == 4


def test_scatter_is_not_a_pattern():
    """Half the corners one way and half the other is a description of noise."""
    assert corner_patterns(corners(BRAKE, -30, -30, -30, 30, 30, 30)) == []


def test_too_few_corners_to_have_a_habit_at():
    """Three corners agreeing out of three measured is still only three corners."""
    assert corner_patterns(corners(BRAKE, -30, -30, -30)) == []
    assert corner_patterns(corners(BRAKE, -30, -30, -30, -30))


def test_two_corners_are_a_pair_not_a_way_of_driving():
    assert corner_patterns(corners(BRAKE, -30, -30, 0, 0, 0, 0)) == []


# -- the direction rule ------------------------------------------------------


def test_the_helpful_direction_is_never_reported_as_a_habit():
    """Braking *later* everywhere is not a thing to work on.

    The banner sits above the findings, where a driver looks for what to fix.
    Reported symmetrically this rule said "going faster through the slowest part
    of most corners" on the two slowest laps a real participant drove -- praise
    in the place advice goes, on the laps that lost the most time.
    """
    assert corner_patterns(corners(BRAKE, 30, 30, 30, 30, 30, 30)) == []
    assert corner_patterns(corners(SPEED, 20, 20, 20, 20, 20, 20)) == []


def test_corners_that_ran_the_other_way_are_counted_against_the_claim():
    """Not a pattern of its own, but a reader is entitled to see it."""
    pattern = only(corner_patterns(corners(BRAKE, -30, -30, -30, -30, 30, 0)))

    assert pattern["agreeing"] == 4
    assert pattern["against"] == 1
    assert pattern["against_direction"] == "later"


def test_every_metric_names_a_direction_that_costs_time():
    """A metric with no worse side would make "at most corners" meaningless."""
    for metric in PATTERN_METRICS:
        assert metric.sign in (-1.0, 1.0)
        assert metric.word and metric.counter and metric.word != metric.counter
        assert metric.threshold > 0


# -- ranking and thresholds --------------------------------------------------


def test_habits_in_different_units_are_ranked_against_each_other():
    """Metres and km/h only compare through their own reporting thresholds."""
    rows = corners(BRAKE, -12, -12, -12, -12)
    for row, gap in zip(rows, (-30.0, -30.0, -30.0, -30.0), strict=True):
        row[SPEED] = 100.0 + gap
        row[f"ref_{SPEED}"] = 100.0

    patterns = corner_patterns(rows)

    assert [p["metric"] for p in patterns] == [SPEED, BRAKE]
    # 30 km/h is ten of its own thresholds; 12 m is barely over one of its.
    assert patterns[0]["strength"] > patterns[1]["strength"]


def test_the_thresholds_are_the_ones_the_debrief_reports_on():
    """One notion of notable, or a drive is remarkable in one place and not the next."""
    by_key = {metric.key: metric for metric in PATTERN_METRICS}

    assert by_key[BRAKE].threshold == NOTABLE_BRAKE_POINT_M
    assert by_key[SPEED].threshold == NOTABLE_MIN_SPEED_KMH


def test_the_gates_are_stated_as_numbers_somebody_can_argue_with():
    assert (MIN_PATTERN_CORNERS, MIN_PATTERN_AGREEING) == (4, 3)
    assert 0.5 < MIN_PATTERN_SHARE < 1.0


# -- against real laps -------------------------------------------------------


@pytest.fixture(scope="module")
def session():
    return load_sample_session()


def test_a_real_session_with_no_habit_is_reported_as_having_none(session):
    """0822 is quicker than their own best at some corners and slower at others.

    A rule that finds a pattern in that would find one anywhere. The sample is
    the only session in the suite, so this is the guard that the gates are not
    so loose that every lap gets a banner.
    """
    best = session.best_lap
    for lap in session.laps:
        if lap is best:
            continue
        assert corner_patterns(_corner_facts(lap, best)) == []


def test_junk_in_the_corner_list_is_skipped_rather_than_raised(session):
    """Corner rows arrive from an audit file, which is untrusted input."""
    rows = corners(BRAKE, -30, -30, -30, -30)
    polluted = [*rows, "not a corner", {"corner": ""}, {}, {"corner": "T9", BRAKE: None}]

    assert only(corner_patterns(polluted))["agreeing"] == 4
