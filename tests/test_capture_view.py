"""The participant-facing human telemetry collection guide."""

import json
import os
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QThreadPool

from apex.capture_view import CaptureGuideView
from racecoach.telemetry.human_capture import (
    HumanCaptureCancelled,
    HumanCaptureResult,
    TorcsStudyPreset,
)


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    # The test host may itself be reached over RDP; individual tests choose the
    # unsupported state explicitly rather than inheriting the runner session.
    monkeypatch.setenv("SESSIONNAME", "Console")


@pytest.fixture
def torcs_binary(tmp_path) -> Path:
    binary = tmp_path / "torcs"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    return binary


@pytest.fixture
def study_preset(tmp_path) -> TorcsStudyPreset:
    """The shipped default, spelled the way human_capture spells it.

    It said g-track-1 and five laps long after the default became aalborg and
    three, which is harmless on its own and not harmless next to a second
    fixture: a test that switched to the speedway preset and looked for
    "g-track-1" in the summary was reading a string the default already had.
    """
    race_config = tmp_path / "apexstudy.xml"
    race_config.write_text("<params name='Apex Study v1'/>", encoding="utf-8")
    return TorcsStudyPreset(
        preset_id="apex-study-v1",
        display_name="Apex Study v1",
        track_id="aalborg",
        track_category="road",
        car_id="car7-trb1",
        laps=3,
        race_config=race_config,
    )


@pytest.fixture
def speedway_preset(tmp_path) -> TorcsStudyPreset:
    race_config = tmp_path / "apexstudyspeedway.xml"
    race_config.write_text("<params name='Apex Study Speedway v1'/>", encoding="utf-8")
    return TorcsStudyPreset(
        preset_id="apex-study-speedway-v1",
        display_name="Apex Study v1 on CG Speedway",
        track_id="g-track-1",
        track_category="road",
        car_id="car7-trb1",
        laps=3,
        race_config=race_config,
    )


@pytest.fixture
def team_preset(tmp_path) -> TorcsStudyPreset:
    race_config = tmp_path / "apexibmf1.xml"
    race_config.write_text("<params name='Apex Team Practice'/>", encoding="utf-8")
    return TorcsStudyPreset(
        preset_id="ibmf1-practice-v5",
        display_name="Team practice (matches IBMF1)",
        track_id="g-track-1",
        track_category="road",
        car_id="car1-trb1",
        laps=2,
        race_config=race_config,
        opponents=("berniw", "bt", "olethros"),
    )


def make_ready(view: CaptureGuideView) -> None:
    view._participant_id.setText("P001")
    for check in view._readiness_checks:
        check.setChecked(True)


def test_the_facilitator_is_told_the_grid_is_not_empty(qtbot, torcs_binary, team_preset):
    """Traffic is the condition most easily missed and the one that changes most.

    Solo, every session finishes P1 and the lap-to-lap spread belongs to the
    driver. With three robots neither is true, and a facilitator who reads the
    summary as "the usual race on a different track" would pool two things that
    do not pool.
    """
    view = CaptureGuideView(torcs_binary=torcs_binary, study_preset=team_preset)
    qtbot.addWidget(view)

    assert "3 opponents" in view._preset_summary.text()
    assert "2 laps" in view._preset_summary.text()
    assert "car1-trb1" in view._preset_summary.text()


def test_capture_has_a_dedicated_worker_so_model_jobs_cannot_delay_torcs(
    qtbot, torcs_binary, study_preset
):
    view = CaptureGuideView(torcs_binary=torcs_binary, study_preset=study_preset)
    qtbot.addWidget(view)

    assert view._pool is not QThreadPool.globalInstance()
    assert view._pool.maxThreadCount() == 1


