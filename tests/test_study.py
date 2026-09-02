"""Study metrics: what the comparison is judged on, and what it refuses to guess."""

import csv
import io
from pathlib import Path

import numpy as np
import pandas as pd

from f1coach_core.lap import Lap, StudyIdentity
from f1coach_core.participant import Background
from f1coach_core.study import (
    EXPOSURE_COLUMNS,
    MIN_EXCURSION_SAMPLES,
    lap_columns,
    lap_csv,
    lap_metrics,
    lap_rows,
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
    source: str = "lap.csv",
    lap_number: int | None = None,
    setup: str | None = "apex-study-v1",
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
        Path(source),
        schema_version=1,
        dist_derived=False,
        lap_number=lap_number,
        identity=StudyIdentity(driver=driver, phase=phase, setup=setup),
    )


def _first_row(text: str) -> dict:
    return next(csv.DictReader(io.StringIO(text)))


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

    assert lines[0].startswith("driver,phase,setup,laps,best_lap_s")
    assert len(lines) == 3
    assert lines[1].startswith("A001,baseline,apex-study-v1,1,18.0")
    assert lines[2].startswith("A001,coached,apex-study-v1,1,16.0")


def test_a_single_lap_phase_has_no_spread_rather_than_an_error():
    summary = summarise("A001", "baseline", [make_lap(seconds=18.0)])
    assert summary.sd_lap_s == 0.0


def _excursions(count: int) -> np.ndarray:
    pos = np.zeros(100)
    for index in range(count):
        start = 10 + index * 25
        pos[start : start + 10] = 1.5
    return pos


def test_every_lap_is_its_own_row_so_a_learning_curve_can_be_drawn():
    """A phase summarised to one number carries no slope; its laps do.

    Whether a coached participant improved more than practice alone explains is
    a question about the trend inside each phase, and a mean cannot be asked it.
    """
    laps = [
        make_lap(driver="A001", phase="baseline", seconds=22.0, source="run-lap01.csv"),
        make_lap(driver="A001", phase="baseline", seconds=21.0, source="run-lap02.csv"),
        make_lap(driver="A001", phase="baseline", seconds=20.0, source="run-lap03.csv"),
    ]

    rows = lap_rows(laps)

    assert [row.lap for row in rows] == [1, 2, 3]
    assert [round(row.lap_time_s) for row in rows] == [22, 21, 20]


def test_the_lap_index_follows_the_order_driven_not_the_simulators_counter():
    """The counter restarts at 1 every run; a phase can take two runs to record."""
    laps = [
        make_lap(driver="A001", phase="baseline", seconds=21.0,
                 source="human-1-1787740000-1-lap02.csv", lap_number=2),
        make_lap(driver="A001", phase="baseline", seconds=19.0,
                 source="human-1-1787750000-1-lap01.csv", lap_number=1),
        make_lap(driver="A001", phase="baseline", seconds=22.0,
                 source="human-1-1787740000-1-lap01.csv", lap_number=1),
    ]

    rows = lap_rows(laps)

    assert [row.lap for row in rows] == [1, 2, 3]
    # Both of the first run's laps precede the second run's, and the counter
    # they carry is kept beside the index rather than used as it.
    assert [row.race_lap for row in rows] == [1, 2, 1]
    assert [round(row.lap_time_s) for row in rows] == [22, 21, 19]


def test_an_unattributed_lap_cannot_join_either_side_of_the_comparison():
    laps = [
        make_lap(driver="A001", phase="baseline"),
        make_lap(driver=None, phase=None, source="loose.csv"),
    ]

    assert [row.driver for row in lap_rows(laps)] == ["A001"]


def test_prior_experience_rides_on_every_lap_row(tmp_path, monkeypatch):
    """A regression adjusting for experience must not need a second file joined in."""
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path))
    background = Background(participant_id="A001", racing_games="weekly", age_band="25-34")

    text = lap_csv(
        [
            make_lap(driver="A001", phase="baseline", source="run-lap01.csv"),
            make_lap(driver="A001", phase="baseline", source="run-lap02.csv"),
        ],
        backgrounds={"A001": background},
    )
    lines = text.strip().splitlines()

    assert lines[0] == ",".join(lap_columns())
    assert len(lines) == 3
    for line in lines[1:]:
        assert line.endswith("weekly,3,,,,,25-34,1")


def test_a_channel_the_recording_lacks_stays_blank_per_lap_too():
    """Per-lap rows keep the rule the per-phase rows keep: missing is not zero."""
    text = lap_csv([make_lap(driver="A001", phase="baseline", source="run-lap01.csv")])
    header, row = text.strip().splitlines()
    cells = dict(zip(header.split(","), row.split(","), strict=True))

    assert cells["off_track_events"] == ""
    assert cells["damage_events"] == ""
    assert cells["lap"] == "1"
    assert cells["source"] == "run-lap01.csv"


