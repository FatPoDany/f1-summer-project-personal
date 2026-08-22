"""Lap Analysis: corner table under the strips + theoretical-best chip.

The table renders f1coach_core.corner_table rows (mockup columns) and a row
click zooms the strips onto that corner's zone — same path as "◈ show".
"""

import pytest

from apex.analysis_view import AnalysisView
from f1coach_core import corner_table, load_sample_session, single_lap_corner_table


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


def make_view(qtbot):
    session = load_sample_session()
    view = AnalysisView()
    qtbot.addWidget(view)
    view.show()
    view.set_context(session.laps[2], session)  # the ragged lap, ref -> session best
    return view, session


def test_single_lap_corner_table_is_available_when_chosen(qtbot):
    """No longer the default: opening a lap now compares it with the driver's best."""
    view, session = make_view(qtbot)
    view._ref_combo.setCurrentIndex(view._ref_combo.findData(None))
    assert view._ref_combo.currentData() is None

    rows = single_lap_corner_table(session.laps[2])
    assert not view._corners.isHidden()
    assert view._corners.rowCount() == len(rows)
    assert view._corners.horizontalHeaderItem(5).text() == "Technique review"
    assert any("REVIEW" in view._corners.item(row, 5).text() for row in range(len(rows)))


def test_optional_reference_comparison_matches_core_and_flags_worst(qtbot):
    view, session = make_view(qtbot)
    best_index = view._ref_combo.findData(session.best_lap)
    view._ref_combo.setCurrentIndex(best_index)
    reference = view._ref_combo.currentData()

    rows = corner_table(session.laps[2], reference)
    assert not view._corners.isHidden()
    assert view._corners.rowCount() == len(rows)
    worst = max(range(len(rows)), key=lambda i: rows[i]["delta_s"])
    assert "⚠" in view._corners.item(worst, 0).text()
    assert view._corners.item(0, 1).text().endswith("m")  # "580 · ref 595 m"
    assert "/" in view._corners.item(0, 2).text()  # mine / ref speeds
    assert view._corners.horizontalHeaderItem(3).text() == "Throttle 50%"
    assert view._corners.item(0, 3).text().endswith("m")  # mine / ref throttle points
    assert "/" in view._corners.item(0, 4).text()  # mine / ref exit speeds
    assert view._corners.item(0, 5).text().startswith(("+", "-"))

    total = sum(session.best_sector_times.values())
    assert f"Theoretical {total:.3f} s" == view._theoretical.text()


def test_corner_row_click_zooms_and_highlights_the_zone(qtbot):
    view, _session = make_view(qtbot)
    view._zoom_corner_row(0)
    d0, d1 = view._corner_rows[0]["span_m"]
    x0, x1 = view._stack._strips[0].getViewBox().viewRange()[0]
    assert x0 == pytest.approx(d0 - 60, abs=1) and x1 == pytest.approx(d1 + 60, abs=1)
    assert view._stack._highlights  # the zone is shaded across the strips


def test_returning_to_single_lap_keeps_the_table_visible(qtbot):
    view, _session = make_view(qtbot)
    view._ref_combo.setCurrentIndex(1)
    view._ref_combo.setCurrentIndex(0)
    assert view._ref_combo.currentText() == "Single-lap analysis"
    assert not view._corners.isHidden()
    assert view._corner_rows


def test_debrief_names_where_time_went_and_zooms_to_it(qtbot):
    """The participant-facing half of the screen, independent of any model."""
    session = load_sample_session()
    best = session.best_lap
    slowest = max(session.laps, key=lambda lap: lap.lap_time)

    view = AnalysisView()
    qtbot.addWidget(view)
    view.set_context(slowest, session)
    view._ref_combo.setCurrentIndex(view._ref_combo.findData(best))

    assert view._debrief.isVisible() or view._debrief.count()
    assert "off your best lap" in view._debrief_heading.text()
    assert view._debrief.count() == len(view._debrief_points)
    assert view._debrief.count() >= 1

    # Clicking a stretch zooms the strips onto exactly that span.
    zoomed = []
    view._show_evidence = lambda d0, d1: zoomed.append((d0, d1))
    view._zoom_debrief_item(view._debrief.item(0))
    assert zoomed == [view._debrief_points[0].span_m]


def test_debrief_is_absent_without_a_reference_lap(qtbot):
    """Nothing to compare against means nothing to say about where time went."""
    view = AnalysisView()
    qtbot.addWidget(view)
    view.set_context(load_sample_session().best_lap, None)

    assert view._debrief.count() == 0
    assert not view._debrief_heading.isVisible()


def test_the_replay_has_a_button_not_just_a_hidden_double_click(qtbot):
    """The main thing a participant is here for cannot be a double-click on a row."""
    from f1coach_core import load_sample_session

    session = load_sample_session()
    view = AnalysisView()
    qtbot.addWidget(view)
    view.set_context(max(session.laps, key=lambda lap: lap.lap_time), session)

    assert view._replay_button.isVisibleTo(view)
    assert "corner" in view._replay_button.text().lower()


def test_the_replay_button_opens_the_worst_stretch_when_none_is_selected(qtbot):
    from f1coach_core import load_sample_session

    session = load_sample_session()
    view = AnalysisView()
    qtbot.addWidget(view)
    view.set_context(max(session.laps, key=lambda lap: lap.lap_time), session)
    assert view._debrief_points  # the sample session has findings to replay

    view._replay_button.click()

    # Ranked worst-first, so an unselected list replays the costliest stretch.
    assert view._debrief.currentRow() == 0
    assert view._replay_window is not None


def test_opening_a_lap_compares_it_with_the_drivers_best_by_default(qtbot):
    """Defaulting to no reference showed a participant nothing at all.

    The debrief, the corner losses, the replay and anything the coach can say
    all need a lap to compare against. Opening a lap and finding an empty screen,
    with a dropdown as the only way out, is not something anybody discovers.
    """
    from f1coach_core import load_sample_session

    session = load_sample_session()
    slowest = max(session.laps, key=lambda lap: lap.lap_time)
    best = min((lap for lap in session.laps if lap is not slowest), key=lambda lap: lap.lap_time)

    view = AnalysisView()
    qtbot.addWidget(view)
    view.set_context(slowest, session)

    assert view._ref_combo.currentData() is best
    assert view._debrief_points  # so there is something to coach and to replay


def test_a_lap_with_nothing_to_compare_against_still_opens(qtbot, tmp_path):
    """A single loose CSV has no session best, and must not become an error."""
    from f1coach_core import load_telemetry_csv

    csv = tmp_path / "loose.csv"
    csv.write_text("t,speed,throttle,brake,steer,gear\n0.0,10,1,0,0,3\n1.0,20,1,0,0,3\n")
    view = AnalysisView()
    qtbot.addWidget(view)
    view.set_context(load_telemetry_csv(csv), None)

    assert view._ref_combo.currentData() is None
    assert not view._replay_button.isVisibleTo(view)


def test_the_review_button_retracts_when_there_is_nothing_to_review(qtbot):
    """It used to linger from the previous lap and do nothing when pressed."""
    from f1coach_core import load_sample_session

    session = load_sample_session()
    view = AnalysisView()
    qtbot.addWidget(view)
    view.set_context(max(session.laps, key=lambda lap: lap.lap_time), session)
    assert view._replay_button.isVisibleTo(view)

    # Single-lap: no reference, so there is no stretch to review.
    view._ref_combo.setCurrentIndex(view._ref_combo.findData(None))

    assert not view._replay_button.isVisibleTo(view)
