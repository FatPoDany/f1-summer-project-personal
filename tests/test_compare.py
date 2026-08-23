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
    assert view._combo_a.currentData().source.stem == "0822-coached-lap01"
    assert view._combo_b.currentData().source.stem == "0822-coached-lap03"
    _x, y = view._delta_curve.getData()
    # The delta grid stops a few metres shy of the flag, so it lands just under
    # the lap-time difference rather than exactly on it.
    behind = session.laps[0].lap_time - session.best_lap.lap_time
    assert y[-1] == pytest.approx(behind, abs=0.05)
    assert f"{y[-1]:.3f} s behind" in view._verdict.text()

    view._combo_a.setCurrentIndex(3)  # 0822-coached-lap04
    _x, y = view._delta_curve.getData()
    assert y[-1] == pytest.approx(
        session.laps[3].lap_time - session.best_lap.lap_time, abs=0.05
    )
    assert f"{y[-1]:.3f} s behind" in view._verdict.text()


def test_ahead_wording(qtbot):
    session = load_sample_session()
    view = CompareView()
    qtbot.addWidget(view)
    view.show()
    view.set_session(session, lap_a=session.best_lap)
    # A is the best lap, so B defaults to the next-best and A runs ahead
    assert view._combo_b.currentData().source.stem == "0822-coached-lap02"
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
    assert view._changed.text() == "" and view._remaining.text() == ""


def test_narrative_lines_derive_from_the_corner_table(qtbot):
    session = load_sample_session()
    view = CompareView()
    qtbot.addWidget(view)
    view.show()
    view.set_session(session)  # A = the slowest lap, B = the session best

    # B is the best lap: its biggest corner gain is narrated with numbers…
    changed = view._changed.text()
    assert "What changed" in changed and "km/h" in changed
    assert session.best_lap.source.stem in changed
    # …and nothing is left on the table against a slower lap
    assert "Still on the table" in view._remaining.text()

    view._combo_b.setCurrentIndex(0)  # compare the slowest lap with itself
    assert view._changed.text() == "" and view._remaining.text() == ""
