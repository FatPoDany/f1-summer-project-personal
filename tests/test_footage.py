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

    qtbot.waitUntil(lambda: played == [str(clip)], timeout=5000)


def test_a_moment_on_track_maps_to_a_position_in_the_clip(qtbot, tmp_path):
    """The clip carries lead-in, so its start is not the corner's start.

    And near the beginning of a recording that lead is truncated, so assuming a
    fixed three seconds puts the marker seconds away from the picture.
    """
    from apex.widgets import footage_pane
    from racecoach.telemetry.screen_capture import CLIP_LEAD_S, Recording

    if footage_pane.video_support() is None:
        pytest.skip("this build has no Qt multimedia, so there is no player to line up")

    pane = footage_pane.FootagePane()
    qtbot.addWidget(pane)
    recording = Recording(path=tmp_path / "s.mp4", started_at=1000.0, duration_s=600.0)

    # A corner well inside the recording: the clip opens CLIP_LEAD_S early, so
    # the corner itself begins that far into it.
    pane._recording = recording
    pane._window = footage.Window(from_wall_clock=1100.0, to_wall_clock=1106.0)
    assert pane.position_ms_for(1100.0) == int(CLIP_LEAD_S * 1000)
    assert pane.position_ms_for(1106.0) == int((CLIP_LEAD_S + 6.0) * 1000)

    # A corner in the first seconds: there was no room for the full lead, and
    # the clip therefore starts at the recording's own start.
    pane._window = footage.Window(from_wall_clock=1001.0, to_wall_clock=1004.0)
    assert pane.position_ms_for(1001.0) == 1000  # one second in, not CLIP_LEAD_S
    assert pane.position_ms_for(1004.0) == 4000


def test_footage_is_loaded_paused_rather_than_playing_itself(qtbot, tmp_path, monkeypatch):
    """Two players each starting on their own is what put them out of step."""
    from apex.widgets import footage_pane
    from racecoach.telemetry.screen_capture import Recording

    if footage_pane.video_support() is None:
        pytest.skip("this build has no Qt multimedia, so there is nothing to pause")

    clip = tmp_path / "cut.mp4"
    clip.write_bytes(b"clip")
    monkeypatch.setattr(footage_pane, "cut_clip", lambda *a, **k: clip)
    pane = footage_pane.FootagePane()
    qtbot.addWidget(pane)
    calls = []
    monkeypatch.setattr(pane._player, "pause", lambda: calls.append("pause"))
    monkeypatch.setattr(pane._player, "play", lambda: calls.append("play"))
    monkeypatch.setattr(pane._player, "setSource", lambda _url: None)

    pane.show_stretch(
        0,
        Recording(path=tmp_path / "s.mp4", started_at=1000.0, duration_s=60.0),
        footage.Window(from_wall_clock=1010.0, to_wall_clock=1015.0),
        tmp_path,
    )
    qtbot.waitUntil(lambda: "pause" in calls, timeout=5000)

    assert "play" not in calls, "the clip must wait for the replay's transport"


def test_a_clip_that_ran_out_is_started_again_when_the_replay_goes_round(
    qtbot, tmp_path, monkeypatch
):
    """Playback repeats, and a stopped player cannot be restarted by seeking.

    A stretch at the very end of a session has no tail left to cut, so the clip
    runs out as the corner does and Qt stops the player. Every later round would
    then hold one frozen frame beside a marker that kept moving.
    """
    from apex.widgets import footage_pane
    from racecoach.telemetry.screen_capture import CLIP_LEAD_S, Recording

    if footage_pane.video_support() is None:
        pytest.skip("this build has no Qt multimedia, so there is no player to restart")

    pane = footage_pane.FootagePane()
    qtbot.addWidget(pane)
    pane._recording = Recording(path=tmp_path / "s.mp4", started_at=1000.0, duration_s=60.0)
    pane._window = footage.Window(from_wall_clock=1040.0, to_wall_clock=1046.0)
    corner_start = int(CLIP_LEAD_S * 1000)
    calls = []
    monkeypatch.setattr(pane._player, "setPosition", lambda ms: calls.append(("seek", ms)))
    monkeypatch.setattr(pane._player, "play", lambda: calls.append("play"))

    pane.seek_to(1040.0)
    assert calls == [("seek", corner_start)]  # paused: nobody asked for it to run

    pane.resume()
    calls.clear()
    pane.seek_to(1040.0)

    assert calls == [("seek", corner_start), "play"]


def test_two_stretches_cannot_be_written_to_the_same_clip(tmp_path):
    """A session keeps one clips folder for every lap and every comparison.

    Named by position alone, corner three of a technique review and corner three
    of a comparison were the same file: whichever was cut last is what the
    participant was shown, and a clip of the wrong stretch runs out while the
    marker is still driving the corner it claims to be about.
    """
    from apex.widgets.footage_pane import clip_path
    from racecoach.telemetry.screen_capture import Recording

    recording = Recording(path=tmp_path / "s.mp4", started_at=1000.0, duration_s=600.0)
    review = footage.Window(from_wall_clock=1100.0, to_wall_clock=1120.0)
    comparison = footage.Window(from_wall_clock=1100.0, to_wall_clock=1113.0)

    assert clip_path(tmp_path, 3, recording, review) != clip_path(
        tmp_path, 3, recording, comparison
    )
    # And the same stretch is the same file, or nothing would ever be reused.
    assert clip_path(tmp_path, 3, recording, review) == clip_path(
        tmp_path, 3, recording, footage.Window(1100.0, 1120.0)
    )


