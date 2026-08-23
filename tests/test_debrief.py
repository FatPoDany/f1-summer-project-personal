"""The participant-facing debrief: where time went, and what differed there.

Correctness here is mostly about restraint. The debrief must rank real losses,
must not report a corner the driver was quicker through, and must say nothing
about a cause when no measurement moved far enough to be worth reporting.
"""

from pathlib import Path

import numpy as np
import pandas as pd

from f1coach_core import DebriefPoint, Lap, debrief_summary, lap_debrief, load_sample_session
from f1coach_core import debrief as debrief_module


def _lap(name: str, *, speed_scale: float = 1.0, brake_shift_m: float = 0.0) -> Lap:
    """A lap with one clear corner at ~300 m, optionally slower or braked late."""
    dist = np.arange(0.0, 900.0, 5.0)
    # A speed dip centred on the corner, scaled to make a slower lap.
    dip = 30.0 * np.exp(-(((dist - 300.0) / 90.0) ** 2))
    speed = (60.0 - dip) * speed_scale
    brake = np.where((dist > 120.0 + brake_shift_m) & (dist < 290.0), 0.8, 0.0)
    throttle = np.where(dist > 310.0, 0.9, 0.0)
    t = np.concatenate([[0.0], np.cumsum(np.diff(dist) / speed[:-1])])
    return Lap(
        pd.DataFrame(
            {
                "t": t,
                "dist": dist,
                "speed": speed,
                "throttle": throttle,
                "brake": brake,
                "steer": np.zeros_like(dist),
                "gear": np.full_like(dist, 4),
            }
        ),
        Path(f"{name}.csv"),
        schema_version=1,
        dist_derived=False,
    )


def test_reports_the_corners_that_cost_time_worst_first():
    session = load_sample_session()
    best = session.best_lap
    slowest = max(session.laps, key=lambda lap: lap.lap_time)

    points = lap_debrief(slowest, best)

    assert points, "a lap 5 s off the best must have somewhere to look"
    assert all(isinstance(point, DebriefPoint) for point in points)
    losses = [point.time_lost_s for point in points]
    assert losses == sorted(losses, reverse=True)
    assert all(loss > 0 for loss in losses)
    assert len(points) <= 3


def test_a_lap_is_never_debriefed_against_itself_into_findings():
    """Compared with itself a lap lost nothing, so there is nothing to report."""
    lap = load_sample_session().best_lap
    assert lap_debrief(lap, lap) == []


def test_only_losses_are_reported():
    quick, slow = _lap("quick"), _lap("slow", speed_scale=0.85)

    # Slow lap against quick: the corner cost time and is reported.
    assert lap_debrief(slow, quick)
    # The reverse gained time everywhere, so there is nothing to look at.
    assert lap_debrief(quick, slow) == []


def test_a_measured_difference_below_its_threshold_is_left_unexplained():
    """Time was lost but nothing moved far enough to report — say so, invent nothing."""
    reference = _lap("reference")
    barely = _lap("barely", speed_scale=0.999)

    points = lap_debrief(barely, reference, min_time_lost_s=0.0)

    assert points, "the lap did lose time"
    assert all(point.difference == "" and point.detail == "" for point in points)


def test_the_largest_gap_relative_to_its_own_threshold_is_the_one_shown():
    """Metres and km/h are not comparable directly, so ranking is by threshold."""
    fact = {
        # 2x its 10 m threshold
        "brake_point_m": 100.0,
        "ref_brake_point_m": 80.0,
        # 5x its 3 km/h threshold
        "min_speed_kmh": 85.0,
        "ref_min_speed_kmh": 100.0,
        "throttle_reapply_m": None,
        "ref_throttle_reapply_m": None,
        "coast_distance_m": None,
        "ref_coast_distance_m": None,
    }

    ranked = sorted(debrief_module._candidates(fact), key=lambda item: item[0], reverse=True)

    assert "km/h slower" in ranked[0][1]
    assert "Braked" in ranked[1][1]


def test_summary_states_the_quickest_lap_plainly():
    session = load_sample_session()
    best = session.best_lap
    assert debrief_summary(best, best, []) == "This was your quickest lap of the session."


def test_summary_names_the_corners_it_accounted_for():
    session = load_sample_session()
    best = session.best_lap
    slowest = max(session.laps, key=lambda lap: lap.lap_time)

    points = lap_debrief(slowest, best)
    summary = debrief_summary(slowest, best, points)

    assert "off your best lap" in summary
    for point in points:
        assert point.corner in summary


def test_a_summary_never_claims_more_loss_than_the_lap_had():
    """"16.68 s of it" against a 13.22 s deficit is visibly impossible.

    Corner losses can exceed the lap's own deficit, because the driver gave time
    back elsewhere. The synthetic sample never showed it -- its slow lap was slow
    everywhere -- and the first recorded session did, on both laps.
    """
    from f1coach_core import debrief_summary
    from f1coach_core.debrief import DebriefPoint

    def point(corner, lost):
        return DebriefPoint(
            corner=corner, apex_m=100.0, span_m=(0.0, 200.0),
            time_lost_s=lost, difference="", detail="",
        )

    class FakeLap:
        def __init__(self, lap_time):
            self.lap_time = lap_time

    lap, best = FakeLap(113.22), FakeLap(100.0)  # 13.22 s off
    over = debrief_summary(lap, best, [point("T2", 8.0), point("T10", 8.68)])

    assert "of it went at" not in over
    assert "16.68 s between them" in over
    assert "gave some of that back" in over

    under = debrief_summary(lap, best, [point("T2", 5.0), point("T10", 4.0)])
    assert "9.00 s of it went at T2, T10" in under
