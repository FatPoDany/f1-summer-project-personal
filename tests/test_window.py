"""Shell smoke tests — run offscreen on Mac/Linux CI (QT_QPA_PLATFORM=offscreen)."""

import pytest

from apex.main_window import MainWindow
from f1coach_core import ensure_sample_session, load_sample_session

CANONICAL = (
    "t,speed,throttle,brake,steer,gear\n"
    "0.0,10.0,1,0,0,3\n1.0,20.0,1,0,0,3\n2.0,20.0,0.5,0,0,4\n"
)


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


def test_window_starts_in_the_garage(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert window._stacked.currentWidget() is window._garage
    assert not window._analysis_action.isEnabled()


def test_garage_lists_sample_session_and_opens_laps(qtbot):
    ensure_sample_session()
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    garage = window._garage
    assert garage.session is not None and len(garage.session.laps) == 3
    assert garage._table.rowCount() == 3
    assert garage._table.item(1, 3).text() == "SESSION BEST"  # lap_02

    with qtbot.waitSignal(garage.lapOpened):
        garage._open_row(1)
    assert window._stacked.currentWidget() is window._analysis
    assert window._analysis.lap is garage.session.laps[1]


def test_analysis_defaults_reference_to_next_best_for_the_best_lap(qtbot):
    session = load_sample_session()
    best = session.best_lap
    window = MainWindow()
    qtbot.addWidget(window)
    window.show_analysis(best, session)

    view = window._analysis
    assert view._ref_combo.count() == 3  # "No reference" + two other laps
    reference = view._ref_combo.currentData()
    assert reference is not None and reference.source.stem == "lap_01"  # next-best

    stack = view._stack
    values = stack.values_at(2000.0)
    assert values is not None
    assert values["speed_kmh"] > 0
    assert {"dist", "speed_kmh", "throttle_pct", "brake_pct", "gear"} <= set(values)
    x_ref, _ = stack._ref_curves[0].getData()
    assert x_ref is not None and len(x_ref) > 100  # reference overlay drawn


def test_open_path_shows_lap_without_session(qtbot, tmp_path):
    csv = tmp_path / "two_points.csv"
    csv.write_text(CANONICAL)
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_path(csv)

    assert window._stacked.currentWidget() is window._analysis
    assert window._analysis._ref_combo.count() == 1  # only "No reference"
    assert "dist derived" in window.statusBar().currentMessage()


def test_open_path_imports_torcs_runs_into_garage(qtbot, tmp_path):
    from test_torcs import make_run

    run = make_run(tmp_path / "quali.csv")
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_path(run)

    assert window._stacked.currentWidget() is window._garage
    session = window._garage.session
    assert session is not None and session.name == "quali"
    assert len(session.laps) == 3
    assert "3 laps" in window.statusBar().currentMessage()


def test_report_export_failure_is_a_dialog_not_a_crash(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    window = MainWindow()
    qtbot.addWidget(window)
    window.show_analysis(load_sample_session().best_lap, None)

    target = tmp_path / "no-such-dir" / "report.html"  # missing parent -> OSError
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )
    dialogs: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox, "critical", staticmethod(lambda *a: dialogs.append(a))
    )

    window._export_report()

    assert dialogs and dialogs[0][1] == "Can't export report"
    assert "report" not in window.statusBar().currentMessage().lower()


def test_crash_hook_shows_a_dialog_and_never_raises(qtbot, monkeypatch):
    import sys
    from unittest.mock import MagicMock

    from apex import app as app_module

    box = MagicMock()
    monkeypatch.setattr(app_module, "QMessageBox", MagicMock(return_value=box))
    try:
        raise RuntimeError("boom in a slot")
    except RuntimeError:
        app_module.show_crash_dialog(*sys.exc_info())

    assert "boom in a slot" in box.setInformativeText.call_args[0][0]
    assert "RuntimeError" in box.setDetailedText.call_args[0][0]
    box.exec.assert_called_once()
