"""Lap Analysis: corner table under the strips + theoretical-best chip.

The table renders f1coach_core.corner_table rows (mockup columns) and a row
click zooms the strips onto that corner's zone — same path as "◈ show".
"""

import pytest

from apex.analysis_view import (
    COMPARISON_HEADERS,
    SINGLE_LAP_HEADERS,
    AnalysisView,
)
from f1coach_core import (
    build_evidence_summary,
    corner_table,
    get_provider,
    load_sample_session,
    single_lap_corner_table,
)
from sample_laps import lap_without_position, slow_lap


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


def make_view(qtbot):
    session = load_sample_session()
    view = AnalysisView()
    qtbot.addWidget(view)
    view.show()
    view.set_context(slow_lap(session), session)  # slowest lap, ref -> session best
    return view, session


def test_single_lap_corner_table_is_available_when_chosen(qtbot):
    """No longer the default: opening a lap now compares it with the driver's best."""
    view, session = make_view(qtbot)
    view._ref_combo.setCurrentIndex(view._ref_combo.findData(None))
    assert view._ref_combo.currentData() is None

    rows = single_lap_corner_table(slow_lap(session))
    assert not view._corners.isHidden()
    assert view._corners.rowCount() == len(rows)
    assert view._corners.horizontalHeaderItem(5).text() == "Technique review"
    assert any("REVIEW" in view._corners.item(row, 5).text() for row in range(len(rows)))


def test_optional_reference_comparison_matches_core_and_flags_worst(qtbot):
    view, session = make_view(qtbot)
    best_index = view._ref_combo.findData(session.best_lap)
    view._ref_combo.setCurrentIndex(best_index)
    reference = view._ref_combo.currentData()

    rows = corner_table(slow_lap(session), reference)
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


def test_comparison_keeps_the_technique_review_column(qtbot):
    """Choosing a reference used to drop the column that says what to do.

    A comparison answers "where did the time go"; the technique flags answer
    "what about the corner produced it". They are properties of the driver's own
    lap, so a comparison has no reason to withhold them -- and dropping them left
    the stretched last section holding a five-character delta and a lot of nothing.
    """
    view, session = make_view(qtbot)
    view._ref_combo.setCurrentIndex(view._ref_combo.findData(session.best_lap))
    reference = view._ref_combo.currentData()
    assert reference is not None

    rows = corner_table(slow_lap(session), reference)
    review_col = len(COMPARISON_HEADERS) - 1
    assert view._corners.columnCount() == len(COMPARISON_HEADERS)
    assert view._corners.horizontalHeaderItem(review_col).text() == "Technique review"
    assert view._corners.horizontalHeaderItem(review_col - 1).text() == "Δ vs ref"

    shown = [view._corners.item(i, review_col).text() for i in range(len(rows))]
    expected = [
        "REVIEW · " + ", ".join(flags) if (flags := row["technique_flags"]) else "No flag"
        for row in rows
    ]
    assert shown == expected


def test_switching_back_to_single_lap_drops_only_the_delta_column(qtbot):
    """The two modes are one table with one column's difference between them."""
    view, session = make_view(qtbot)
    view._ref_combo.setCurrentIndex(view._ref_combo.findData(session.best_lap))
    assert view._corners.columnCount() == len(COMPARISON_HEADERS)

    view._ref_combo.setCurrentIndex(view._ref_combo.findData(None))

    assert view._corners.columnCount() == len(SINGLE_LAP_HEADERS)
    last = len(SINGLE_LAP_HEADERS) - 1
    assert view._corners.horizontalHeaderItem(last).text() == "Technique review"


def test_show_picks_the_cited_stretch_out_on_the_track(qtbot):
    """A cited stretch is a place on the circuit before it is a chart region.

    Highlighting only the strips asked a participant to find the corner from a
    distance axis. The map answers "where" directly, which is the question
    somebody who has driven the track actually has.
    """
    from apex.widgets.track_replay import span_indices
    from test_track_replay import _positioned_lap

    lap = _positioned_lap()
    view = AnalysisView()
    qtbot.addWidget(view)
    view._panel._auto = False  # no model run: this is about the drawing
    view.set_context(lap, None)
    assert view._track._span == (0, 0)  # nothing cited yet, so nothing picked out

    view._show_evidence(500.0, 950.0)

    assert view._track._span == span_indices(lap, 500.0, 950.0)
    first, last = view._track._span
    assert lap.df["dist"].iloc[first] >= 500.0
    assert lap.df["dist"].iloc[last] <= 950.0 + 1e-6


def test_a_lap_without_world_position_leaves_the_track_empty(qtbot):
    """Those laps analyse fine; they simply cannot be drawn on a circuit."""
    view = AnalysisView()
    qtbot.addWidget(view)
    view._panel._auto = False
    view.set_context(lap_without_position(), None)

    assert not view.lap.has_track_map
    assert view._track._x == []

    view._show_evidence(500.0, 950.0)  # must not invent a position it never had

    assert view._track._x == []


