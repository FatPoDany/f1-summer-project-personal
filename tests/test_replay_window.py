

def _positioned_lap(seconds, offset=0.0, driver="A001", wall_clock=False,
                    source="lap.csv"):
    """A lap with world position, shifted sideways so two lines differ visibly.

    ``wall_clock`` adds the channel a real capture stamps on every sample, which
    is what lines the marker up with the footage.
    """
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
            **(
                {"wall_clock_s": 1_700_000_000.0 + np.linspace(0.0, seconds, n)}
                if wall_clock
                else {}
            ),
        }),
        Path(source),
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

    assert window._replay._map._ref_x  # the compared lap's path is on the map
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


def test_review_states_that_video_is_not_ai_evidence(qtbot):
    from apex.widgets.replay_window import ReplayWindow

    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(_positioned_lap(20.0), _point())

    disclosure = window._ai_evidence_note.text().lower()
    assert "telemetry" in disclosure
    assert "not" in disclosure and "video" in disclosure


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


def test_finished_analysis_says_when_no_validated_advice_matches_the_stretch(qtbot):
    """A completed run must not leave the review looking permanently busy."""
    from apex.widgets.replay_window import ReplayWindow

    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(
        _positioned_lap(20.0),
        _point(),
        advice_complete=True,
    )

    text = window._advice.text().lower()
    assert "analysis is complete" in text
    assert "no validated ai advice" in text
    assert "still being written" not in text


def test_the_measurement_stays_on_screen_beneath_the_words(qtbot):
    """The prose is a convenience; the numbers are the finding."""
    from apex.widgets.replay_window import ReplayWindow

    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(_positioned_lap(20.0), _point())

    assert "118 m" in window._measured.text()


def test_one_play_button_drives_both_halves_of_the_corner(qtbot):
    """The picture and the marker are two views of one moment, so one transport.

    They used to be two players: the clip started itself as soon as ffmpeg was
    done and looped for ever, while the marker waited on its own Play button and
    stopped at the end. Whatever the participant did, the two showed different
    parts of the same corner.
    """
    from apex.widgets.replay_window import ReplayWindow

    lap = _positioned_lap(20.0, wall_clock=True)
    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(lap, _point())
    moves = []
    window._footage.resume = lambda: moves.append("resume")
    window._footage.pause = lambda: moves.append("pause")
    window._footage.seek_to = lambda w: moves.append(("seek", round(w, 3)))

    window._replay._play.setChecked(True)
    assert window._replay.playing
    # Anchored before it runs, so the picture starts on the marker's moment.
    assert moves[-2:] == [("seek", round(window._replay.current_wall_clock, 3)), "resume"]

    window._replay._play.setChecked(False)
    assert not window._replay.playing
    assert moves[-1] == "pause"


def test_dragging_the_slider_moves_the_footage_with_it(qtbot):
    """Scrubbing the marker has to scrub the picture, not just the track."""
    from apex.widgets.replay_window import ReplayWindow

    lap = _positioned_lap(20.0, wall_clock=True)
    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(lap, _point())
    seeks = []
    window._footage.seek_to = seeks.append

    target = (window._replay._first + window._replay._last) // 2
    window._replay._slider.setValue(target)
    window._replay._slider.sliderMoved.emit(target)

    assert seeks == [window._replay.wall_clock_at(target)]


def test_going_round_again_takes_the_footage_back_with_it(qtbot):
    """The clip outlasts the corner, so looping the marker alone would drift.

    Playback repeats until it is paused; on each round the picture has to be put
    back to the corner's start, or the second time through shows the marker at
    the turn-in and the footage already down the following straight.
    """
    from apex.widgets.replay_window import ReplayWindow

    lap = _positioned_lap(20.0, wall_clock=True)
    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(lap, _point())
    seeks = []
    window._footage.seek_to = seeks.append
    window._footage.resume = lambda: None

    window._replay._play.setChecked(True)
    window._replay._slider.setValue(window._replay._last)
    seeks.clear()  # the anchor taken when playback started
    window._replay._advance()  # the round ends here

    assert window._replay._slider.value() == window._replay._first
    assert window._replay.playing
    assert seeks == [window._replay.wall_clock_at(window._replay._first)]


def test_a_lap_without_the_clock_still_plays_but_anchors_nothing(qtbot):
    """It cannot be lined up with footage, and must not pretend to be."""
    from apex.widgets.replay_window import ReplayWindow

    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(_positioned_lap(20.0), _point())  # no wall clock
    moves = []
    window._footage.resume = lambda: moves.append("resume")
    window._footage.seek_to = lambda w: moves.append(("seek", w))

    window._replay._play.setChecked(True)

    assert window._replay.playing  # the marker still runs
    assert moves == ["resume"]  # but nothing claims to know where the picture is


