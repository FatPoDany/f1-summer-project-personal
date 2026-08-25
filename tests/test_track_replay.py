"""Replaying a stretch: real elapsed time, exact span, and honest degradation."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PySide6.QtCore import QElapsedTimer

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
    """One frame of playback must cover one frame of lap time, not one sample.

    The playhead is kept in the lap's own seconds. Reading it back off the
    sample the marker landed on rounds up, because landing takes the first
    sample at or after the step, and rounding up once per frame is a ratchet
    rather than a rounding: at the 50 Hz these laps are recorded at, every 33 ms
    became 40 and the replay ran a fifth faster than the drive it was replaying.
    """
    lap = _positioned_lap()
    view = TrackReplay()
    qtbot.addWidget(view)
    view.set_stretch(lap, 500.0, 950.0, "T1")

    t = lap.df["t"]
    before = float(t.iloc[view._slider.value()])
    for _ in range(30):
        view._step(0.033)
    after = float(t.iloc[view._slider.value()])

    assert after > before
    # One sample of slack: the marker sits on samples, the playhead does not.
    assert after - before == pytest.approx(30 * 0.033, abs=0.08)


def test_the_marker_runs_at_real_time_rather_than_at_a_count_of_frames(qtbot):
    """Playback claims to run at the speed it happened, so measure it doing so.

    Qt fires a 33 ms timer when it can get to one rather than when it says: on
    Windows every 47 ms, the granularity of the system clock. Stepping a nominal
    frame per tick therefore ran the marker at 0.84x the drive it was replaying
    while the footage beside it ran off the media clock at real time -- so the
    picture supposed to be showing the moment under the marker ended the corner
    a second away from it, and the drift check dragged it back twice a second
    for the whole corner. The stutter that caused is what this test is for.
    """
    lap = _positioned_lap()
    view = TrackReplay()
    qtbot.addWidget(view)
    view.set_stretch(lap, 300.0, 1100.0, "T1")  # long enough not to go round again
    t = lap.df["t"]
    start = float(t.iloc[view._slider.value()])
    clock = QElapsedTimer()
    seen: list[tuple[float, float]] = []
    # Read at each frame rather than at the end: the marker is only ever as
    # current as its last frame, and on a loaded machine that can be a while ago.
    view.cursorMoved.connect(
        lambda _d: seen.append(
            (clock.elapsed() / 1000.0, float(t.iloc[view._slider.value()]) - start)
        )
    )

    clock.start()
    view._play.setChecked(True)
    qtbot.wait(600)
    view._play.setChecked(False)

    assert seen, "playback never moved the marker"
    real, covered = seen[-1]
    assert real > 0.2  # the timer did run
    assert covered == pytest.approx(real, abs=0.08)  # one sample of slack


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

    t = lap.df["t"]
    view._step(float(t.iloc[view._last]) - float(t.iloc[view._first]))

    assert view._slider.value() == view._last  # the stretch's last metres are shown
    assert view.playing

    view._step(0.033)

    assert view._slider.value() == view._first


def test_the_marker_carries_straight_on_after_it_has_gone_round(qtbot):
    """Going round again costs nothing: the next pass starts at once.

    Driven by the timer rather than by `_step`, because the fault was in the
    bookkeeping between the two. Going round starts the clock afresh, and the
    frame that had asked for the step wrote its own reading back on top of that
    anchor, so the frame after it measured a whole stretch of negative time.
    The marker then sat on the first sample -- one instant, one set of numbers,
    a progress bar parked at the left -- for as long as the corner had just
    taken, while the two pictures beside it played on to the end of their clips
    and went black.
    """
    lap = _positioned_lap()
    view = TrackReplay()
    qtbot.addWidget(view)
    view.set_stretch(lap, 500.0, 560.0, "T1")  # a couple of seconds, so it goes round

    view._play.setChecked(True)
    qtbot.waitUntil(lambda: view._slider.value() == view._last, timeout=5000)
    qtbot.waitUntil(lambda: view._slider.value() == view._first, timeout=5000)
    qtbot.wait(400)  # a fifth of the stretch, and several frames whatever the load

    assert view.playing
    assert view._slider.value() > view._first
    assert view.playing


def test_playback_asks_for_a_drift_check_a_few_times_a_second(qtbot):
    """The marker cannot see the pictures, so it asks whoever owns them to look.

    Not on every frame: seeking a media player thirty times a second stutters it
    for no gain, which is why the footage is anchored and then left to run in the
    first place. Asking a few times a second is what catches a picture that has
    wandered off the marker in the middle of a corner.
    """
    from apex.widgets.track_replay import RESYNC_S

    lap = _positioned_lap()
    view = TrackReplay()
    qtbot.addWidget(view)
    view.set_stretch(lap, 0.0, 1000.0, "T1")
    checks = []
    view.driftCheckDue.connect(lambda: checks.append(view._slider.value()))
    view._play.setChecked(True)

    frame = RESYNC_S / 10  # ten frames to a check, near enough
    for _ in range(9):
        view._step(frame)
    assert checks == []  # not once a frame: a seek a frame is the stutter itself

    view._step(frame * 2)
    assert len(checks) == 1
    assert checks[0] > view._first  # the marker had moved on before anyone looked


def test_the_marker_carries_on_from_where_a_key_put_it_mid_playback(qtbot):
    """Anything that moves the marker but playback restarts playback's clock.

    Playback keeps its own playhead in lap seconds so that landing on samples
    cannot ratchet it. That playhead has to be told when the marker is put
    somewhere else, and not every way of doing that is a drag: an arrow key or a
    click on the slider's groove moves it without one. A playhead that had not
    heard about it pulled the marker straight back on the next frame.
    """
    lap = _positioned_lap()
    view = TrackReplay()
    qtbot.addWidget(view)
    view.set_stretch(lap, 300.0, 1100.0, "T1")
    view._play.setChecked(True)

    put = view._first + 120
    view._slider.setValue(put)  # as an arrow key or a click on the groove does
    view._step(0.05)

    assert view._slider.value() >= put  # carried on from there, not snapped back


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
