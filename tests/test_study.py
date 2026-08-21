"""Study metrics: what the comparison is judged on, and what it refuses to guess."""

from pathlib import Path

import numpy as np
import pandas as pd

from f1coach_core.lap import Lap, StudyIdentity
from f1coach_core.study import (
    MIN_EXCURSION_SAMPLES,
    lap_metrics,
    summarise,
    summarise_all,
    summary_csv,
)


def make_lap(
    *,
    seconds: float = 10.0,
    track_pos: np.ndarray | None = None,
    damage: np.ndarray | None = None,
    driver: str | None = "A001",
    phase: str | None = "baseline",
    n: int = 100,
) -> Lap:
    t = np.linspace(0.0, seconds, n)
    data = {
        "t": t,
        "dist": np.linspace(0.0, 900.0, n),
        "speed": np.full(n, 40.0),
        "throttle": np.ones(n),
        "brake": np.zeros(n),
        "steer": np.zeros(n),
        "gear": np.full(n, 4),
    }
    if track_pos is not None:
        data["track_pos"] = track_pos
    if damage is not None:
        data["damage"] = damage
    return Lap(
        pd.DataFrame(data),
        Path("lap.csv"),
        schema_version=1,
        dist_derived=False,
        identity=StudyIdentity(driver=driver, phase=phase, setup="apex-study-v1"),
    )


def test_a_lap_without_the_channel_reports_unavailable_not_zero():
    """Counting a missing signal as "no excursions" rewards incomplete data."""
    metrics = lap_metrics(make_lap())

    assert metrics.off_track_events is None
    assert metrics.damage_events is None
    assert metrics.lap_time_s > 0


def test_leaving_the_track_is_counted_as_episodes_not_samples():
    """One long excursion and ten wobbles are different problems."""
    pos = np.zeros(100)
    pos[20:40] = 1.5  # one clear excursion
    pos[60:80] = -1.4  # and another, the other side of the track

    metrics = lap_metrics(make_lap(track_pos=pos))

    assert metrics.off_track_events == 2
    assert metrics.off_track_seconds > 0


def test_a_wheel_clipping_the_line_is_not_an_excursion():
    pos = np.zeros(100)
    pos[50 : 50 + MIN_EXCURSION_SAMPLES - 1] = 1.05

    assert lap_metrics(make_lap(track_pos=pos)).off_track_events == 0


def test_an_excursion_still_running_at_the_finish_line_still_counts():
    pos = np.zeros(100)
    pos[80:] = 1.6

    assert lap_metrics(make_lap(track_pos=pos)).off_track_events == 1


def test_exactly_on_the_edge_is_still_on_the_track():
    assert lap_metrics(make_lap(track_pos=np.full(100, 1.0))).off_track_events == 0


def test_damage_counts_impacts_not_the_level_it_sits_at():
    """Damage accumulates across a race, so lap three starts where lap two ended."""
    damage = np.concatenate([np.full(40, 500.0), np.full(30, 560.0), np.full(30, 600.0)])

    metrics = lap_metrics(make_lap(damage=damage))

    assert metrics.damage_events == 2  # two impacts, not a level of 600
    assert metrics.damage_total == 100.0  # what this lap added


def test_a_clean_lap_after_earlier_damage_reports_nothing_new():
    metrics = lap_metrics(make_lap(damage=np.full(100, 700.0)))

    assert metrics.damage_events == 0 and metrics.damage_total == 0.0


def test_a_phase_summary_is_the_row_a_paired_test_consumes():
    laps = [
        make_lap(seconds=18.0, track_pos=_excursions(1)),
        make_lap(seconds=17.0, track_pos=_excursions(2)),
        make_lap(seconds=17.5, track_pos=_excursions(0)),
    ]
    summary = summarise("A001", "baseline", laps)

    assert summary.laps == 3
    assert summary.best_lap_s == 17.0
    assert round(summary.mean_lap_s, 3) == 17.5
    assert summary.sd_lap_s > 0
    assert summary.off_track_events == 3  # summed across the phase


def test_a_phase_where_no_lap_carries_the_channel_reports_it_missing():
    summary = summarise("A001", "baseline", [make_lap(), make_lap()])

    assert summary.off_track_events is None
    assert summary.to_row()["off_track_events"] == ""  # blank, never 0


def test_laps_group_by_participant_and_phase():
    laps = [
        make_lap(driver="A001", phase="baseline", seconds=18.0),
        make_lap(driver="A001", phase="coached", seconds=16.0),
        make_lap(driver="B002", phase="baseline", seconds=19.0),
    ]
    summaries = summarise_all(laps)

    assert [(s.driver, s.phase) for s in summaries] == [
        ("A001", "baseline"),
        ("A001", "coached"),
        ("B002", "baseline"),
    ]


def test_a_lap_with_no_identity_joins_neither_side_of_the_comparison():
    """An unattributed lap cannot be assigned to a group, so it is not guessed at."""
    laps = [
        make_lap(driver="A001", phase="baseline"),
        make_lap(driver=None, phase=None),
        make_lap(driver="A001", phase=None),
    ]
    summaries = summarise_all(laps)

    assert len(summaries) == 1 and summaries[0].driver == "A001"


def test_the_csv_is_one_row_per_participant_per_phase():
    laps = [
        make_lap(driver="A001", phase="baseline", seconds=18.0, track_pos=_excursions(2)),
        make_lap(driver="A001", phase="coached", seconds=16.0, track_pos=_excursions(0)),
    ]
    text = summary_csv(summarise_all(laps))
    lines = text.strip().split("\n")

    assert lines[0].startswith("driver,phase,laps,best_lap_s")
    assert len(lines) == 3
    assert lines[1].startswith("A001,baseline,1,18.0")
    assert lines[2].startswith("A001,coached,1,16.0")


def test_a_single_lap_phase_has_no_spread_rather_than_an_error():
    summary = summarise("A001", "baseline", [make_lap(seconds=18.0)])
    assert summary.sd_lap_s == 0.0


def _excursions(count: int) -> np.ndarray:
    pos = np.zeros(100)
    for index in range(count):
        start = 10 + index * 25
        pos[start : start + 10] = 1.5
    return pos
