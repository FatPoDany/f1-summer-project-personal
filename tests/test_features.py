"""Features stage: corner detection + evidence summary on the sample session.

The sample track has 8 named corners but only 5 produce speed-trace local
minima (T5 and T8 are shoulders straight into slower corners), so detection
finding 5 is correct, not a miss.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from f1coach_core import Lap, build_evidence_summary, detect_corners, features, load_sample_session
from f1coach_core.coach import opportunity_catalog

# expected apex windows on the 4.29 km sample track (segment maths ± margin)
APEX_WINDOWS = [(690, 880), (1060, 1440), (1840, 2070), (2740, 2930), (3630, 3860)]


@pytest.fixture(scope="module")
def session():
    return load_sample_session()


def test_detects_the_five_true_minima(session):
    corners = detect_corners(session.best_lap)
    assert [c["corner"] for c in corners] == ["T1", "T2", "T3", "T4", "T5"]
    for corner, (lo, hi) in zip(corners, APEX_WINDOWS, strict=True):
        assert lo < corner["apex_m"] < hi, corner
        d0, d1 = corner["span_m"]
        assert d0 < corner["apex_m"] < d1


def test_summary_of_the_ragged_lap(session):
    best = session.best_lap
    ragged = session.laps[2]  # lap_03
    summary = build_evidence_summary(ragged, best)

    assert summary["lap"]["name"] == "lap_03"
    assert summary["reference"]["name"] == "lap_02"
    assert summary["total_delta_s"] == pytest.approx(5.32, abs=0.02)

    corners = summary["corners"]
    assert len(corners) == 5
    brake_pairs = []
    for corner in corners:
        # lap_03 is slower everywhere: loses time and apex speed in every zone
        assert corner["time_lost_s"] > 0
        assert corner["min_speed_kmh"] < corner["ref_min_speed_kmh"]
        if corner["brake_point_m"] is not None and corner["ref_brake_point_m"] is not None:
            brake_pairs.append((corner["brake_point_m"], corner["ref_brake_point_m"]))
    # softer braking limit + slower targets: never later than the reference on
    # the 5 m grid, and clearly earlier somewhere (T1 carries a big override)
    assert brake_pairs and all(mine <= ref for mine, ref in brake_pairs)
    assert any(mine < ref for mine, ref in brake_pairs)
    # zone losses can't exceed the whole lap's loss
    assert sum(c["time_lost_s"] for c in corners) < summary["total_delta_s"] + 0.1


def test_summary_against_itself_is_flat(session):
    best = session.best_lap
    summary = build_evidence_summary(best, best)
    assert summary["total_delta_s"] == 0.0
    for corner in summary["corners"]:
        assert corner["time_lost_s"] == pytest.approx(0.0, abs=0.02)
        assert corner["min_speed_kmh"] == corner["ref_min_speed_kmh"]


def test_single_lap_summary_contains_deterministic_technique_evidence(session):
    summary = build_evidence_summary(session.laps[2])

    assert summary["analysis_mode"] == "single_lap"
    assert summary["reference"] is None
    assert summary["total_delta_s"] is None
    assert summary["corners"]
    assert opportunity_catalog(summary)
    for corner in summary["corners"]:
        assert corner["time_lost_s"] is None
        assert corner["technique_score"] >= 0.0
        assert {
            "brake_applications",
            "throttle_applications",
            "pedal_overlap_pct",
            "coast_distance_m",
        } <= set(corner)


def test_corner_table_extends_the_summary_zones_with_exits():
    session = load_sample_session()
    from f1coach_core import corner_table

    lap, best = session.laps[2], session.best_lap
    rows = corner_table(lap, best)
    summary = build_evidence_summary(lap, best)

    # same zones, same per-corner time deltas — one source of truth
    assert [r["corner"] for r in rows] == [c["corner"] for c in summary["corners"]]
    assert [r["delta_s"] for r in rows] == [c["time_lost_s"] for c in summary["corners"]]
    for row in rows:
        assert row["exit_speed_kmh"] > 0 and row["ref_exit_speed_kmh"] > 0
        assert {"brake_point_m", "ref_brake_point_m", "min_speed_kmh"} <= set(row)
    # the ragged lap loses time in corners overall
    assert sum(r["delta_s"] for r in rows) > 0


def _metric_lap(name: str, *, reference: bool = False, starts_above: bool = False) -> Lap:
    """A 600 m lap with one controlled corner on the core's exact 5 m grid."""
    dist = np.arange(0.0, 605.0, 5.0)
    centre = 300.0 if reference else 310.0
    floor = 22.0 if reference else 20.0
    speed = floor + np.abs(dist - centre) / 20.0
    brake = np.zeros_like(dist)
    throttle = np.zeros_like(dist)

    if starts_above:
        brake[(dist >= 100.0) & (dist < 180.0)] = 0.8
        brake[dist == 180.0] = 0.1
        throttle[dist >= 300.0] = 0.96
    else:
        brake_on = 140.0 if reference else 125.0
        brake_off = 195.0 if reference else 180.0
        brake[(dist >= brake_on) & (dist < brake_off)] = 0.9 if reference else 0.8
        brake[dist == brake_off] = 0.1

        reapply = 320.0 if reference else 330.0
        half = 350.0 if reference else 370.0
        full = 400.0 if reference else 430.0
        throttle[(dist >= reapply) & (dist < half)] = 0.1
        throttle[(dist >= half) & (dist < full)] = 0.5
        throttle[dist >= full] = 0.95

    # Time only needs to be monotonic for distance-aligned feature tests.
    t = dist / (31.0 if reference else 30.0)
    frame = pd.DataFrame(
        {
            "t": t,
            "dist": dist,
            "speed": speed,
            "throttle": throttle,
            "brake": brake,
            "steer": np.zeros_like(dist),
            "gear": np.full_like(dist, 4),
        }
    )
    return Lap(frame, Path(f"{name}.csv"), schema_version=1, dist_derived=False)