def test_guide_requires_pseudonym_readiness_and_simulator_before_starting(
    qtbot, torcs_binary, study_preset
):
    calls = []
    view = CaptureGuideView(
        capture_fn=lambda *a, **k: calls.append((a, k)),
        torcs_binary=torcs_binary,
        study_preset=study_preset,
    )
    qtbot.addWidget(view)

    assert view._title.text() == "Collect driving data"
    assert "No terminal" in view._intro.text()
    assert view._phase.currentData() == "baseline"
    assert "Ready" in view._simulator_status.text()
    assert "aalborg" in view._preset_summary.text()
    assert "car7-trb1" in view._preset_summary.text()
    assert "3 laps" in view._preset_summary.text()
    assert "no opponents" in view._preset_summary.text()
    assert not view._start_button.isEnabled()

    view._participant_id.setText("Alice Smith")
    for check in view._readiness_checks:
        check.setChecked(True)
    assert not view._start_button.isEnabled()
    assert "pseudonymous" in view._form_error.text()

    view._participant_id.setText("P001")
    assert view._start_button.isEnabled()
    assert not calls  # opening the page and completing checks never starts TORCS


def test_capture_runs_off_the_gui_thread_and_shows_registered_result(
    qtbot, tmp_path, torcs_binary, study_preset
):
    gui_thread = threading.get_ident()
    capture_threads = []
    capture_dir = tmp_path / "capture"
    run_dir = tmp_path / "runs" / "human-1"
    capture_dir.mkdir()
    run_dir.mkdir(parents=True)
    # The participant is told how many laps they completed, so the run has to
    # carry the same lap record the Garage reads.
    (run_dir / "meta.json").write_text(json.dumps({"laps_seen": [1, 2, 3, 4, 5]}), "utf-8")

    def fake_capture(config, *, runner, stop_requested):
        del runner, stop_requested
        capture_threads.append(threading.get_ident())
        assert config.participant_id == "P001"
        assert config.phase == "baseline"
        assert config.preset == study_preset
        return HumanCaptureResult(capture_dir=capture_dir, run_dirs=(run_dir,))

    view = CaptureGuideView(
        capture_fn=fake_capture,
        torcs_binary=torcs_binary,
        study_preset=study_preset,
    )
    qtbot.addWidget(view)
    make_ready(view)

    with qtbot.waitSignal(view.sessionFinished, timeout=2000) as finished:
        view._start_button.click()

    assert capture_threads and capture_threads != [gui_thread]
    assert finished.args == [str(capture_dir), [str(run_dir)]]
    assert view._pages.currentWidget() is view._complete_page
    assert "5 complete laps" in view._result_summary.text()
    assert str(capture_dir) in view._result_path.text()
    assert view._new_session_button.isEnabled()

    with qtbot.waitSignal(view.resultsRequested) as requested:
        view._open_results_button.click()
    assert requested.args == [[str(run_dir)]]


def test_completion_confirms_usable_race_window_footage(
    qtbot, tmp_path, torcs_binary, study_preset
):
    capture_dir = tmp_path / "capture-with-video"
    capture_dir.mkdir()
    video = capture_dir / "session.mp4"
    video.write_bytes(b"finished mp4")
    (capture_dir / "manifest.json").write_text(
        json.dumps({
            "recording": {
                "path": str(video),
                "started_at": 1.0,
                "duration_s": 42.0,
            }
        }),
        encoding="utf-8",
    )

    view = _completed_view(qtbot, tmp_path, torcs_binary, study_preset, capture_dir)

    summary = view._result_summary.text().lower()
    assert "race-window footage recorded" in summary
    assert "ai coaching uses telemetry" in summary


def test_completion_exposes_recording_failure_without_losing_telemetry(
    qtbot, tmp_path, torcs_binary, study_preset
):
    capture_dir = tmp_path / "capture-without-video"
    capture_dir.mkdir()
    (capture_dir / "manifest.json").write_text(
        json.dumps({"recording_error": "The TORCS window never appeared."}),
        encoding="utf-8",
    )

    view = _completed_view(qtbot, tmp_path, torcs_binary, study_preset, capture_dir)

    summary = view._result_summary.text().lower()
    assert "telemetry is saved" in summary
    assert "footage was unavailable" in summary
    assert "window never appeared" in summary


