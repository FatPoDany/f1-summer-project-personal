"""Shell smoke tests — run offscreen on Mac/Linux CI (QT_QPA_PLATFORM=offscreen)."""

import json
from pathlib import Path

import pytest
from PySide6.QtGui import QAction

from apex import main_window
from apex.coaching_queue import GarageCoachingQueue
from apex.main_window import RESEARCH_SETTING, MainWindow, _research_mode_enabled
from f1coach_core import (
    CompositeReference,
    ensure_sample_session,
    load_sample_session,
    reference_name,
)
from racecoach.granite.host import Capability

CANONICAL = (
    "t,speed,throttle,brake,steer,gear\n"
    "0.0,10.0,1,0,0,3\n1.0,20.0,1,0,0,3\n2.0,20.0,0.5,0,0,4\n"
)


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


def test_stored_settings_never_reach_the_developers_own_apex(tmp_path, monkeypatch):
    """The suite must not read, or write, the real app's settings.

    Research tools are stored rather than passed in an environment variable, so
    that a researcher can turn them on in a desktop app they double clicked. On
    Windows that store is the registry, and a developer who had turned them on
    once made the "off by default" assertions fail for reasons that had nothing
    to do with the code under test.
    """
    monkeypatch.delenv("APEX_RESEARCH_MODE", raising=False)  # the stored value decides
    settings = main_window.QSettings("Apex", "Apex")
    settings.setValue(RESEARCH_SETTING, True)
    settings.sync()

    assert Path(settings.fileName()).is_relative_to(tmp_path)
    assert _research_mode_enabled() is True  # redirected, but still a real store

    settings.setValue(RESEARCH_SETTING, False)
    settings.sync()
    assert _research_mode_enabled() is False


