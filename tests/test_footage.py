"""Placing a coaching stretch inside a recording, or admitting it cannot be done."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from f1coach_core import footage
from f1coach_core.debrief import DebriefPoint
from f1coach_core.lap import Lap


def make_lap(*, wall_clock: bool = True, start_epoch: float = 1_700_000_000.0) -> Lap:
    n = 200
    dist = np.linspace(0.0, 1000.0, n)
    t = np.linspace(0.0, 40.0, n)
    data = {
        "t": t,
        "dist": dist,
        "speed": np.full(n, 25.0),
        "throttle": np.ones(n),
        "brake": np.zeros(n),
        "steer": np.zeros(n),
        "gear": np.full(n, 4),
    }
    if wall_clock:
        data["wall_clock_s"] = start_epoch + t
    return Lap(pd.DataFrame(data), Path("lap.csv"), schema_version=1, dist_derived=False)


def point(span=(200.0, 400.0)):
    return DebriefPoint(
        corner="T3", apex_m=sum(span) / 2, span_m=span, time_lost_s=0.5,
        difference="", detail="",
    )


def test_a_stretch_is_located_by_wall_clock_not_by_assuming_real_time():
    lap = make_lap()
    window = footage.window_for(lap, point((200.0, 400.0)))

    assert window is not None
    # 200 m and 400 m of a 1000 m lap driven in 40 s: 8 s and 16 s in.
    assert window.from_wall_clock == pytest.approx(1_700_000_008.0, abs=0.3)
    assert window.to_wall_clock == pytest.approx(1_700_000_016.0, abs=0.3)
    assert window.seconds == pytest.approx(8.0, abs=0.5)


def test_every_corner_of_a_recorded_lap_can_be_located_in_the_footage():
    """Each corner in the table has to open its own clip, not most of them.

    The review used to be driven by the debrief, which keeps only the stretches
    that cost time. A participant reading the corner table picks a corner by
    name and expects footage of it, whether or not it was one of their worst.
    """
    from f1coach_core import corner_review_points, load_sample_session

    session = load_sample_session()
    best = session.best_lap
    lap = min(session.laps, key=lambda one: -one.lap_time)
    assert footage.has_wall_clock(lap)

    points = corner_review_points(lap, best)
    assert len(points) >= 5  # the recorded sample detects ten

    located = footage.windows_for(lap, points)

    assert set(located) == set(range(len(points)))
    for index, window in located.items():
        assert window.seconds > 0
        start, end = points[index].span_m
        assert end > start


def test_a_single_lap_review_still_locates_its_corners():
    """No reference means no time lost, and no bearing at all on the footage."""
    from f1coach_core import corner_review_points, load_sample_session

    lap = load_sample_session().best_lap
    points = corner_review_points(lap)

    assert points and all(point.time_lost_s is None for point in points)
    assert set(footage.windows_for(lap, points)) == set(range(len(points)))


def test_a_lap_without_the_clock_yields_no_footage_rather_than_a_guess():
    """A clip that looks right and shows the wrong corner teaches the wrong thing."""
    lap = make_lap(wall_clock=False)

    assert not footage.has_wall_clock(lap)
    assert footage.window_for(lap, point()) is None


def test_a_stretch_past_the_end_of_the_lap_is_not_located():
    lap = make_lap()
    assert footage.window_for(lap, point((5000.0, 6000.0))) is None


def test_a_clock_of_zero_is_treated_as_absent():
    """Old recorders wrote no clock; a zero would place every clip at the epoch."""
    lap = make_lap()
    lap.df["wall_clock_s"] = 0.0

    assert footage.window_for(lap, point()) is None


def test_only_the_stretches_that_can_be_located_come_back():
    lap = make_lap()
    points = [point((200.0, 400.0)), point((5000.0, 6000.0)), point((600.0, 800.0))]

    found = footage.windows_for(lap, points)

    assert set(found) == {0, 2}  # indexed, so the caller knows which are missing


def test_the_window_stops_inside_the_stretch_it_describes():
    """It must not run past what the coaching is talking about."""
    lap = make_lap()
    window = footage.window_for(lap, point((200.0, 400.0)))
    inside = lap.df[(lap.df["dist"] >= 200.0) & (lap.df["dist"] <= 400.0)]

    assert window.from_wall_clock >= float(inside["wall_clock_s"].iloc[0]) - 1e-6
    assert window.to_wall_clock <= float(inside["wall_clock_s"].iloc[-1]) + 1e-6


def test_a_cut_clip_reaches_the_player_as_a_path(qtbot, tmp_path, monkeypatch):
    """The pane kept the clip it was handed, and it has to be the clip.

    The ready signal carried a staleness token as well as the path, while the
    slot took one argument: every cut arrived as that token, so the player was
    handed an `object`, the pane sat on "Preparing the footage of this stretch…"
    for ever, and coming back to the same corner crashed on Path(object).

    It guards the other half too: without a reference held to the runnable, the
    task is collected before ffmpeg runs and this simply times out.
    """
    from apex.widgets import footage_pane
    from racecoach.telemetry.screen_capture import Recording

    if footage_pane.video_support() is None:
        pytest.skip("this build has no Qt multimedia, so there is no player to reach")

    clip = tmp_path / "cut.mp4"
    clip.write_bytes(b"clip")
    monkeypatch.setattr(footage_pane, "cut_clip", lambda *a, **k: clip)

    pane = footage_pane.FootagePane()
    qtbot.addWidget(pane)
    played = []
    pane._play = played.append

    pane.show_stretch(
        3,
        Recording(path=tmp_path / "session.mp4", started_at=1000.0, duration_s=60.0),
        footage.Window(from_wall_clock=1010.0, to_wall_clock=1015.0),
        tmp_path,
    )

    qtbot.waitUntil(lambda: 3 in pane._clips, timeout=5000)
    assert pane._clips[3] == str(clip)
    assert played == [str(clip)]