def test_untrusted_recording_manifest_cannot_break_the_completion_page(
    qtbot, tmp_path, torcs_binary, study_preset
):
    capture_dir = tmp_path / "capture-with-invalid-video-path"
    capture_dir.mkdir()
    (capture_dir / "manifest.json").write_bytes(b"\xff")

    view = _completed_view(qtbot, tmp_path, torcs_binary, study_preset, capture_dir)

    summary = view._result_summary.text().lower()
    assert "lap saved" in summary
    assert "footage status is unavailable" in summary


def test_drive_page_does_not_claim_torcs_is_open_before_it_appears(
    qtbot, torcs_binary, study_preset
):
    view = CaptureGuideView(torcs_binary=torcs_binary, study_preset=study_preset)
    qtbot.addWidget(view)

    assert "opening torcs" in view._drive_page.heading.text().lower()
    assert "is open" not in view._drive_page.heading.text().lower()


def test_stop_ends_background_session_and_restores_the_guide(
    qtbot, tmp_path, torcs_binary, study_preset
):
    started = threading.Event()
    capture_dir = tmp_path / "cancelled"
    capture_dir.mkdir()

    def cancellable_capture(config, *, runner, stop_requested):
        del config, runner
        started.set()
        while not stop_requested():
            time.sleep(0.005)
        raise HumanCaptureCancelled("capture stopped by the user", capture_dir)

    view = CaptureGuideView(
        capture_fn=cancellable_capture,
        torcs_binary=torcs_binary,
        study_preset=study_preset,
    )
    qtbot.addWidget(view)
    make_ready(view)
    view._start_button.click()
    assert started.wait(1.0)
    qtbot.waitUntil(lambda: view.running, timeout=1000)

    with qtbot.waitSignal(view.sessionStopped, timeout=2000):
        view._stop_button.click()

    assert not view.running
    assert view._pages.currentWidget() is view._setup_page
    assert "stopped" in view._form_error.text().lower()
    assert view._start_button.isEnabled()
    assert view.shutdown(timeout_s=0.1)


def test_missing_simulator_has_a_readable_non_terminal_state(qtbot, tmp_path, study_preset):
    missing = tmp_path / "missing-torcs"
    view = CaptureGuideView(torcs_binary=missing, study_preset=study_preset)
    qtbot.addWidget(view)
    make_ready(view)

    assert not view._start_button.isEnabled()
    assert "not available" in view._simulator_status.text().lower()
    assert "facilitator" in view._simulator_help.text().lower()
    assert os.fspath(missing) not in view._simulator_help.text()


def test_missing_study_preset_disables_start_with_facilitator_message(
    qtbot, tmp_path, torcs_binary
):
    preset = TorcsStudyPreset(
        preset_id="apex-study-v1",
        display_name="Apex Study v1",
        track_id="aalborg",
        track_category="road",
        car_id="car7-trb1",
        laps=3,
        race_config=tmp_path / "missing.xml",
    )
    view = CaptureGuideView(torcs_binary=torcs_binary, study_preset=preset)
    qtbot.addWidget(view)
    make_ready(view)

    assert not view._start_button.isEnabled()
    assert "preset" in view._simulator_status.text().lower()
    assert "facilitator" in view._simulator_help.text().lower()


def test_remote_desktop_disables_capture_before_torcs_can_crash(
    qtbot, torcs_binary, study_preset, monkeypatch
):
    monkeypatch.setattr(
        "apex.capture_view.graphical_session_issue",
        lambda: (
            "TORCS driving cannot run reliably through Remote Desktop. "
            "Sign in locally and reopen Apex."
        ),
    )
    view = CaptureGuideView(torcs_binary=torcs_binary, study_preset=study_preset)
    qtbot.addWidget(view)
    make_ready(view)

    assert not view._start_button.isEnabled()
    assert "remote desktop" in view._simulator_status.text().lower()
    assert "locally" in view._simulator_help.text().lower()


