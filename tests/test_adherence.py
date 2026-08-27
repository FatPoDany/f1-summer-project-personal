"""Holding a second run against what the first run was told.

Two things are being defended here. One is the arithmetic: an ask points in a
direction, a shift either goes that way far enough or it does not, and a
measurement nobody recorded must never read as a participant who ignored the
advice. The other is corner identity -- the whole module exists because corner
names are positional, so the test that a wrong anchor is refused is doing more
work than it looks like.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from f1coach_core.adherence import (
    ADHERENCE_COLUMNS,
    AdherenceError,
    AdherenceReport,
    Prescription,
    Shift,
    adherence,
    adherence_columns,
    prescriptions,
    run_prescriptions,
    session_adherence,
)
from f1coach_core.debrief import DebriefPoint
from f1coach_core.lap import Lap
from f1coach_core.session import Session


def _lap(name: str, *, brake_at: float = 120.0, dip_width: float = 90.0) -> Lap:
    """One corner at ~300 m, braked wherever asked, dipping to the same speed.

    Width and not depth is what makes a lap slower here. A deeper dip would move
    the minimum speed too, the debrief would rank corner speed above braking,
    and a test about braking would quietly stop being about braking. Holding the
    minimum fixed leaves the brake point as the only thing these laps disagree
    about.
    """
    dist = np.arange(0.0, 900.0, 5.0)
    dip = 30.0 * np.exp(-(((dist - 300.0) / dip_width) ** 2))
    speed = 60.0 - dip
    brake = np.where((dist > brake_at) & (dist < 290.0), 0.8, 0.0)
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


def _session(name: str, laps: list[Lap]) -> Session:
    return Session(name=name, path=Path(name), laps=tuple(laps), problems=())


def _point(*, corner: str = "T1", metric: str = "brake_point_m", gap: float | None = -30.0):
    return DebriefPoint(
        corner=corner,
        apex_m=300.0,
        span_m=(50.0, 500.0),
        time_lost_s=1.0,
        difference="Braked 30 m earlier",
        detail="brake point 90 m vs 120 m",
        category="braking",
        metric=metric,
        gap=gap,
    )


def _ask(*, direction: int = 1, threshold: float = 10.0) -> Prescription:
    return Prescription(
        corner="T1",
        apex_m=300.0,
        metric="brake_point_m",
        category="braking",
        direction=direction,
        gap=-30.0,
        threshold=threshold,
        said="Braked 30 m earlier",
    )


# -- what a debrief asked for ------------------------------------------------


def test_a_measured_point_becomes_an_ask_to_move_the_other_way():
    """Braking 30 m early is an ask to move the brake point later, not earlier."""
    (ask,) = prescriptions([_point(gap=-30.0)])

    assert ask.corner == "T1"
    assert ask.metric == "brake_point_m"
    assert ask.direction == 1
    assert ask.target == 30.0  # what closing the gap would take


def test_braking_late_is_the_same_ask_in_the_other_direction():
    (ask,) = prescriptions([_point(gap=25.0)])

    assert ask.direction == -1
    assert ask.target == -25.0


def test_a_point_no_measurement_explained_is_not_an_ask():
    """It said time went somewhere. Nobody can be held to that."""
    assert prescriptions([_point(metric="", gap=None)]) == []


def test_the_same_corner_on_three_laps_is_one_ask_said_three_times():
    told = prescriptions([_point(gap=-30.0), _point(gap=-20.0), _point(gap=-40.0)])

    assert len(told) == 1
    assert told[0].said_in == 3
    assert told[0].gap == -30.0  # the mean of the three
    assert told[0].said == "Braked 30 m earlier"  # the loudest one they read


def test_advice_that_contradicted_itself_is_not_an_ask():
    """Told early on one lap and late on another, they were given no direction.

    Nothing special-cases this: the two gaps average to almost nothing, and
    almost nothing does not clear the threshold that admitted either of them.
    """
    assert prescriptions([_point(gap=-30.0), _point(gap=28.0)]) == []


def test_two_metrics_at_one_corner_are_two_asks():
    told = prescriptions([
        _point(metric="brake_point_m", gap=-30.0),
        _point(metric="min_speed_kmh", gap=-8.0),
    ])

    assert {ask.metric for ask in told} == {"brake_point_m", "min_speed_kmh"}


# -- what the second run did about it ----------------------------------------


def test_a_shift_the_asked_for_way_counts_as_followed():
    shift = Shift(
        prescription=_ask(direction=1),
        before_mean=90.0,
        before_sd=4.0,
        before_n=3,
        after_mean=115.0,
        after_n=3,
    )

    assert shift.shift == 25.0
    assert shift.toward == 25.0
    assert shift.followed is True


def test_a_shift_short_of_the_threshold_does_not():
    shift = Shift(
        prescription=_ask(direction=1, threshold=10.0),
        before_mean=90.0,
        before_sd=4.0,
        before_n=3,
        after_mean=96.0,
        after_n=3,
    )

    assert shift.toward == 6.0
    assert shift.followed is False


def test_moving_the_wrong_way_is_not_followed():
    shift = Shift(
        prescription=_ask(direction=1),
        before_mean=90.0,
        before_sd=4.0,
        before_n=3,
        after_mean=60.0,
        after_n=3,
    )

    assert shift.toward == -30.0
    assert shift.followed is False


def test_nothing_measured_is_not_the_same_claim_as_not_followed():
    shift = Shift(
        prescription=_ask(),
        before_mean=90.0,
        before_sd=4.0,
        before_n=3,
        after_mean=None,
        after_n=0,
    )

    assert shift.shift is None
    assert shift.followed is None

    report = AdherenceReport(shifts=(shift,))
    assert report.prescribed == 1
    assert report.measured == 0
    assert report.followed == 0
    assert report.rate is None  # not 0.0: nobody measured it


def test_the_same_move_is_not_the_same_change_for_two_drivers():
    """25 m means one thing for a repeatable driver and another for a scruffy one.

    Found on the first real participant this ran on: a fixed 3 km/h threshold
    scored a 3.3 km/h drift as compliance from someone who wandered 11 km/h
    through that corner lap to lap.
    """
    tidy = Shift(
        prescription=_ask(),
        before_mean=90.0,
        before_sd=5.0,
        before_n=3,
        after_mean=115.0,
        after_n=3,
    )
    scruffy = Shift(
        prescription=_ask(),
        before_mean=90.0,
        before_sd=40.0,
        before_n=3,
        after_mean=115.0,
        after_n=3,
    )

    assert tidy.required == 10.0  # their own spread is under the threshold
    assert scruffy.required == 40.0  # their own spread is what has to be beaten

    assert tidy.followed is True
    assert scruffy.followed is False
    assert tidy.shift_sd == 5.0
    assert scruffy.shift_sd == 0.62


def test_a_participants_own_spread_can_only_ever_raise_the_bar():
    """Error in a three-lap sd must not be able to invent somebody who complied."""
    steady = Shift(
        prescription=_ask(threshold=10.0),
        before_mean=90.0,
        before_sd=1.0,
        before_n=3,
        after_mean=98.0,
        after_n=3,
    )

    assert steady.toward == 8.0
    assert steady.required == 10.0  # not 1.0
    assert steady.followed is False


def test_a_baseline_that_never_moved_leaves_no_scale_to_divide_by():
    shift = Shift(
        prescription=_ask(),
        before_mean=90.0,
        before_sd=0.0,
        before_n=3,
        after_mean=115.0,
        after_n=3,
    )

    assert shift.shift_sd is None


def test_one_lap_of_baseline_is_not_a_scatter():
    shift = Shift(
        prescription=_ask(),
        before_mean=90.0,
        before_sd=None,
        before_n=1,
        after_mean=115.0,
        after_n=3,
    )

    assert shift.required == 10.0  # nothing to raise the bar with
    assert shift.followed is True  # still measurable
    assert shift.shift_sd is None  # but not interpretable


def test_a_report_counts_only_what_could_be_measured():
    followed = Shift(_ask(), 90.0, 5.0, 3, 115.0, 3)
    ignored = Shift(_ask(), 90.0, 5.0, 3, 92.0, 3)
    unmeasured = Shift(_ask(), 90.0, 5.0, 3, None, 0)

    report = AdherenceReport(shifts=(followed, ignored, unmeasured))

    assert report.prescribed == 3
    assert report.measured == 2
    assert report.followed == 1
    assert report.rate == 0.5
    assert report.mean_shift_sd == 2.7  # (5.0 + 0.4) / 2 -- ignored still moved 2 m


# -- export ------------------------------------------------------------------


def test_columns_are_blank_when_nothing_was_computed():
    cells = adherence_columns(None)

    assert set(cells) == set(ADHERENCE_COLUMNS)
    assert set(cells.values()) == {""}


def test_a_measured_row_carries_its_counts():
    report = AdherenceReport(shifts=(Shift(_ask(), 90.0, 5.0, 3, 115.0, 3),))

    cells = adherence_columns(report)

    assert cells["advice_prescribed"] == 1
    assert cells["advice_followed"] == 1
    assert cells["advice_followed_rate"] == 1.0
    assert cells["advice_shift_sd"] == 5.0


def test_a_rate_nobody_could_compute_is_blank_not_zero():
    report = AdherenceReport(shifts=(Shift(_ask(), 90.0, 5.0, 3, None, 0),))

    assert adherence_columns(report)["advice_followed_rate"] == ""


# -- end to end, on telemetry ------------------------------------------------


def test_a_run_that_braked_early_is_asked_to_brake_later():
    baseline = [
        _lap("b-best", brake_at=120.0),
        _lap("b-1", brake_at=90.0, dip_width=140.0),
        _lap("b-2", brake_at=92.0, dip_width=140.0),
    ]

    told = run_prescriptions(baseline)

    assert told, "two laps braking 30 m early is something the debrief says"
    assert all(ask.metric == "brake_point_m" for ask in told)
    assert all(ask.direction == 1 for ask in told)


def test_a_second_run_that_braked_later_followed_the_advice():
    baseline = [
        _lap("b-best", brake_at=120.0),
        _lap("b-1", brake_at=90.0, dip_width=140.0),
        _lap("b-2", brake_at=92.0, dip_width=140.0),
    ]
    after = [_lap("a-1", brake_at=145.0), _lap("a-2", brake_at=150.0)]

    report = session_adherence(_session("baseline", baseline), _session("coached", after))

    assert report is not None
    assert report.measured == report.prescribed
    assert report.followed == report.prescribed
    assert report.rate == 1.0
    assert all(shift.toward > 0 for shift in report.shifts)


def test_a_second_run_that_braked_even_earlier_did_not():
    baseline = [
        _lap("b-best", brake_at=120.0),
        _lap("b-1", brake_at=90.0, dip_width=140.0),
        _lap("b-2", brake_at=92.0, dip_width=140.0),
    ]
    after = [_lap("a-1", brake_at=70.0), _lap("a-2", brake_at=72.0)]

    report = session_adherence(_session("baseline", baseline), _session("control", after))

    assert report is not None
    assert report.followed == 0
    assert report.rate == 0.0  # measured, and they went the other way
    assert all(shift.toward < 0 for shift in report.shifts)


def test_the_baseline_run_is_measured_whole_including_its_fastest_lap():
    """The run they drove is the run they drove; the anchor is one lap of it."""
    baseline = [
        _lap("b-best", brake_at=120.0),
        _lap("b-1", brake_at=90.0, dip_width=140.0),
        _lap("b-2", brake_at=92.0, dip_width=140.0),
    ]
    after = [_lap("a-1", brake_at=145.0)]

    report = session_adherence(_session("baseline", baseline), _session("coached", after))

    assert report is not None
    assert all(shift.before_n == 3 for shift in report.shifts)


def test_the_wrong_anchor_is_refused():
    """Corner names are positional, so a second detection is not a second opinion."""
    baseline = [
        _lap("b-best", brake_at=120.0),
        _lap("b-1", brake_at=90.0, dip_width=140.0),
    ]
    told = run_prescriptions(baseline)
    assert told

    elsewhere = Prescription(
        corner="T9",
        apex_m=800.0,
        metric="brake_point_m",
        category="braking",
        direction=1,
        gap=-30.0,
        threshold=10.0,
        said="Braked 30 m earlier",
    )

    with pytest.raises(AdherenceError, match="T9"):
        adherence(
            [*told, elsewhere],
            before=baseline,
            after=baseline,
            anchor=baseline[0],
        )


def test_a_baseline_with_no_laps_has_nothing_to_anchor_on():
    assert session_adherence(_session("baseline", []), _session("coached", [])) is None


def test_a_baseline_nobody_could_fault_asks_nothing():
    """Three identical laps lose no time, so the debrief names no corner."""
    same = [_lap(f"b-{i}", brake_at=120.0) for i in range(3)]

    assert run_prescriptions(same) == []
    assert session_adherence(_session("baseline", same), _session("coached", same)) is None


# -- grouping and export -----------------------------------------------------


def _identified(name: str, driver: str, phase: str, **kw) -> Lap:
    from dataclasses import replace

    from f1coach_core.lap import StudyIdentity

    return replace(
        _lap(name, **kw), identity=StudyIdentity(driver=driver, phase=phase)
    )


def _study_laps() -> list[Lap]:
    return [
        _identified("p1-b0", "P1", "baseline", brake_at=120.0),
        _identified("p1-b1", "P1", "baseline", brake_at=90.0, dip_width=140.0),
        _identified("p1-b2", "P1", "baseline", brake_at=92.0, dip_width=140.0),
        _identified("p1-c0", "P1", "coached", brake_at=145.0),
        _identified("p1-c1", "P1", "coached", brake_at=150.0),
    ]


def test_the_baseline_run_is_not_a_row_anybody_had_been_told_anything_on():
    from f1coach_core.adherence import adherence_all

    found = adherence_all(_study_laps())

    assert set(found) == {("P1", "coached")}


def test_a_participant_with_no_baseline_has_nothing_to_be_held_against():
    from f1coach_core.adherence import adherence_all

    orphan = [_identified("p2-c0", "P2", "coached", brake_at=145.0)]

    assert adherence_all(orphan) == {}


def test_laps_with_no_identity_are_dropped_not_pooled():
    from f1coach_core.adherence import adherence_all

    anonymous = [_lap("loose", brake_at=120.0), _lap("loose-2", brake_at=90.0)]

    assert adherence_all(anonymous) == {}


def test_the_summary_export_carries_adherence_on_the_later_row_only():
    from f1coach_core.adherence import adherence_all
    from f1coach_core.study import summarise_all, summary_csv

    laps = _study_laps()
    text = summary_csv(summarise_all(laps), adherence=adherence_all(laps))

    header, *rows = text.strip().splitlines()
    assert "advice_followed_rate" in header.split(",")
    columns = header.split(",")
    baseline, coached = (dict(zip(columns, row.split(","), strict=True)) for row in rows)

    assert baseline["phase"] == "baseline"
    assert baseline["advice_prescribed"] == ""  # nobody had told them anything yet
    assert coached["phase"] == "coached"
    assert int(coached["advice_prescribed"]) >= 1


def test_the_summary_export_is_blank_when_nobody_asked_for_adherence():
    from f1coach_core.study import summarise_all, summary_csv

    text = summary_csv(summarise_all(_study_laps()))

    columns = text.strip().splitlines()[0].split(",")
    for row in text.strip().splitlines()[1:]:
        cells = dict(zip(columns, row.split(","), strict=True))
        assert cells["advice_followed_rate"] == ""


def test_the_detail_export_has_a_row_for_every_ask():
    from f1coach_core.adherence import adherence_all
    from f1coach_core.study import adherence_csv

    laps = _study_laps()
    reports = adherence_all(laps)
    text = adherence_csv(reports)

    header, *rows = text.strip().splitlines()
    assert "required" in header.split(",")
    assert len(rows) == sum(report.prescribed for report in reports.values())
    assert all(row.startswith("P1,baseline,coached,") for row in rows)


# -- the screen the export is driven from ------------------------------------


def _write_cornered_lap(
    directory: Path,
    lap_number: int,
    *,
    driver: str,
    phase: str,
    brake_at: float,
    dip_width: float = 90.0,
) -> None:
    """A lap file with one real corner in it, written the way capture writes them.

    The study-view fixtures drive at a constant 40 m/s, which is right for a
    lap-time test and useless for this one: with no speed dip there are no
    corners, so no debrief point, so nothing anybody could have been asked to
    change. A guard against blank adherence columns has to be able to produce
    non-blank ones first.
    """
    directory.mkdir(parents=True, exist_ok=True)
    dist = np.arange(0.0, 900.0, 5.0)
    speed = 60.0 - 30.0 * np.exp(-(((dist - 300.0) / dip_width) ** 2))
    brake = np.where((dist > brake_at) & (dist < 290.0), 0.8, 0.0)
    throttle = np.where(dist > 310.0, 0.9, 0.0)
    t = np.concatenate([[0.0], np.cumsum(np.diff(dist) / speed[:-1])])
    rows = [
        f"{ti:.4f},{d:.1f},{v:.3f},{th:.1f},{br:.1f},0.0,4,1,0.000"
        for ti, d, v, th, br in zip(t, dist, speed, throttle, brake, strict=True)
    ]
    (directory / f"telemetry-lap{lap_number:02d}.csv").write_text(
        "# schema_version: 1\n"
        f"# lap: {lap_number}\n"
        f"# driver: {driver}\n"
        f"# phase: {phase}\n"
        "t,dist,speed,throttle,brake,steer,gear,sector,track_pos\n"
        + "\n".join(rows)
        + "\n",
        encoding="utf-8",
    )


def test_the_export_button_carries_adherence_and_not_a_screen_of_empty_columns(
    qtbot, tmp_path, monkeypatch
):
    """The same shape of bug as the background columns, and then the dose columns.

    Both shipped: a screen offering an export whose new columns were blank in
    every row, because the call that writes the file was not passed the thing
    the columns are made of. Twice is a pattern, so this asserts the third set
    arrives populated rather than merely present in the header.
    """
    from apex.study_view import StudyView

    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    baseline, coached = tmp_path / "A001-baseline", tmp_path / "A001-coached"
    _write_cornered_lap(baseline, 1, driver="A001", phase="baseline", brake_at=120.0)
    _write_cornered_lap(
        baseline, 2, driver="A001", phase="baseline", brake_at=90.0, dip_width=140.0
    )
    _write_cornered_lap(
        baseline, 3, driver="A001", phase="baseline", brake_at=92.0, dip_width=140.0
    )
    _write_cornered_lap(coached, 1, driver="A001", phase="coached", brake_at=145.0)
    _write_cornered_lap(coached, 2, driver="A001", phase="coached", brake_at=150.0)

    view = StudyView()
    qtbot.addWidget(view)
    view.reload([baseline, coached])

    target = tmp_path / "summary.csv"
    monkeypatch.setattr(
        "apex.study_view.QFileDialog.getSaveFileName", lambda *a, **k: (str(target), "")
    )
    view._export_csv()

    lines = target.read_text("utf-8").strip().splitlines()
    columns = lines[0].split(",")
    cells = [dict(zip(columns, line.split(","), strict=True)) for line in lines[1:]]
    first = next(row for row in cells if row["phase"] == "baseline")
    second = next(row for row in cells if row["phase"] == "coached")

    assert first["advice_prescribed"] == ""  # nobody had told them anything yet
    assert int(second["advice_prescribed"]) >= 1
    assert second["advice_followed_rate"] != ""