def _recording(tmp_path, started_at=1_699_999_990.0):
    from racecoach.telemetry.screen_capture import Recording

    return Recording(path=tmp_path / "session.mp4", started_at=started_at, duration_s=600.0)


def _cut_into(monkeypatch, tmp_path):
    """Make clip cutting instant, and hand back what was written where."""
    from apex.widgets import footage_pane

    def cut(_recording, _from, _to, destination):
        from pathlib import Path as _Path

        _Path(destination).write_bytes(b"clip")
        return _Path(destination)

    monkeypatch.setattr(footage_pane, "cut_clip", cut)


def test_play_waits_until_the_footage_of_the_corner_is_cut(qtbot, tmp_path, monkeypatch):
    """Otherwise the marker runs against a picture that is not loaded yet.

    The clip arrives paused at its own first frame while the marker is already
    at the apex, so the corner looks like it ended early -- and the two halves
    are out of step for the whole round.

    Only a build with a player ever waits. Without one, ``show_stretch`` says so
    and returns before anything is cut, so Play is offered at once and there is
    no wait to assert -- which is the right behaviour, not a bug this should
    fail over.
    """
    import pytest

    from apex.widgets import footage_pane
    from apex.widgets.replay_window import ReplayWindow

    if footage_pane.video_support() is None:
        pytest.skip("this build has no Qt multimedia, so nothing is ever cut")

    _cut_into(monkeypatch, tmp_path)
    window = ReplayWindow()
    qtbot.addWidget(window)
    played = []
    window._footage._play = played.append

    window.show_stretch(
        _positioned_lap(20.0, wall_clock=True),
        _point(),
        recording=_recording(tmp_path),
        clips_dir=tmp_path,
    )

    assert not window._replay._play.isEnabled()  # a clip is being cut
    assert "still being cut" in window._replay._play.toolTip()

    qtbot.waitUntil(lambda: len(played) == 1, timeout=5000)

    assert window._replay._play.isEnabled()
    assert window._replay._play.toolTip() == ""


def test_a_comparison_shows_the_other_lap_beside_this_one(qtbot, tmp_path, monkeypatch):
    """One picture shows a corner going badly; the pair shows what to do instead.

    The column is put there by the two laps and their two recordings, not by
    the player that fills it, so this holds in a build that cannot show video
    and is checked separately from what gets played into it.
    """
    from apex.widgets.replay_window import ReplayWindow

    _cut_into(monkeypatch, tmp_path)
    window = ReplayWindow()
    qtbot.addWidget(window)

    window.show_stretch(
        _positioned_lap(20.0, wall_clock=True),
        _point(),
        reference=_positioned_lap(18.0, offset=8.0, wall_clock=True),
        recording=_recording(tmp_path),
        reference_recording=_recording(tmp_path),
        clips_dir=tmp_path,
    )

    assert window._ref_showing
    assert window._ref_column.isVisibleTo(window)


def test_each_lap_of_a_comparison_is_played_its_own_clip(qtbot, tmp_path, monkeypatch):
    """Two laps, two stretches of recording, two files.

    One lap's corner handed to the other is the failure nobody catches by
    looking: both pictures move, both show a car in a corner, and only the
    numbers underneath ever disagree.

    Needs a build with a player: without one nothing is cut and nothing is
    played, so there are no two clips to keep apart.
    """
    import pytest

    from apex.widgets import footage_pane
    from apex.widgets.replay_window import ReplayWindow

    if footage_pane.video_support() is None:
        pytest.skip("this build has no Qt multimedia, so nothing is ever cut")

    _cut_into(monkeypatch, tmp_path)
    window = ReplayWindow()
    qtbot.addWidget(window)
    mine, theirs = [], []
    window._footage._play = mine.append
    window._ref_footage._play = theirs.append

    window.show_stretch(
        _positioned_lap(20.0, wall_clock=True),
        _point(),
        reference=_positioned_lap(18.0, offset=8.0, wall_clock=True),
        recording=_recording(tmp_path),
        reference_recording=_recording(tmp_path),
        clips_dir=tmp_path,
    )

    qtbot.waitUntil(lambda: len(mine) == 1 and len(theirs) == 1, timeout=5000)
    assert mine != theirs


def test_a_single_lap_review_keeps_the_second_picture_out_of_the_way(qtbot, tmp_path):
    """There is no other lap, so there is nothing honest to put beside it."""
    from apex.widgets.replay_window import ReplayWindow

    window = ReplayWindow()
    qtbot.addWidget(window)

    window.show_stretch(_positioned_lap(20.0, wall_clock=True), _point())

    assert not window._ref_showing
    assert not window._ref_column.isVisibleTo(window)