def test_the_saved_summary_counts_laps_not_recordings(tmp_path):
    """One drive is one file; the participant asked how many laps they finished."""
    from apex.capture_view import _saved_summary

    run_dir = tmp_path / "human-1"
    run_dir.mkdir()
    (run_dir / "meta.json").write_text(json.dumps({"laps_seen": [1, 2, 3, 4, 5]}), "utf-8")
    assert "5 complete laps" in _saved_summary([run_dir])

    (run_dir / "meta.json").write_text(json.dumps({"laps_seen": [1]}), "utf-8")
    assert "1 complete lap " in _saved_summary([run_dir])


def test_the_saved_summary_does_not_claim_laps_that_were_never_finished(tmp_path):
    from apex.capture_view import _saved_summary

    run_dir = tmp_path / "human-1"
    run_dir.mkdir()
    (run_dir / "meta.json").write_text(json.dumps({"laps_seen": []}), "utf-8")
    assert "no complete lap" in _saved_summary([run_dir])


def test_the_saved_summary_stays_useful_when_the_count_is_unreadable(tmp_path):
    """An unreadable meta.json must not hide the fact that the data is saved."""
    from apex.capture_view import _saved_summary

    run_dir = tmp_path / "human-1"
    run_dir.mkdir()
    assert "saved" in _saved_summary([run_dir])



def _completed_view(qtbot, tmp_path, torcs_binary, study_preset, capture_dir):
    """Drive a fake capture to the completion page, as a participant would."""
    import json

    from racecoach.telemetry.human_capture import HumanCaptureResult

    run_dir = tmp_path / "runs" / "human-1"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "meta.json").write_text(json.dumps({"laps_seen": [1]}), "utf-8")

    def fake_capture(config, *, runner, stop_requested):
        del config, runner, stop_requested
        return HumanCaptureResult(capture_dir=capture_dir, run_dirs=(run_dir,))

    view = CaptureGuideView(
        capture_fn=fake_capture, torcs_binary=torcs_binary, study_preset=study_preset
    )
    qtbot.addWidget(view)
    make_ready(view)
    with qtbot.waitSignal(view.sessionFinished, timeout=2000):
        view._start_button.click()
    return view


def test_the_participant_can_save_one_file_to_send(
    qtbot, tmp_path, torcs_binary, study_preset, monkeypatch
):
    """Handing the data over is the point of the session, so it is one click."""
    import json

    capture_dir = tmp_path / "A001-baseline-20260821-120000"
    capture_dir.mkdir()
    (capture_dir / "human-1.csv").write_text("t,speed\n0,10\n", encoding="utf-8")
    (capture_dir / "manifest.json").write_text(
        json.dumps({"participant_id": "A001"}), encoding="utf-8"
    )
    view = _completed_view(qtbot, tmp_path, torcs_binary, study_preset, capture_dir)

    target = tmp_path / "to-send.zip"
    monkeypatch.setattr(
        "apex.capture_view.QFileDialog.getSaveFileName", lambda *a, **k: (str(target), "")
    )
    view._package_button.click()

    assert target.is_file()
    assert "Send that one file" in view._result_path.text()


def test_saving_a_file_to_send_reports_a_failure_instead_of_looking_done(
    qtbot, tmp_path, torcs_binary, study_preset, monkeypatch
):
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    view = _completed_view(qtbot, tmp_path, torcs_binary, study_preset, empty)

    monkeypatch.setattr(
        "apex.capture_view.QFileDialog.getSaveFileName",
        lambda *a, **k: (str(tmp_path / "out.zip"), ""),
    )
    view._package_button.click()

    assert "no files to hand over" in view._result_path.text()
    assert not (tmp_path / "out.zip").exists()