def test_a_clip_being_cut_is_announced_so_the_transport_can_wait(qtbot, tmp_path, monkeypatch):
    """Play during the wait started the marker against a picture that was not there."""
    from apex.widgets import footage_pane
    from racecoach.telemetry.screen_capture import Recording

    if footage_pane.video_support() is None:
        pytest.skip("this build has no Qt multimedia, so nothing is ever cut")

    def cut(_recording, _from, _to, destination):
        Path(destination).write_bytes(b"clip")
        return Path(destination)

    monkeypatch.setattr(footage_pane, "cut_clip", cut)
    pane = footage_pane.FootagePane()
    qtbot.addWidget(pane)
    pane._play = lambda _path: None
    states = []
    pane.busyChanged.connect(states.append)

    pane.show_stretch(
        0,
        Recording(path=tmp_path / "s.mp4", started_at=1000.0, duration_s=60.0),
        footage.Window(from_wall_clock=1010.0, to_wall_clock=1015.0),
        tmp_path,
    )

    qtbot.waitUntil(lambda: states == [True, False], timeout=5000)
    assert not pane.busy


def test_a_session_without_footage_never_says_it_is_working(qtbot, tmp_path):
    """Nothing is being cut, so Play must not be held back waiting for it."""
    from apex.widgets import footage_pane

    pane = footage_pane.FootagePane()
    qtbot.addWidget(pane)
    states = []
    pane.busyChanged.connect(states.append)

    pane.show_stretch(0, None, None, tmp_path)  # a session that was not recorded

    assert states == []
    assert not pane.busy


def test_a_clip_still_being_cut_survives_the_next_corner_being_opened(
    qtbot, tmp_path, monkeypatch
):
    """Dropping a runnable that ffmpeg's thread is still inside takes Apex with it.

    Not an exception: an access violation in the interpreter, leaving two
    half-written clips on disk as the only trace of what happened. Moving to the
    next corner while one is being cut is the ordinary way to use this window.
    """
    import threading

    from apex.widgets import footage_pane
    from racecoach.telemetry.screen_capture import Recording

    if footage_pane.video_support() is None:
        pytest.skip("this build has no Qt multimedia, so nothing is ever cut")

    started, release = threading.Event(), threading.Event()

    def slow_cut(_recording, _from, _to, destination):
        started.set()
        release.wait(10)
        Path(destination).write_bytes(b"clip")
        return Path(destination)

    monkeypatch.setattr(footage_pane, "cut_clip", slow_cut)
    pane = footage_pane.FootagePane()
    qtbot.addWidget(pane)
    pane._play = lambda _path: None
    recording = Recording(path=tmp_path / "s.mp4", started_at=1000.0, duration_s=600.0)

    pane.show_stretch(0, recording, footage.Window(1010.0, 1015.0), tmp_path)
    qtbot.waitUntil(started.is_set, timeout=5000)
    cutting = pane._tasks[0]
    pane.show_stretch(1, recording, footage.Window(1030.0, 1036.0), tmp_path)

    assert cutting in pane._tasks, "the task ffmpeg is inside must stay referenced"
    assert not cutting.autoDelete(), "and the pool must not delete it either"
    release.set()
    qtbot.waitUntil(lambda: len(list(tmp_path.glob("*.mp4"))) == 2, timeout=10000)


def test_a_clip_that_arrives_after_the_review_closed_is_not_an_error(
    qtbot, tmp_path, monkeypatch
):
    """Closing the review while ffmpeg works is ordinary, not a fault.

    The reply comes back to a pane Qt has already destroyed, and every Qt call
    in it raises. Left uncaught that escapes into the event loop as a traceback
    about a deleted signal source, which is not something to show a participant.
    """
    import threading

    import shiboken6

    from apex.widgets import footage_pane
    from racecoach.telemetry.screen_capture import Recording

    if footage_pane.video_support() is None:
        pytest.skip("this build has no Qt multimedia, so nothing is ever cut")

    started, release = threading.Event(), threading.Event()

    def slow_cut(_recording, _from, _to, destination):
        started.set()
        release.wait(10)
        Path(destination).write_bytes(b"clip")
        return Path(destination)

    monkeypatch.setattr(footage_pane, "cut_clip", slow_cut)
    pane = footage_pane.FootagePane()
    pane.show_stretch(
        0,
        Recording(path=tmp_path / "s.mp4", started_at=1000.0, duration_s=600.0),
        footage.Window(1010.0, 1015.0),
        tmp_path,
    )
    qtbot.waitUntil(started.is_set, timeout=5000)

    shiboken6.delete(pane)  # the participant closed the review
    release.set()

    qtbot.wait(500)  # the reply lands in here, and anything it raises with it
