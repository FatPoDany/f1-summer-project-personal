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


def test_too_short_laps_read_as_cant_compare(qtbot, tmp_path):
    from f1coach_core import Session, load_telemetry_csv

    stub = (
        "t,dist,speed,throttle,brake,steer,gear,sector\n"
        "0.0,0,20,1,0,0,3,1\n1.0,20,20,1,0,0,3,2\n2.0,40,20,1,0,0,3,3\n"
    )
    for name in ("a.csv", "b.csv"):
        (tmp_path / name).write_text(stub)
    laps = tuple(load_telemetry_csv(tmp_path / name) for name in ("a.csv", "b.csv"))
    session = Session(name="tiny", path=tmp_path, laps=laps, problems=())

    view = CompareView()
    qtbot.addWidget(view)
    view.show()
    view.set_session(session)  # 40 m laps can't share a usable distance grid

    assert "Can't compare" in view._verdict.text()
    x, _y = view._delta_curve.getData()
    assert x is None or len(x) == 0
