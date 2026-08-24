"""Replaying a stretch: real elapsed time, exact span, and honest degradation."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from apex.widgets.replay_window import ReplayWindow
from apex.widgets.track_replay import TrackReplay
from f1coach_core import DebriefPoint, Lap
from sample_laps import lap_without_position


def _positioned_lap(source: str = "lap.csv") -> Lap:
    """A lap carrying world position, as a real capture does.

    Synthesised rather than read from a captured run so the test runs anywhere,
    including CI: a test that skips when the data is absent protects nothing.
    """
    dist = np.arange(0.0, 1200.0, 2.0)
    speed = 40.0 + 10.0 * np.sin(dist / 150.0)
    angle = dist / 1200.0 * 2 * np.pi  # one closed loop
    return Lap(
        pd.DataFrame(
            {
                "t": np.concatenate([[0.0], np.cumsum(np.diff(dist) / speed[:-1])]),
                "dist": dist,
                "speed": speed,
                "throttle": np.clip(np.cos(dist / 120.0), 0, 1),
                "brake": np.clip(-np.cos(dist / 120.0), 0, 1),
                "steer": np.zeros_like(dist),
                "gear": np.full_like(dist, 4),
                "x": 190.0 * np.cos(angle),
                "y": 120.0 * np.sin(angle),
            }
        ),
        Path(source),
        schema_version=1,
        dist_derived=False,
    )


def test_a_stretch_with_no_width_still_spans_two_samples():
    """Shared by the replay and the Analysis track, so the guard belongs here.

    A zero-width citation would otherwise produce last <= first, which draws
    nothing and leaves the reader looking for a highlight that is not there.
    """
    from apex.widgets.track_replay import span_indices

    lap = _positioned_lap()
    first, last = span_indices(lap, 600.0, 600.0)

    assert last > first
    assert last < len(lap.df)


def test_the_replayed_span_is_exactly_the_requested_stretch(qtbot):
    lap = _positioned_lap()
    view = TrackReplay()
    qtbot.addWidget(view)

    view.set_stretch(lap, 500.0, 950.0, "T1")

    dist = lap.df["dist"]
    assert dist.iloc[view._first] >= 500.0
    assert dist.iloc[view._first - 1] < 500.0
    assert dist.iloc[view._last] <= 950.0 + 1e-6


def test_playback_advances_by_the_time_the_driver_actually_took(qtbot):
    """One frame of playback must cover one frame of lap time, not one sample."""
    lap = _positioned_lap()
    view = TrackReplay()
    qtbot.addWidget(view)
    view.set_stretch(lap, 500.0, 950.0, "T1")

    t = lap.df["t"]
    before = float(t.iloc[view._slider.value()])
    view._advance()
    after = float(t.iloc[view._slider.value()])

    assert after > before
    assert after - before == pytest.approx(0.033, abs=0.03)


def test_playback_goes_round_again_rather_than_stopping_at_the_end(qtbot):
    """One press replays the stretch until it is paused.

    A corner is watched several times over before anything about it sinks in,
    and a few seconds of footage that stopped dead each round made the
    participant reach for the button instead of the driving.
    """
    lap = _positioned_lap()
    view = TrackReplay()
    qtbot.addWidget(view)
    view.set_stretch(lap, 500.0, 950.0, "T1")
    view._play.setChecked(True)
    assert view.playing

    view._slider.setValue(view._last - 1)
    view._advance()

    assert view._slider.value() == view._last  # the stretch's last metres are shown
    assert view.playing

    view._advance()

    assert view._slider.value() == view._first
    assert view.playing


def test_a_lap_without_position_says_so_instead_of_drawing_nothing(qtbot):
    """A lap from a source without the position channel: readout still works."""
    lap = lap_without_position()
    assert not lap.has_track_map
    view = TrackReplay()
    qtbot.addWidget(view)

    view.set_stretch(lap, 100.0, 400.0, "T1")

    assert view._map._x == []  # nothing to draw, and paintEvent explains why
    assert "km/h" in view._readout.text()  # the numbers are still available


def test_replay_window_titles_the_stretch_it_is_showing(qtbot):
    lap = _positioned_lap()
    window = ReplayWindow()
    qtbot.addWidget(window)
    point = DebriefPoint(
        corner="T4",
        apex_m=700.0,
        span_m=(500.0, 950.0),
        time_lost_s=0.42,
        difference="Braked 31 m earlier",
        detail="brake point 560 m vs 591 m",
    )

    window.show_stretch(lap, point)

    assert "T4" in window._replay._title.text()
    assert "Braked 31 m earlier" in window._replay._title.text()


def test_a_pedal_reads_as_its_travel_not_the_raw_command(qtbot):
    """berniw commands past full throttle; the car cannot go past full throttle."""
    lap = _positioned_lap()
    lap.df.loc[lap.df.index[:50], "throttle"] = 1.253  # as recorded from berniw
    view = TrackReplay()
    qtbot.addWidget(view)

    view.set_stretch(lap, 0.0, 200.0, "T1")

    assert "throttle 100%" in view._readout.text()
    # The recorded value itself is untouched.
    assert lap.df["throttle"].max() == pytest.approx(1.253)


def test_the_legend_and_the_readout_name_the_lap_being_compared_with(qtbot):
    """The blue line is whichever lap the driver picked, not their best one.

    Both said "best lap" whatever they were handed, because the session best was
    once the only reference on offer. A driver comparing two mid-session laps
    was reading a label for a lap that was not on the screen.
    """
    lap = _positioned_lap("lap07.csv")
    reference = _positioned_lap("lap02.csv")
    view = TrackReplay()
    qtbot.addWidget(view)

    view.set_stretch(lap, 500.0, 950.0, "T1", reference=reference)

    legend = view._legend.text()
    assert "lap07" in legend and "lap02" in legend  # both colours are accounted for
    assert "best lap" not in legend
    assert view._ref_readout.text().startswith("lap02 here:")
    assert "km/h" in view._ref_readout.text()
    assert "best lap" not in view._readout.text()


def test_a_replay_with_nothing_to_compare_claims_no_second_lap(qtbot):
    """No reference means no blue line, so nothing may be labelled as one."""
    view = TrackReplay()
    qtbot.addWidget(view)

    view.set_stretch(_positioned_lap(), 500.0, 950.0, "T1")

    assert view._legend.text() == ""
    assert not view._ref_readout.isVisibleTo(view)
    assert view._ref_readout.text() == ""