def test_the_facilitator_can_record_a_control_run_without_the_command_line(
    qtbot, torcs_binary, study_preset
):
    """A study with no unadvised second run cannot tell coaching from practice.

    The condition existed underneath all along -- phase is a free slug -- but only
    on the command line, where nothing checks the participant id or the phase. An
    arm that can only be collected by the facilitator typing it correctly under
    time pressure is an arm that will be collected wrong.
    """
    view = CaptureGuideView(torcs_binary=torcs_binary, study_preset=study_preset)
    qtbot.addWidget(view)

    offered = [
        (view._phase.itemText(i), view._phase.itemData(i))
        for i in range(view._phase.count())
    ]
    assert [slug for _label, slug in offered] == [
        "baseline",
        "coached",
        "control",
        "familiarisation",
    ]
    assert view._phase.currentData() == "baseline"  # unchanged default

    # Both second-run arms have to be distinguishable from the baseline and from
    # each other by reading one line, and the control has to say what it is: the
    # participant practised on their own and was given nothing.
    labels = dict((slug, label) for label, slug in offered)
    assert "first run" in labels["baseline"]
    assert "after AI advice" in labels["coached"]
    assert "own practice only, no AI advice" in labels["control"]

    make_ready(view)
    view._phase.setCurrentIndex(2)
    assert view._current_config().phase == "control"


def test_one_preset_leaves_nothing_to_choose(qtbot, torcs_binary, study_preset):
    """A build with a single assignment must not show a selector with one row."""
    view = CaptureGuideView(torcs_binary=torcs_binary, study_preset=study_preset)
    qtbot.addWidget(view)
    make_ready(view)

    assert view._setup_page.preset.isEnabled() is False
    assert view._setup_page.preset.isVisibleTo(view._setup_page) is False
    assert view._current_config().preset == study_preset


def test_the_chosen_circuit_is_the_one_the_race_is_started_on(
    qtbot, torcs_binary, study_preset, speedway_preset, monkeypatch
):
    """The preset is recorded into the manifest, so picking the wrong one
    mislabels every row of the capture."""
    monkeypatch.setattr(
        "apex.capture_view.study_presets", lambda binary: (study_preset, speedway_preset)
    )
    view = CaptureGuideView(torcs_binary=torcs_binary)
    qtbot.addWidget(view)
    make_ready(view)

    assert view._setup_page.preset.isEnabled() is True
    assert view._current_config().preset == study_preset

    assert "aalborg" in view._preset_summary.text()

    view._setup_page.preset.setCurrentIndex(1)

    assert view._current_config().preset == speedway_preset
    assert "g-track-1" in view._preset_summary.text()
    assert "aalborg" not in view._preset_summary.text()


def test_a_circuit_this_build_cannot_open_is_reported_before_the_race_starts(
    qtbot, tmp_path, torcs_binary, study_preset, speedway_preset, monkeypatch
):
    """A partial install can hold one preset's race config and not the other's."""
    speedway_preset.race_config.unlink()
    monkeypatch.setattr(
        "apex.capture_view.study_presets", lambda binary: (study_preset, speedway_preset)
    )
    view = CaptureGuideView(torcs_binary=torcs_binary)
    qtbot.addWidget(view)
    make_ready(view)
    assert view._start_button.isEnabled() is True

    view._setup_page.preset.setCurrentIndex(1)

    assert view._start_button.isEnabled() is False
    assert "not available" in view._simulator_status.text()


# --- the loop closes without the participant closing it -----------------------