def test_the_sector_ribbon_says_the_sectors_are_derived(qtbot):
    """S1-S3 must not be read as the circuit's official timing sectors.

    TORCS records none, so these are equal thirds of the lap distance that Apex
    derives. Unlabelled, a reader compares them with broadcast splits that mean
    something else entirely.
    """
    view, _session = make_view(qtbot)

    tip = view._stack._ribbon.toolTip()

    assert "thirds" in tip and "derived" in tip
    assert "TORCS records no timing sectors" in tip


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

    assert "off your best lap" in view._debrief_heading.text()
    assert view._debrief_points  # still computed: it is what the coach narrates

    # The stretches are no longer listed under the sentence — the corner table
    # says the same thing for every corner — so zooming is a row click there.
    zoomed = []
    view._show_evidence = lambda d0, d1: zoomed.append((d0, d1))
    view._zoom_corner_row(0)
    assert zoomed == [tuple(view._corner_rows[0]["span_m"])]


def test_debrief_is_absent_without_a_reference_lap(qtbot):
    """Nothing to compare against means nothing to say about where time went."""
    view = AnalysisView()
    qtbot.addWidget(view)
    view.set_context(load_sample_session().best_lap, None)

    assert view._debrief_points == []
    assert not view._debrief_heading.isVisible()


def test_every_corner_in_the_table_is_reviewable(qtbot):
    """A participant picks a corner by name, so every corner has to open.

    The debrief lists only the stretches that cost time. Driving the review from
    that list meant a corner somebody wanted to look at — one they were quick
    through, or one just under the reporting threshold — could not be opened at
    all, and the way in was a button beside a summary rather than the row itself.
    """
    view, session = make_view(qtbot)

    assert view._corner_rows
    assert len(view._review_points) == len(view._corner_rows)
    # Same corners, same order: a row and its review cannot drift apart.
    assert [point.corner for point in view._review_points] == [
        row["corner"] for row in view._corner_rows
    ]


def test_double_clicking_a_corner_row_reviews_that_corner(qtbot):
    """The table is the way in; the review opens on the row that was clicked."""
    view, _session = make_view(qtbot)
    row = len(view._corner_rows) - 1  # not the first, so position cannot pass by luck

    view._review_corner(row)

    assert view._replay_window is not None
    # Every corner travels with it, so the window can move between them.
    assert [p.corner for p in view._replay_window._points] == [
        p.corner for p in view._review_points
    ]
    shown = view._replay_window._points[view._replay_window._chooser.currentRow()]
    assert shown.corner == view._corner_rows[row]["corner"]


def test_validated_report_advice_is_reused_by_the_matching_corner_review(qtbot):
    view, _session = make_view(qtbot)
    reference = view._ref_combo.currentData()
    report = get_provider("mock").generate(
        build_evidence_summary(view._lap, reference)
    )

    view._panel.reportReady.emit(report)

    assert view._advice
    for index, narrated in view._advice.items():
        point = view._review_points[index]
        finding = next(
            finding
            for finding in report.findings
            if any(
                citation.corner == point.corner and citation.span == point.span_m
                for citation in finding.evidence
            )
        )
        assert narrated.point is point
        assert narrated.advice == finding.action

    cited = min(view._advice)
    view._review_corner(cited)
    assert view._advice[cited].advice in view._replay_window._advice.text()


def test_a_new_report_clears_advice_that_it_does_not_cite(qtbot):
    """A prior run's prose must not survive as if the latest report said it."""
    from types import SimpleNamespace

    view, _session = make_view(qtbot)
    report = get_provider("mock").generate(
        build_evidence_summary(view._lap, view._ref_combo.currentData())
    )
    view._panel.reportReady.emit(report)
    assert view._advice

    view._panel.reportReady.emit(SimpleNamespace(findings=()))

    assert view._advice == {}
    assert view._advice_complete


def test_changing_analysis_context_closes_the_old_corner_review(qtbot):
    """Late advice for a new lap must never be painted into an old lap's window."""
    view, _session = make_view(qtbot)
    view._review_corner(0)
    old_review = view._replay_window
    assert old_review is not None

    view._ref_combo.setCurrentIndex(view._ref_combo.findData(None))

    assert view._replay_window is None
    assert not old_review.isVisible()


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
    # Two samples cannot make a corner, so there is nothing to review — and
    # saying that by having no rows beats an error.
    assert view._review_points == []


def test_single_lap_review_keeps_every_corner_and_claims_no_time_lost(qtbot):
    """Without a reference there is no loss — but there is still footage.

    Review used to disappear entirely in single-lap mode, because it was driven
    by the debrief and a debrief needs something to compare against. The corners
    were always there, and so was the recording of driving them.
    """
    view, _session = make_view(qtbot)
    assert view._review_points

    view._ref_combo.setCurrentIndex(view._ref_combo.findData(None))

    assert view._review_points, "single-lap review still has every corner"
    assert all(point.time_lost_s is None for point in view._review_points)

    view._review_corner(0)
    assert view._replay_window is not None
    # No reference, so nothing claims a loss that was never measured.
    assert "s lost" not in view._replay_window._headline.text()