def test_the_compared_lap_is_shown_at_the_same_place_not_the_same_moment(qtbot):
    """The two drove the corner at different times; it is the place that matches.

    Anchoring the second picture by elapsed time would put it at a point on
    track the marker is not at, which is the one thing two pictures side by side
    must never do.
    """
    from apex.widgets.replay_window import ReplayWindow

    lap = _positioned_lap(20.0, wall_clock=True)
    reference = _positioned_lap(18.0, offset=8.0, wall_clock=True)
    window = ReplayWindow()
    qtbot.addWidget(window)
    window.show_stretch(lap, _point(), reference=reference)
    window._ref_showing = True  # as a comparison with footage on both sides
    seeks = []
    for pane in (window._footage, window._ref_footage):
        pane.resume = lambda: None
    window._footage.seek_to = lambda w: seeks.append(("mine", w))
    window._ref_footage.seek_to = lambda w: seeks.append(("theirs", w))

    window._replay._play.setChecked(True)

    marker = window._replay._slider.value()
    distance = float(lap.df["dist"].iloc[marker])
    matched = int(reference.df["dist"].searchsorted(distance, side="left"))
    assert seeks == [
        ("mine", float(lap.df["wall_clock_s"].iloc[marker])),
        ("theirs", float(reference.df["wall_clock_s"].iloc[matched])),
    ]


def test_the_compared_lap_runs_at_the_speed_that_keeps_it_alongside(qtbot, tmp_path, monkeypatch):
    """Real time would leave the second picture a corner behind the first.

    The two drivers took different times through the same metres -- 6.6 s and
    14.7 s at Turn 7 of the baseline session -- and the track map has always
    moved the second marker by distance to match. The picture follows the dot.
    """
    import pytest

    from apex.widgets.replay_window import ReplayWindow, _matched_rate
    from f1coach_core.footage import Window

    assert _matched_rate(Window(0.0, 6.6), Window(0.0, 14.7)) == pytest.approx(2.227, abs=1e-3)
    assert _matched_rate(Window(0.0, 14.7), Window(0.0, 6.6)) == pytest.approx(0.449, abs=1e-3)
    assert _matched_rate(Window(0.0, 6.6), None) == 1.0  # nothing to match
    assert _matched_rate(Window(0.0, 0.0), Window(0.0, 6.6)) == 1.0  # and no dividing by it
    assert _matched_rate(Window(0.0, 1.0), Window(0.0, 90.0)) == 4.0  # clamped, not stuttering

    _cut_into(monkeypatch, tmp_path)
    window = ReplayWindow()
    qtbot.addWidget(window)
    rates = []
    window._ref_footage.set_rate = rates.append
    window._footage._play = lambda _path: None
    window._ref_footage._play = lambda _path: None

    window.show_stretch(
        _positioned_lap(20.0, wall_clock=True),
        _point(),
        reference=_positioned_lap(10.0, offset=8.0, wall_clock=True),  # half the time
        recording=_recording(tmp_path),
        reference_recording=_recording(tmp_path),
        clips_dir=tmp_path,
    )

    assert rates and rates[-1] == pytest.approx(0.5, abs=0.02)


def test_the_two_pictures_are_captioned_with_the_laps_they_are_of(qtbot):
    """"this lap" and "the lap you are comparing with" name roles, not laps.

    Which two laps a review was of had to be carried over in the driver's head
    from the picker on the analysis screen, and a window left open beside
    another one said nothing at all about which comparison it was showing.
    """
    from apex.widgets.replay_window import ReplayWindow

    lap = _positioned_lap(20.0, source="lap07.csv")
    reference = _positioned_lap(18.0, offset=8.0, source="lap02.csv")
    window = ReplayWindow()
    qtbot.addWidget(window)

    window.show_stretch(lap, _point(), reference=reference)

    assert "lap07" in window._footage_caption.text()
    assert "lap02" in window._ref_caption.text()
    assert "best lap" not in window._ref_caption.text()
    # And the same two laps on the window itself, in the same words.
    assert "lap07" in window.windowTitle() and "lap02" in window.windowTitle()


def test_a_single_lap_review_names_the_one_lap_it_has(qtbot):
    """Nothing to compare with, so nothing may be named as the comparison."""
    from apex.widgets.replay_window import ReplayWindow

    window = ReplayWindow()
    qtbot.addWidget(window)

    window.show_stretch(_positioned_lap(20.0, source="lap07.csv"), _point())

    assert "lap07" in window._footage_caption.text()
    assert "lap07" in window.windowTitle()
    assert "vs" not in window.windowTitle()
