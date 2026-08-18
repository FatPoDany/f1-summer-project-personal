"""Replaying a stretch: real elapsed time, exact span, and honest degradation."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from apex.widgets.replay_window import ReplayWindow
from apex.widgets.track_replay import TrackReplay
from f1coach_core import DebriefPoint, Lap, load_sample_session


def _positioned_lap() -> Lap:
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
        Path("lap.csv"),
        schema_version=1,
        dist_derived=False,
    )


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


def test_playback_stops_at_the_end_rather_than_looping(qtbot):
    lap = _positioned_lap()
    view = TrackReplay()
    qtbot.addWidget(view)
    view.set_stretch(lap, 500.0, 950.0, "T1")
    view._play.setChecked(True)
    assert view.playing

    view._slider.setValue(view._last - 1)
    view._advance()

    assert view._slider.value() == view._last
    assert not view.playing


def test_a_lap_without_position_says_so_instead_of_drawing_nothing(qtbot):
    """Sample laps predate the position channel; the readout must still work."""
    lap = load_sample_session().best_lap
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
