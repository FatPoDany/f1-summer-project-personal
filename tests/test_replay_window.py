

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
        category="braking",
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


def test_the_review_window_carries_the_whole_laps_stretches(qtbot):
    """Reviewing one corner usually means wanting the next one too."""
    from apex.widgets.replay_window import ReplayWindow
    from f1coach_core.debrief import DebriefPoint

    points = [
        _point(),
        DebriefPoint(corner="T7", apex_m=700.0, span_m=(650.0, 780.0),
                     time_lost_s=0.2, difference="", detail=""),
    ]
    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(_positioned_lap(20.0), points[0], points=points)

    assert window._chooser.count() == 2
    assert window._chooser.isEnabled()
    assert window._chooser.currentRow() == 0


def test_changing_stretch_reloads_without_leaving_the_window(qtbot):
    from apex.widgets.replay_window import ReplayWindow
    from f1coach_core.debrief import DebriefPoint

    points = [
        _point(),
        DebriefPoint(corner="T7", apex_m=700.0, span_m=(650.0, 780.0),
                     time_lost_s=0.2, difference="", detail=""),
    ]
    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(_positioned_lap(20.0), points[0], points=points)

    with qtbot.waitSignal(window.stretchChanged):
        window._chooser.setCurrentRow(1)
    assert "T7" in window._replay._title.text()


def test_a_single_moment_still_lists_itself(qtbot):
    """How many things there are to work on is part of the answer."""
    from apex.widgets.replay_window import ReplayWindow

    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(_positioned_lap(20.0), _point())

    assert window._chooser.count() == 1
    assert "T3" in window._chooser.item(0).text()


def test_the_coachs_note_follows_the_stretch_it_belongs_to(qtbot):
    from apex.widgets.replay_window import ReplayWindow
    from f1coach_core.debrief import DebriefPoint

    points = [
        _point(),
        DebriefPoint(corner="T7", apex_m=700.0, span_m=(650.0, 780.0),
                     time_lost_s=0.2, difference="", detail=""),
    ]
    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(
        _positioned_lap(20.0), points[0], points=points,
        notes={0: "Brake later here.", 1: "You lost momentum along this stretch."},
    )
    assert "Brake later here." in window._replay._note.text()

    window._chooser.setCurrentRow(1)
    assert "lost momentum" in window._replay._note.text()


def _narrated(observation="", advice=""):
    from racecoach.granite.narrate import NarratedPoint

    return NarratedPoint(point=_point(), narration=observation, advice=advice)


def test_a_moment_is_labelled_by_the_kind_of_driving_it_is_about(qtbot):
    """"Turn 3, braking" is something to work on; "stretch 1" is not."""
    from apex.widgets.replay_window import ReplayWindow

    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(_positioned_lap(20.0), _point())

    assert "braking" in window._chooser.item(0).text()
    assert "braking" in window._headline.text()


def test_the_instruction_is_shown_as_something_to_do_next_lap(qtbot):
    from apex.widgets.replay_window import ReplayWindow

    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(
        _positioned_lap(20.0),
        _point(),
        advice={0: _narrated("You braked 12 m earlier.", "Hold on to 130 m first.")},
    )

    assert "You braked 12 m earlier." in window._observation.text()
    assert window._advice.text().startswith("Next lap:")
    assert "130 m" in window._advice.text()


def test_a_moment_nothing_explains_says_so_instead_of_inventing_advice(qtbot):
    """The loss is real; the remedy was never measured."""
    from apex.widgets.replay_window import ReplayWindow
    from f1coach_core.debrief import DebriefPoint

    unexplained = DebriefPoint(
        corner="T5", apex_m=500.0, span_m=(450.0, 560.0), time_lost_s=0.3,
        difference="", detail="", category="",
    )
    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(_positioned_lap(20.0), unexplained)

    assert "no advice" in window._advice.text()
    assert "unexplained" in window._headline.text()


def test_a_measured_moment_without_words_yet_says_they_are_coming(qtbot):
    """The analysis starts by itself, so this is a wait, not an instruction."""
    from apex.widgets.replay_window import ReplayWindow

    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(_positioned_lap(20.0), _point())  # no advice supplied

    text = window._advice.text()
    assert "still being written" in text
    assert "Analyze lap" not in text  # there is no button to press any more


def test_the_measurement_stays_on_screen_beneath_the_words(qtbot):
    """The prose is a convenience; the numbers are the finding."""
    from apex.widgets.replay_window import ReplayWindow

    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(_positioned_lap(20.0), _point())

    assert "118 m" in window._measured.text()