def _one_corner(_reference: Lap) -> list[dict]:
    return [{"corner": "T1", "apex_m": 300.0, "span_m": [100.0, 500.0]}]


def test_personalised_corner_facts_are_paired_and_shared(monkeypatch):
    monkeypatch.setattr(features, "detect_corners", _one_corner)
    lap = _metric_lap("mine")
    reference = _metric_lap("reference", reference=True)

    summary_row = build_evidence_summary(lap, reference)["corners"][0]
    table_row = features.corner_table(lap, reference)[0]

    assert summary_row["entry_speed_kmh"] == 109.8
    assert summary_row["ref_entry_speed_kmh"] == 115.2
    assert summary_row["min_speed_kmh"] == 72.0
    assert summary_row["min_speed_point_m"] == 310.0
    assert summary_row["ref_min_speed_kmh"] == 79.2
    assert summary_row["ref_min_speed_point_m"] == 300.0
    assert summary_row["exit_speed_kmh"] == 106.2
    assert summary_row["ref_exit_speed_kmh"] == 115.2

    assert summary_row["brake_point_m"] == 125.0
    assert summary_row["ref_brake_point_m"] == 140.0
    assert summary_row["peak_brake_pct"] == 80.0
    assert summary_row["ref_peak_brake_pct"] == 90.0
    assert summary_row["brake_release_m"] == 180.0
    assert summary_row["ref_brake_release_m"] == 195.0

    assert summary_row["throttle_reapply_m"] == 330.0
    assert summary_row["ref_throttle_reapply_m"] == 320.0
    assert summary_row["throttle_point_m"] == 370.0  # legacy 50% field, now a crossing
    assert summary_row["ref_throttle_point_m"] == 350.0
    assert summary_row["full_throttle_m"] == 430.0
    assert summary_row["ref_full_throttle_m"] == 400.0
    assert summary_row["exit_throttle_pct"] == 95.0
    assert summary_row["ref_exit_throttle_pct"] == 95.0
    assert summary_row["coast_distance_m"] == 150.0
    assert summary_row["ref_coast_distance_m"] == 125.0

    # The table consumes exactly the same facts and only renames the delta.
    assert table_row["delta_s"] == summary_row["time_lost_s"]
    for key, value in summary_row.items():
        if key != "time_lost_s":
            assert table_row[key] == value


def test_zone_start_above_threshold_is_not_a_fabricated_crossing(monkeypatch):
    monkeypatch.setattr(features, "detect_corners", _one_corner)
    lap = _metric_lap("mine", starts_above=True)
    reference = _metric_lap("reference", reference=True, starts_above=True)

    row = build_evidence_summary(lap, reference)["corners"][0]

    assert row["brake_point_m"] is None
    assert row["ref_brake_point_m"] is None
    assert row["brake_release_m"] == 180.0  # a real 80% -> 10% down-crossing
    assert row["throttle_reapply_m"] is None
    assert row["throttle_point_m"] is None
    assert row["full_throttle_m"] is None
    assert row["coast_distance_m"] is None
    assert row["exit_throttle_pct"] == 96.0
