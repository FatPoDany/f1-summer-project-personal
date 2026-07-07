"""Compare view: delta trace between any two session laps."""

import pytest

from apex.compare_view import CompareView
from f1coach_core import load_sample_session


def test_defaults_and_switching(qtbot):
    session = load_sample_session()
    view = CompareView()
    qtbot.addWidget(view)
    view.show()  # clip-to-view curves only materialise once painted
    view.set_session(session)

    # defaults: first non-best lap vs the session best
    assert view._combo_a.currentData().source.stem == "lap_01"
    assert view._combo_b.currentData().source.stem == "lap_02"
    _x, y = view._delta_curve.getData()
    assert y[-1] == pytest.approx(1.660, abs=0.02)
    assert "1.660 s behind" in view._verdict.text()

    view._combo_a.setCurrentIndex(2)  # lap_03
    _x, y = view._delta_curve.getData()
    assert y[-1] == pytest.approx(5.32, abs=0.02)
    assert "5.32" in view._verdict.text()


def test_ahead_wording(qtbot):
    session = load_sample_session()
    view = CompareView()
    qtbot.addWidget(view)
    view.show()
    view.set_session(session, lap_a=session.best_lap)
    # A is the best lap, so B defaults to the next-best and A runs ahead
    assert view._combo_b.currentData().source.stem == "lap_01"
    assert "ahead" in view._verdict.text()