def test_window_starts_in_the_garage(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert window._stacked.currentWidget() is window._garage
    assert not window._analysis_action.isEnabled()


def test_data_collection_guide_is_reachable_without_starting_torcs(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    assert window._capture_action.isEnabled()
    assert not window._capture.running

    window._capture_action.trigger()
    assert window._stacked.currentWidget() is window._capture
    assert not window._capture.running


def test_focused_app_omits_legacy_operator_tools_even_in_research_mode(qtbot, monkeypatch):
    monkeypatch.setenv("APEX_RESEARCH_MODE", "1")
    window = MainWindow()
    qtbot.addWidget(window)

    actions = {action.text() for action in window.findChildren(QAction)}
    assert "Live Pit Wall" not in actions
    assert "Robot Pilot" not in actions
    assert not hasattr(window, "_live")
    assert not hasattr(window, "_synthetic")
    assert "Study Results" in actions


def test_garage_lists_sample_session_and_opens_laps(qtbot):
    ensure_sample_session()
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    garage = window._garage
    assert garage.session is not None and len(garage.session.laps) == 5
    assert garage._table.rowCount() == 5
    # Found rather than hardcoded: which row is quickest is a property of the
    # recording, not of the table.
    best_row = next(
        row for row, lap in enumerate(garage.session.laps) if lap is garage.session.best_lap
    )
    assert garage._table.item(best_row, 4).text().startswith("SESSION BEST")

    garage._table.selectRow(1)
    assert garage._open_button.isEnabled()
    with qtbot.waitSignal(garage.lapOpened):
        garage._open_button.click()
    assert window._stacked.currentWidget() is window._analysis
    assert window._analysis.lap is garage.session.laps[1]
    assert window._analysis_action.isEnabled()


def test_main_window_wires_garage_sessions_to_visible_coaching_state(qtbot, monkeypatch):
    ensure_sample_session()
    monkeypatch.delenv("GRANITE_BASE_URL", raising=False)
    monkeypatch.setattr(
        "apex.coaching_queue.gh.capability",
        lambda: Capability(
            can_run=True,
            reason="Install the pinned model before automatic coaching.",
            model_present=False,
            download_bytes=1,
        ),
    )

    window = MainWindow()
    qtbot.addWidget(window)

    table = window._garage._table
    labels = [table.item(row, 4).text() for row in range(table.rowCount())]
    assert all("AI SETUP NEEDED" in label for label in labels)


def test_session_best_opens_against_its_own_corners_not_against_nothing(qtbot):
    """The Garage queue and Analysis must use the same context for the best lap.

    That context used to be no context at all: four fixed technique guides and
    no corner speeds, for the lap a driver most wants explained. It is now the
    best each of its own corners was driven, and the two screens have to agree
    on that or Granite runs twice on a participant CPU for one lap.
    """
    session = load_sample_session()
    best = session.best_lap
    window = MainWindow()
    qtbot.addWidget(window)
    window.show_analysis(best, session)

    view = window._analysis
    # single-lap + per-corner best + four optional references
    assert view._ref_combo.count() == 6
    selected = view._ref_combo.currentData()
    assert isinstance(selected, CompositeReference)
    assert selected.anchor is best
    queued = GarageCoachingQueue._composite(session)
    assert reference_name(queued) == reference_name(selected)

    stack = view._stack
    values = stack.values_at(2000.0)
    assert values is not None
    assert values["speed_kmh"] > 0
    assert {"dist", "speed_kmh", "throttle_pct", "brake_pct", "gear"} <= set(values)
    # A composite has no single trace to draw, so nothing is overlaid -- which
    # is what this lap showed before, when it was compared with nothing at all.
    x_ref, _ = stack._ref_curves[0].getData()
    assert x_ref is None or len(x_ref) == 0

    view._ref_combo.setCurrentIndex(2)  # slower laps remain optional references
    x_ref, _ = stack._ref_curves[0].getData()
    assert x_ref is not None and len(x_ref) > 100


def test_open_path_shows_lap_without_session(qtbot, tmp_path):
    csv = tmp_path / "two_points.csv"
    csv.write_text(CANONICAL)
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_path(csv)

    assert window._stacked.currentWidget() is window._analysis
    assert window._analysis._ref_combo.count() == 1  # single-lap analysis only
    assert "dist derived" in window.statusBar().currentMessage()


def test_open_path_imports_a_participant_handover(qtbot, tmp_path):
    """File > Open takes the file a participant actually sends.

    They send one archive, not a folder of CSVs, and opening only the CSV out of
    it would drop the driver, the phase and the questionnaire it exists to carry.
    """
    from racecoach.telemetry.handover import package
    from test_torcs import make_human_run

    capture = tmp_path / "P010-coached-20260819-104237"
    capture.mkdir()
    make_human_run(capture / "human-1.csv", laps=2)
    (capture / "manifest.json").write_text(
        json.dumps({"participant_id": "P010", "phase": "coached"}), encoding="utf-8"
    )
    archive = package(capture, tmp_path / "P010.zip").path

    window = MainWindow()
    qtbot.addWidget(window)
    window.open_path(archive)

    assert window._stacked.currentWidget() is window._garage
    session = window._garage.session
    assert session is not None and session.name == capture.name
    assert {lap.identity.driver for lap in session.laps} == {"P010"}
    assert "P010" in window._garage._participant.text()


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


def test_deleting_the_open_session_invalidates_analysis_and_compare(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    ensure_sample_session()
    window = MainWindow()
    qtbot.addWidget(window)
    window._garage.refresh_sessions(select="sample-session")
    session = window._garage.session
    assert session is not None
    window.show_analysis(session.laps[0], session)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )

    window._garage._delete_session()

    assert window._analysis.lap is None
    assert window._compare._session is None
    assert not window._analysis_action.isEnabled()
    assert not window._compare_action.isEnabled()
    assert window._stacked.currentWidget() is window._garage


def test_captured_run_opens_as_a_named_garage_session(qtbot, tmp_path):
    from test_torcs import make_run

    run_dir = tmp_path / "runs" / "P001-baseline"
    run_dir.mkdir(parents=True)
    make_run(run_dir / "telemetry.csv")
    window = MainWindow()
    qtbot.addWidget(window)

    window._open_captured_runs([str(run_dir)])

    assert window._stacked.currentWidget() is window._garage
    assert window._garage.session is not None
    assert window._garage.session.name == "P001-baseline"
    assert len(window._garage.session.laps) == 3

    window._open_captured_runs([str(run_dir)])
    assert window._garage.session is not None
    assert len(window._garage.session.laps) == 3


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


def test_finished_capture_registers_laps_without_a_further_click(qtbot, tmp_path, monkeypatch):
    """A participant should not have to know a click is what saves their drive."""
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    import numpy as np
    import pandas as pd

    from f1coach_core import list_sessions

    track, per_lap = 2050.0, 60
    dist = np.concatenate(
        [np.linspace(track - 40, track - 1, 25)]
        + [np.linspace(0, track, per_lap, endpoint=False)] * 2
    )
    n = dist.size
    run_dir = tmp_path / "ws" / "runs" / "human-P001"
    run_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "sim_time_s": 100.0 + np.arange(n) * 0.02,
            "dist_from_start_m": dist,
            "total_speed_mps": np.full(n, 45.0),
            "accel_cmd": np.full(n, 0.6),
            "brake_cmd": np.zeros(n),
            "steer_cmd": np.zeros(n),
            "gear": np.full(n, 4),
            "race_lap": np.concatenate(
                [np.full(25, 1)] + [np.full(per_lap, i + 1) for i in range(2)]
            ),
            "car_name": "Human, Driver",
        }
    ).to_csv(run_dir / "telemetry.csv", index=False)
    (run_dir / "meta.json").write_text(
        json.dumps({"driver": "P001", "phase": "baseline", "setup": "apex-study-v1"}),
        encoding="utf-8",
    )

    window = MainWindow()
    qtbot.addWidget(window)
    assert not list_sessions()

    window._capture_completed(str(run_dir), [str(run_dir)])

    assert [p.name for p in list_sessions()] == ["human-P001"]
    # ...and the identity from meta.json reached the laps themselves.
    window._garage.refresh_sessions(select="human-P001")
    table = window._garage._table
    assert [table.item(r, 0).text() for r in range(table.rowCount())] == ["1", "2"]
    assert {table.item(r, 1).text() for r in range(table.rowCount())} == {"P001"}
