

def _positioned_lap(seconds, offset=0.0, driver="A001"):
    """A lap with world position, shifted sideways so two lines differ visibly."""
    from pathlib import Path

    import numpy as np
    import pandas as pd

    from f1coach_core.lap import Lap

    n = 200
    dist = np.linspace(0.0, 900.0, n)
    angle = np.linspace(0, 2 * np.pi, n)
    return Lap(
        pd.DataFrame({
            "t": np.linspace(0.0, seconds, n),
            "dist": dist,
            "speed": np.full(n, 40.0) + offset,
            "throttle": np.ones(n),
            "brake": np.zeros(n),
            "steer": np.zeros(n),
            "gear": np.full(n, 4),
            "x": 100 * np.cos(angle) + offset,
            "y": 100 * np.sin(angle),
        }),
        Path("lap.csv"),
        schema_version=1,
        dist_derived=False,
    )


def _point():
    from f1coach_core.debrief import DebriefPoint

    return DebriefPoint(
        corner="T3", apex_m=400.0, span_m=(300.0, 500.0), time_lost_s=0.4,
        difference="braked 12 m earlier", detail="you 118 m, best 130 m",
    )


def test_the_reference_line_is_drawn_so_the_difference_is_visible(qtbot):
    """"You ran wide" is an argument; a line that visibly goes elsewhere is not."""
    from apex.widgets.replay_window import ReplayWindow

    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(
        _positioned_lap(20.0), _point(), reference=_positioned_lap(18.0, offset=8.0)
    )

    assert window._replay._map._ref_x  # the best lap's path is on the map
    assert window._replay._legend.isVisibleTo(window)


def test_the_reference_marker_tracks_distance_not_time(qtbot):
    """Both dots must sit at the same point on track, or the gap means nothing."""
    from apex.widgets.replay_window import ReplayWindow

    lap = _positioned_lap(30.0)
    reference = _positioned_lap(15.0)  # same route, driven twice as fast
    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(lap, _point(), reference=reference)

    replay = window._replay
    replay._slider.setValue(replay._first + 40)
    here = float(lap.df["dist"].iloc[replay._slider.value()])
    there = float(reference.df["dist"].iloc[replay._map._ref_cursor])

    assert abs(here - there) < 10.0  # matched by track position, not elapsed time


def test_a_lap_with_no_reference_still_replays(qtbot):
    from apex.widgets.replay_window import ReplayWindow

    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(_positioned_lap(20.0), _point())

    assert not window._replay._map._ref_x
    assert not window._replay._legend.isVisibleTo(window)


def test_the_coachs_words_appear_beside_the_stretch_they_describe(qtbot):
    from apex.widgets.replay_window import ReplayWindow

    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(
        _positioned_lap(20.0), _point(), note="You braked earlier here than usual."
    )

    assert "braked earlier here" in window._replay._note.text()
