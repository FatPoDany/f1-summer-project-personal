"""Features stage: corner detection + evidence summary on the sample session.

The sample track has 8 named corners but only 5 produce speed-trace local
minima (T5 and T8 are shoulders straight into slower corners), so detection
finding 5 is correct, not a miss.
"""

import pytest

from f1coach_core import build_evidence_summary, detect_corners, load_sample_session

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