def test_the_dose_rides_on_the_row_of_the_phase_that_was_reviewed():
    """A dose-response test must not need a third file joined in either."""
    from f1coach_core.exposure import CORNER_VIEW, REPORT_VIEW, ReviewView

    logs = {
        "A001": [
            ReviewView(driver="A001", phase="baseline", kind=CORNER_VIEW,
                       seconds=40.0, corner="T3", advice=True),
            ReviewView(driver="A001", phase="baseline", kind=REPORT_VIEW, seconds=25.0),
        ]
    }
    text = summary_csv(
        [
            summarise("A001", "baseline", [make_lap(driver="A001", phase="baseline")]),
            summarise("A001", "coached", [make_lap(driver="A001", phase="coached")]),
        ],
        exposure=logs,
    )
    header, *rows = text.strip().splitlines()
    cells = [dict(zip(header.split(","), row.split(","), strict=True)) for row in rows]

    assert cells[0]["phase"] == "baseline"
    assert cells[0]["review_seconds"] == "40.0"
    assert cells[0]["advice_seconds"] == "40.0"
    assert cells[0]["report_seconds"] == "25.0"
    # The second run is the response, not another dose: what they read after it
    # cannot have caused it, and this row must not borrow the first row's.
    assert cells[1]["phase"] == "coached"
    assert cells[1]["review_seconds"] == "0"


def test_the_row_says_which_assignment_produced_it():
    """Two tracks are assignable, and lap times across them are not comparable.

    Without this column a CG Speedway row and an aalborg row sit in the same
    file, in the same units, with nothing to tell them apart.
    """
    laps = [make_lap(driver="A001", phase="baseline", setup="apex-study-speedway-v1")]

    assert _first_row(summary_csv(summarise_all(laps)))["setup"] == "apex-study-speedway-v1"
    assert _first_row(lap_csv(laps))["setup"] == "apex-study-speedway-v1"


def test_a_session_driven_outside_the_presets_says_so_rather_than_passing():
    """The CLI can be told to launch unassigned. That has to remain visible in
    the export, because such a lap was driven under conditions nothing recorded."""
    laps = [make_lap(driver="A001", phase="baseline", setup=None)]

    assert _first_row(summary_csv(summarise_all(laps)))["setup"] == ""
    assert _first_row(lap_csv(laps))["setup"] == ""


def test_a_phase_that_mixes_two_assignments_is_shown_mixed_not_averaged():
    """One row per participant per phase is a lie if the phase pooled two
    tracks. The row still exists -- dropping it would hide the mistake -- but it
    names both, so nobody reads its best lap as a time on either circuit."""
    laps = [
        make_lap(driver="A001", phase="baseline", seconds=126.0, setup="apex-study-v1"),
        make_lap(
            driver="A001", phase="baseline", seconds=43.0, setup="apex-study-speedway-v1"
        ),
    ]

    row = _first_row(summary_csv(summarise_all(laps)))

    assert row["setup"] == "apex-study-speedway-v1+apex-study-v1"
    assert row["laps"] == "2"


def test_a_participant_nobody_recorded_gets_blanks_not_zeros():
    """Zero is a claim about them; blank is a claim about the record."""
    summaries = [summarise("A001", "baseline", [make_lap(driver="A001")])]

    unknown = _first_row(summary_csv(summaries))
    watched = _first_row(summary_csv(summaries, exposure={"A001": []}))

    assert [unknown[name] for name in EXPOSURE_COLUMNS] == [""] * len(EXPOSURE_COLUMNS)
    assert [watched[name] for name in EXPOSURE_COLUMNS] == ["0"] * len(EXPOSURE_COLUMNS)


def test_every_view_gets_its_own_row_so_an_implausible_one_can_be_seen():
    """A dose is only a dose if the window was being read, and only the raw
    rows can show a review left open through a coffee break."""
    from f1coach_core.exposure import CORNER_VIEW, ReviewView
    from f1coach_core.study import exposure_csv

    text = exposure_csv({
        "A001": [
            ReviewView(driver="A001", phase="baseline", kind=CORNER_VIEW,
                       seconds=12.0, corner="T3", lap="run-lap01.csv", advice=True,
                       at="2026-08-27T10:00:00+00:00"),
            ReviewView(driver="A001", phase="baseline", kind=CORNER_VIEW,
                       seconds=4210.0, corner="T1", lap="run-lap01.csv",
                       at="2026-08-27T11:20:00+00:00"),
        ]
    })
    header, *rows = text.strip().splitlines()

    assert header.startswith("driver,phase,kind,corner,lap,seconds,advice")
    assert len(rows) == 2
    assert rows[0].startswith("A001,baseline,corner,T3,run-lap01.csv,12.0,1")
    assert ",4210.0,0," in rows[1]
