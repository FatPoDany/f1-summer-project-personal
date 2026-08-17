"""Facilitator-only Qt workflow for synthetic reference batches."""

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from apex.main_window import MainWindow
from apex.synthetic_capture_view import SyntheticCaptureView
from racecoach.telemetry.synthetic_capture import (
    RobotStudyPreset,
    SyntheticBatchResult,
    SyntheticProgress,
    SyntheticSessionOutcome,
)
from test_synthetic_capture import write_robot_preset


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


@pytest.fixture
def torcs_binary(tmp_path) -> Path:
    binary = tmp_path / "torcs"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    return binary


@pytest.fixture
def robot_preset(tmp_path) -> RobotStudyPreset:
    race_config = tmp_path / "apexrobotstudy.xml"
    write_robot_preset(race_config)
    return RobotStudyPreset(
        preset_id="apex-robot-study-v2",
        display_name="Apex Robot Study v2",
        track_id="g-track-1",
        track_category="road",
        car_id="car7-trb1",
        laps=3,
        race_config=race_config,
    )


def test_view_defaults_to_three_and_never_starts_on_open(
    qtbot, torcs_binary, robot_preset
):
    calls = []
    view = SyntheticCaptureView(
        capture_fn=lambda *args, **kwargs: calls.append((args, kwargs)),
        torcs_binary=torcs_binary,
        study_preset=robot_preset,
    )
    qtbot.addWidget(view)
    view.show()

    assert "Synthetic" in view._title.text()
    assert "not participant data" in view._warning.text().lower()
    assert view._count.value() == 3
    assert view._count.minimum() == 1 and view._count.maximum() == 20
    assert "3 laps" in view._preset_summary.text()
    assert view._start_button.isEnabled()
    assert not view.running
    assert calls == []


def test_batch_runs_off_gui_thread_and_visible_controls_open_results(
    qtbot, tmp_path, torcs_binary, robot_preset
):
    gui_thread = threading.get_ident()
    capture_threads = []
    batch_dir = tmp_path / "batch"
    run_dirs = tuple(tmp_path / "runs" / f"run-{index}" for index in range(1, 4))
    for path in run_dirs:
        path.mkdir(parents=True)

    def fake_capture(config, *, runner, stop_requested, on_progress):
        del runner, stop_requested
        capture_threads.append(threading.get_ident())
        batch_dir.mkdir()
        outcomes = []
        for ordinal, run_dir in enumerate(run_dirs, start=1):
            session_id = f"SIM-BERNIW9-{ordinal:03d}"
            on_progress(
                SyntheticProgress(
                    ordinal, config.count, session_id, "running", f"Running {session_id}"
                )
            )
            outcomes.append(
                SyntheticSessionOutcome(
                    session_id=session_id,
                    status="complete",
                    capture_dir=batch_dir / session_id,
                    run_dirs=(run_dir,),
                )
            )
            on_progress(
                SyntheticProgress(
                    ordinal,
                    config.count,
                    session_id,
                    "complete",
                    f"Completed {session_id}",
                )
            )
        return SyntheticBatchResult(batch_dir, tuple(outcomes))

    view = SyntheticCaptureView(
        capture_fn=fake_capture,
        torcs_binary=torcs_binary,
        study_preset=robot_preset,
    )
    qtbot.addWidget(view)

    with qtbot.waitSignal(view.batchFinished, timeout=2000) as finished:
        view._start_button.click()

    assert capture_threads and capture_threads != [gui_thread]
    assert finished.args == [str(batch_dir), [str(path) for path in run_dirs]]
    assert view._progress.value() == 3 and view._progress.maximum() == 3
    assert view._result_table.rowCount() == 3
    assert not view.running

    with qtbot.waitSignal(view.resultsRequested) as requested:
        view._open_results_button.click()
    assert requested.args == [[str(path) for path in run_dirs]]


def test_stop_is_responsive_and_later_results_are_not_registered(
    qtbot, tmp_path, torcs_binary, robot_preset
):
    started = threading.Event()
    batch_dir = tmp_path / "cancelled-batch"

    def fake_capture(config, *, runner, stop_requested, on_progress):
        del runner, on_progress
        batch_dir.mkdir()
        started.set()
        while not stop_requested():
            time.sleep(0.005)
        outcomes = tuple(
            SyntheticSessionOutcome(
                session_id=f"SIM-BERNIW9-{ordinal:03d}",
                status="cancelled" if ordinal == 1 else "not_started",
            )
            for ordinal in range(1, config.count + 1)
        )
        return SyntheticBatchResult(batch_dir, outcomes)

    view = SyntheticCaptureView(
        capture_fn=fake_capture,
        torcs_binary=torcs_binary,
        study_preset=robot_preset,
    )
    qtbot.addWidget(view)
    view._start_button.click()
    assert started.wait(1.0)
    qtbot.waitUntil(lambda: view.running, timeout=1000)

    with qtbot.waitSignal(view.batchStopped, timeout=2000):
        view._stop_button.click()

    assert not view.running
    assert "cancelled" in view._status.text().lower()
    assert not view._open_results_button.isEnabled()
    assert view.shutdown(timeout_s=0.1)


def test_research_navigation_is_absent_by_default_and_opt_in_does_not_start(
    qtbot, monkeypatch
):
    monkeypatch.delenv("APEX_RESEARCH_MODE", raising=False)
    participant_window = MainWindow()
    qtbot.addWidget(participant_window)
    assert participant_window._synthetic is None
    assert participant_window._synthetic_action is None

    monkeypatch.setenv("APEX_RESEARCH_MODE", "1")
    research_window = MainWindow()
    qtbot.addWidget(research_window)
    assert research_window._synthetic is not None
    assert research_window._synthetic_action is not None
    assert not research_window._synthetic.running

    research_window._synthetic_action.trigger()
    assert research_window._stacked.currentWidget() is research_window._synthetic
    assert not research_window._synthetic.running


def test_stale_completion_cannot_mutate_a_newer_batch(
    qtbot, tmp_path, torcs_binary, robot_preset
):
    view = SyntheticCaptureView(
        torcs_binary=torcs_binary,
        study_preset=robot_preset,
    )
    qtbot.addWidget(view)
    current_token = object()
    view._task = SimpleNamespace(token=current_token)
    view._status.setText("Newer batch is running")
    stale = SyntheticBatchResult(
        tmp_path / "old-batch",
        (
            SyntheticSessionOutcome(
                session_id="SIM-BERNIW9-001",
                status="complete",
                run_dirs=(tmp_path / "old-run",),
            ),
        ),
    )

    view._on_completed(object(), stale)

    assert view._task.token is current_token
    assert view._status.text() == "Newer batch is running"
    assert view._result_table.rowCount() == 0


def test_completed_synthetic_run_opens_through_existing_garage_flow(
    qtbot, tmp_path, monkeypatch
):
    from test_torcs import make_run

    monkeypatch.setenv("APEX_RESEARCH_MODE", "1")
    run_dir = tmp_path / "runs" / "SIM-BERNIW9-001"
    run_dir.mkdir(parents=True)
    make_run(run_dir / "telemetry.csv")
    window = MainWindow()
    qtbot.addWidget(window)
    assert window._synthetic is not None

    window._synthetic.resultsRequested.emit([str(run_dir)])

    assert window._stacked.currentWidget() is window._garage
    assert window._garage.session is not None
    assert window._garage.session.name == "SIM-BERNIW9-001"
    assert len(window._garage.session.laps) == 3