def _handoff_view(qtbot, tmp_path, torcs_binary, study_preset, finish_fn):
    """A completed capture whose hand-over is driven by `finish_fn`."""
    import json as _json

    from racecoach.telemetry.human_capture import HumanCaptureResult

    capture_dir = tmp_path / "P001-coached-20260902-101500"
    capture_dir.mkdir(parents=True, exist_ok=True)
    run_dir = tmp_path / "runs" / "human-1"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "meta.json").write_text(_json.dumps({"laps_seen": [1]}), "utf-8")

    def fake_capture(config, *, runner, stop_requested):
        del config, runner, stop_requested
        return HumanCaptureResult(capture_dir=capture_dir, run_dirs=(run_dir,))

    view = CaptureGuideView(
        capture_fn=fake_capture,
        torcs_binary=torcs_binary,
        study_preset=study_preset,
        finish_fn=finish_fn,
    )
    qtbot.addWidget(view)
    make_ready(view)
    with qtbot.waitSignal(view.sessionFinished, timeout=2000):
        view._start_button.click()
    return view, capture_dir


def test_finishing_a_session_hands_it_over_without_being_asked(
    qtbot, tmp_path, torcs_binary, study_preset
):
    """Every step of this used to be somebody remembering to do it.

    The one most easily forgotten -- reading the laps into the study path -- was
    also invisible when it did not happen, because the Garage showed the session
    either way.
    """
    from racecoach.telemetry.handoff import Handoff, Step

    seen = []

    def finish_fn(capture_dir, *, progress=None, **_kwargs):
        seen.append(Path(capture_dir))
        progress("ok  laps read into the study: 3 lap(s)")
        return Handoff(
            capture_dir=Path(capture_dir),
            archive=Path(capture_dir).with_suffix(".zip"),
            review_url="https://demo.lzqqq.org/",
            delivered=True,
            steps=(Step("laps read into the study", True, "3 lap(s)"),),
        )

    view, capture_dir = _handoff_view(qtbot, tmp_path, torcs_binary, study_preset, finish_fn)

    qtbot.waitUntil(lambda: "The file to send" in view._handoff_status.text(), timeout=3000)
    assert seen == [capture_dir]
    assert "laps read into the study" in view._handoff_status.text()
    assert "https://demo.lzqqq.org/" in view._handoff_status.text()


def test_a_hand_over_that_saved_nothing_says_so_and_names_the_way_out(
    qtbot, tmp_path, torcs_binary, study_preset
):
    """Losing the file is the one outcome that costs data rather than time."""
    from racecoach.telemetry.handoff import Handoff

    def finish_fn(capture_dir, *, progress=None, **_kwargs):
        del progress
        return Handoff(capture_dir=Path(capture_dir))

    view, _capture = _handoff_view(qtbot, tmp_path, torcs_binary, study_preset, finish_fn)

    qtbot.waitUntil(lambda: "could not save" in view._handoff_status.text(), timeout=3000)
    assert "Save another copy" in view._handoff_status.text()
    assert view._package_button.isEnabled()


def test_a_hand_over_that_raises_does_not_reach_the_participant(
    qtbot, tmp_path, torcs_binary, study_preset
):
    def finish_fn(capture_dir, *, progress=None, **_kwargs):
        del capture_dir, progress
        raise RuntimeError("the network stack fell over")

    view, _capture = _handoff_view(qtbot, tmp_path, torcs_binary, study_preset, finish_fn)

    qtbot.waitUntil(lambda: "could not save" in view._handoff_status.text(), timeout=3000)
    assert "the network stack fell over" in view._handoff_status.text()


def test_starting_another_session_stops_showing_the_last_one(
    qtbot, tmp_path, torcs_binary, study_preset
):
    """An upload for the last participant must not appear under the next one."""
    from racecoach.telemetry.handoff import Handoff

    def finish_fn(capture_dir, *, progress=None, **_kwargs):
        del progress
        return Handoff(capture_dir=Path(capture_dir), archive=Path(capture_dir))

    view, _capture = _handoff_view(qtbot, tmp_path, torcs_binary, study_preset, finish_fn)
    qtbot.waitUntil(lambda: view._handoff_status.text() != "", timeout=3000)

    view.reset_guide()

    assert view._handoff_status.text() == ""
    assert view._handoff is None
