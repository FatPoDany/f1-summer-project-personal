"""Synthetic TORCS reference capture contracts and failure paths."""

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from racecoach.cli import main
from racecoach.telemetry.run_store import list_runs, load_run
from racecoach.telemetry.synthetic_capture import (
    REFERENCE_PHASE,
    ManagedSyntheticTorcsRunner,
    RobotIdentity,
    RobotStudyPreset,
    SyntheticCaptureCancelled,
    SyntheticCaptureConfig,
    SyntheticCaptureError,
    capture_synthetic_batch,
    capture_synthetic_session,
    default_robot_study_preset,
    synthetic_captures_root,
    synthetic_command,
    synthetic_session_id,
)


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


@pytest.fixture
def torcs_binary(tmp_path) -> Path:
    binary = tmp_path / "torcs"
    binary.write_bytes(b"test executable placeholder")
    binary.chmod(0o700)
    return binary


def robot_preset(tmp_path: Path) -> RobotStudyPreset:
    race_config = tmp_path / "apexrobotstudy.xml"
    write_robot_preset(race_config)
    return RobotStudyPreset(
        preset_id="apex-robot-study-v1",
        display_name="Apex Robot Study v1",
        track_id="g-track-1",
        track_category="road",
        car_id="car7-trb1",
        laps=3,
        race_config=race_config,
    )


def write_robot_preset(path: Path) -> None:
    path.write_text(
        """<?xml version="1.0"?>
<params name="Apex Robot Study v1">
  <section name="Tracks">
    <section name="1">
      <attstr name="name" val="g-track-1"/>
      <attstr name="category" val="road"/>
    </section>
  </section>
  <section name="Apex Robot Study">
    <attnum name="laps" val="3"/>
  </section>
  <section name="Drivers">
    <attnum name="maximum number" val="1"/>
    <attstr name="focused module" val="berniw"/>
    <attnum name="focused idx" val="9"/>
    <section name="1">
      <attnum name="idx" val="9"/>
      <attstr name="module" val="berniw"/>
    </section>
  </section>
</params>
""",
        encoding="utf-8",
    )


def write_robot_capture(
    path: Path,
    *,
    schema_version: str = "apex-robot-v1",
    driver_module: str = "berniw",
    driver_index: int = 9,
    car_model: str = "car7-trb1",
    track_id: str = "g-track-1",
    completed_laps: int = 3,
    initial_remaining_laps: int = 3,
) -> None:
    rows = []
    sample = 0
    for lap in range(1, completed_laps + 1):
        for point in range(25):
            rows.append(
                {
                    "schema_version": schema_version,
                    "sample": sample,
                    "sim_time_s": sample * 0.02,
                    "dist_from_start_m": point * 10.0,
                    "accel_cmd": 0.7,
                    "brake_cmd": 0.0,
                    "steer_cmd": 0.01,
                    "gear": 3,
                    "car_name": "berniw 9",
                    "car_model": car_model,
                    "driver_module": driver_module,
                    "driver_index": driver_index,
                    "track_internal_name": track_id,
                    "race_lap": lap,
                    "remaining_laps": initial_remaining_laps - lap + 1,
                    "race_finished": int(
                        lap == completed_laps and point == 24
                    ),
                    "total_speed_mps": 40.0,
                }
            )
            sample += 1
    pd.DataFrame(rows).to_csv(path, index=False)


def test_synthetic_contract_accepts_only_the_pinned_reference(torcs_binary, tmp_path):
    config = SyntheticCaptureConfig(
        torcs_binary=torcs_binary,
        preset=robot_preset(tmp_path),
    )

    assert config.count == 3
    assert config.phase == REFERENCE_PHASE
    assert config.robot == RobotIdentity("berniw", 9, "car7-trb1")
    assert config.preset.laps == 3
    assert synthetic_captures_root().parts[-2:] == ("captures", "synthetic")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("module", "human", "module"),
        ("index", 8, "index"),
        ("index", True, "index"),
        ("car_id", "car1-trb1", "car"),
    ],
)
def test_robot_identity_rejects_every_unapproved_assignment(field, value, message):
    values = {"module": "berniw", "index": 9, "car_id": "car7-trb1"}
    values[field] = value

    with pytest.raises(ValueError, match=message):
        RobotIdentity(**values)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("preset_id", "apex-study-v1", "preset"),
        ("track_id", "g-track-2", "track"),
        ("track_category", "dirt", "category"),
        ("car_id", "car1-trb1", "car"),
        ("laps", 5, "laps"),
        ("laps", True, "laps"),
        ("race_config", Path("robot.txt"), "XML"),
    ],
)
def test_robot_preset_rejects_drift_from_the_three_lap_assignment(
    tmp_path, field, value, message
):
    values = {
        "preset_id": "apex-robot-study-v1",
        "display_name": "Apex Robot Study v1",
        "track_id": "g-track-1",
        "track_category": "road",
        "car_id": "car7-trb1",
        "laps": 3,
        "race_config": tmp_path / "apexrobotstudy.xml",
    }
    values[field] = value

    with pytest.raises(ValueError, match=message):
        RobotStudyPreset(**values)


@pytest.mark.parametrize("count", [0, 21, True, 3.0])
def test_batch_count_is_an_integer_between_one_and_twenty(
    torcs_binary, tmp_path, count
):
    with pytest.raises(ValueError, match="count"):
        SyntheticCaptureConfig(
            torcs_binary=torcs_binary,
            preset=robot_preset(tmp_path),
            count=count,
        )


@pytest.mark.parametrize("argument", ["", "bad\x00arg", "-r", "-Rother.xml"])
def test_robot_preset_cannot_be_overridden_by_arguments(
    torcs_binary, tmp_path, argument
):
    with pytest.raises(ValueError, match="argument|preset"):
        SyntheticCaptureConfig(
            torcs_binary=torcs_binary,
            preset=robot_preset(tmp_path),
            torcs_args=(argument,),
        )


def test_session_ids_are_stable_and_bounded_by_the_batch(torcs_binary, tmp_path):
    config = SyntheticCaptureConfig(
        torcs_binary=torcs_binary,
        preset=robot_preset(tmp_path),
        count=3,
    )

    assert synthetic_session_id(config, 1) == "SIM-BERNIW9-001"
    assert synthetic_session_id(config, 3) == "SIM-BERNIW9-003"
    with pytest.raises(ValueError, match="ordinal"):
        synthetic_session_id(config, 0)
    with pytest.raises(ValueError, match="ordinal"):
        synthetic_session_id(config, 4)


def test_default_robot_preset_resolves_from_the_torcs_runtime(torcs_binary):
    preset = default_robot_study_preset(torcs_binary)

    assert preset.preset_id == "apex-robot-study-v1"
    assert preset.laps == 3
    assert preset.race_config == (
        torcs_binary.resolve().parent.parent
        / "share"
        / "games"
        / "torcs"
        / "config"
        / "raceman"
        / "apexrobotstudy.xml"
    )


def test_command_uses_validated_unattended_preset_without_a_shell(torcs_binary, tmp_path):
    preset = robot_preset(tmp_path)
    config = SyntheticCaptureConfig(
        torcs_binary=torcs_binary,
        preset=preset,
        torcs_args=("--noisy",),
    )

    assert synthetic_command(config) == (
        str(torcs_binary),
        "-r",
        str(preset.race_config),
        "--noisy",
    )


def test_missing_or_malformed_preset_fails_before_launch(torcs_binary, tmp_path):
    missing = RobotStudyPreset(
        preset_id="apex-robot-study-v1",
        display_name="Apex Robot Study v1",
        track_id="g-track-1",
        track_category="road",
        car_id="car7-trb1",
        laps=3,
        race_config=tmp_path / "missing.xml",
    )
    with pytest.raises(ValueError, match="configuration not found"):
        synthetic_command(SyntheticCaptureConfig(torcs_binary, missing))

    malformed = robot_preset(tmp_path)
    malformed.race_config.write_text("<params>", encoding="utf-8")
    with pytest.raises(ValueError, match="parseable XML"):
        synthetic_command(SyntheticCaptureConfig(torcs_binary, malformed))

    unsafe = robot_preset(tmp_path)
    unsafe.race_config.write_text(
        '<!DOCTYPE params [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        '<params name="Apex Robot Study v1">&xxe;</params>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="safe XML"):
        synthetic_command(SyntheticCaptureConfig(torcs_binary, unsafe))


@pytest.mark.parametrize(
    ("before", "after", "message"),
    [
        ('name="Apex Robot Study v1"', 'name="Other"', "name"),
        ('val="g-track-1"', 'val="g-track-2"', "track"),
        ('val="road"', 'val="dirt"', "category"),
        ('name="laps" val="3"', 'name="laps" val="5"', "laps"),
        ('name="module" val="berniw"', 'name="module" val="human"', "module"),
        ('name="idx" val="9"', 'name="idx" val="8"', "index"),
    ],
)
def test_preset_xml_assignment_drift_is_rejected(
    torcs_binary, tmp_path, before, after, message
):
    preset = robot_preset(tmp_path)
    text = preset.race_config.read_text("utf-8")
    preset.race_config.write_text(text.replace(before, after), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        synthetic_command(SyntheticCaptureConfig(torcs_binary, preset))


def test_single_session_launches_without_shell_and_registers_valid_evidence(
    torcs_binary, tmp_path
):
    preset = robot_preset(tmp_path)
    observed = {}

    def runner(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        output_dir = Path(kwargs["env"]["APEX_SYNTHETIC_TELEMETRY_DIR"])
        write_robot_capture(output_dir / "robot.csv")
        return SimpleNamespace(returncode=0)

    result = capture_synthetic_session(
        SyntheticCaptureConfig(torcs_binary, preset, count=1),
        runner=runner,
    )

    assert observed["command"] == [str(torcs_binary), "-r", str(preset.race_config)]
    assert observed["kwargs"]["check"] is False
    assert "shell" not in observed["kwargs"]
    assert "APEX_HUMAN_PARTICIPANT_ID" not in observed["kwargs"]["env"]
    assert "APEX_HUMAN_PHASE" not in observed["kwargs"]["env"]
    assert result.session_id == "SIM-BERNIW9-001"
    assert len(result.run_dirs) == 1

    (meta,) = list_runs()
    assert meta.capture == "synthetic-robot"
    assert meta.laps_seen == (1, 2, 3)
    assert load_run(meta.run_id).df["driver_index"].unique().tolist() == [9]

    manifest = json.loads((result.capture_dir / "manifest.json").read_text("utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["session_id"] == "SIM-BERNIW9-001"
    assert manifest["phase"] == "reference-pilot"
    assert manifest["capture"] == "synthetic-robot"
    assert manifest["robot"] == {
        "module": "berniw",
        "index": 9,
        "car_id": "car7-trb1",
    }
    assert manifest["study_preset"]["laps"] == 3
    assert manifest["command"] == observed["command"]
    assert manifest["runs"][0]["run_id"] == meta.run_id
    assert manifest["runs"][0]["complete_laps"] == [1, 2, 3]
    assert len(manifest["runs"][0]["sha256"]) == 64


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"schema_version": "apex-human-v1"}, "schema_version"),
        ({"driver_module": "human"}, "driver_module"),
        ({"driver_index": 8}, "driver_index"),
        ({"car_model": "car1-trb1"}, "car_model"),
        ({"track_id": "g-track-2"}, "track_internal_name"),
        ({"initial_remaining_laps": 5}, "remaining_laps"),
        ({"completed_laps": 2}, "complete laps"),
    ],
)
def test_single_session_rejects_mismatched_robot_evidence(
    torcs_binary, tmp_path, overrides, message
):
    def runner(_command, **kwargs):
        output_dir = Path(kwargs["env"]["APEX_SYNTHETIC_TELEMETRY_DIR"])
        write_robot_capture(output_dir / "robot.csv", **overrides)
        return SimpleNamespace(returncode=0)

    with pytest.raises(SyntheticCaptureError, match=message) as failure:
        capture_synthetic_session(
            SyntheticCaptureConfig(torcs_binary, robot_preset(tmp_path), count=1),
            runner=runner,
        )

    manifest = json.loads((failure.value.capture_dir / "manifest.json").read_text("utf-8"))
    assert manifest["status"] == "invalid_telemetry"
    assert list_runs() == []


def test_all_robot_outputs_are_validated_before_any_run_is_registered(
    torcs_binary, tmp_path
):
    def runner(_command, **kwargs):
        output_dir = Path(kwargs["env"]["APEX_SYNTHETIC_TELEMETRY_DIR"])
        write_robot_capture(output_dir / "valid.csv")
        (output_dir / "invalid.csv").write_text("not,telemetry\n1,2\n", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    with pytest.raises(SyntheticCaptureError, match="invalid.csv"):
        capture_synthetic_session(
            SyntheticCaptureConfig(torcs_binary, robot_preset(tmp_path), count=1),
            runner=runner,
        )

    assert list_runs() == []


def test_three_distance_segments_with_duplicate_lap_labels_are_rejected(
    torcs_binary, tmp_path
):
    def runner(_command, **kwargs):
        output_dir = Path(kwargs["env"]["APEX_SYNTHETIC_TELEMETRY_DIR"])
        path = output_dir / "robot.csv"
        write_robot_capture(path)
        frame = pd.read_csv(path)
        frame["race_lap"] = 1
        frame.to_csv(path, index=False)
        return SimpleNamespace(returncode=0)

    with pytest.raises(SyntheticCaptureError, match="distinct lap labels"):
        capture_synthetic_session(
            SyntheticCaptureConfig(torcs_binary, robot_preset(tmp_path), count=1),
            runner=runner,
        )

    assert list_runs() == []


def test_single_session_records_simulator_failure_and_cancellation(
    torcs_binary, tmp_path
):
    config = SyntheticCaptureConfig(torcs_binary, robot_preset(tmp_path), count=1)
    with pytest.raises(SyntheticCaptureError, match="status 7") as failed:
        capture_synthetic_session(
            config,
            runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=7),
        )
    failed_manifest = json.loads(
        (failed.value.capture_dir / "manifest.json").read_text("utf-8")
    )
    assert failed_manifest["status"] == "simulator_failed"

    with pytest.raises(SyntheticCaptureCancelled, match="stopped") as cancelled:
        capture_synthetic_session(
            config,
            runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=-15),
            stop_requested=lambda: True,
        )
    cancelled_manifest = json.loads(
        (cancelled.value.capture_dir / "manifest.json").read_text("utf-8")
    )
    assert cancelled_manifest["status"] == "cancelled"
    assert list_runs() == []


def test_successful_exit_without_robot_rows_is_a_readable_failure(
    torcs_binary, tmp_path
):
    with pytest.raises(SyntheticCaptureError, match="no non-empty telemetry CSV") as failure:
        capture_synthetic_session(
            SyntheticCaptureConfig(torcs_binary, robot_preset(tmp_path), count=1),
            runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
        )

    manifest = json.loads((failure.value.capture_dir / "manifest.json").read_text("utf-8"))
    assert manifest["status"] == "no_data"
    assert list_runs() == []


def test_batch_runs_three_isolated_sessions_and_records_progress(
    torcs_binary, tmp_path
):
    output_dirs = []
    progress = []

    def runner(_command, **kwargs):
        output_dir = Path(kwargs["env"]["APEX_SYNTHETIC_TELEMETRY_DIR"])
        output_dirs.append(output_dir)
        write_robot_capture(output_dir / "robot.csv")
        return SimpleNamespace(returncode=0)

    result = capture_synthetic_batch(
        SyntheticCaptureConfig(torcs_binary, robot_preset(tmp_path)),
        runner=runner,
        on_progress=progress.append,
    )

    assert len(set(output_dirs)) == 3
    assert [outcome.session_id for outcome in result.outcomes] == [
        "SIM-BERNIW9-001",
        "SIM-BERNIW9-002",
        "SIM-BERNIW9-003",
    ]
    assert [outcome.status for outcome in result.outcomes] == [
        "complete",
        "complete",
        "complete",
    ]
    assert [snapshot.status for snapshot in progress] == [
        "running",
        "complete",
        "running",
        "complete",
        "running",
        "complete",
    ]
    assert len(list_runs()) == 3

    manifest = json.loads((result.batch_dir / "batch-manifest.json").read_text("utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["count"] == 3
    assert [item["session_id"] for item in manifest["sessions"]] == [
        "SIM-BERNIW9-001",
        "SIM-BERNIW9-002",
        "SIM-BERNIW9-003",
    ]


def test_batch_failure_is_isolated_and_later_session_still_runs(
    torcs_binary, tmp_path
):
    calls = 0

    def runner(_command, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            return SimpleNamespace(returncode=7)
        output_dir = Path(kwargs["env"]["APEX_SYNTHETIC_TELEMETRY_DIR"])
        write_robot_capture(output_dir / "robot.csv")
        return SimpleNamespace(returncode=0)

    result = capture_synthetic_batch(
        SyntheticCaptureConfig(torcs_binary, robot_preset(tmp_path)),
        runner=runner,
    )

    assert calls == 3
    assert [outcome.status for outcome in result.outcomes] == [
        "complete",
        "failed",
        "complete",
    ]
    assert len(list_runs()) == 2
    manifest = json.loads((result.batch_dir / "batch-manifest.json").read_text("utf-8"))
    assert manifest["status"] == "complete_with_failures"
    assert "status 7" in manifest["sessions"][1]["error"]


def test_run_store_failure_is_audited_and_isolated(
    torcs_binary, tmp_path, monkeypatch
):
    from racecoach.telemetry import synthetic_capture as capture_module

    real_import = capture_module.import_run
    imports = 0

    def flaky_import(path, *, capture):
        nonlocal imports
        imports += 1
        if imports == 2:
            raise OSError("run store unavailable")
        return real_import(path, capture=capture)

    def runner(_command, **kwargs):
        output_dir = Path(kwargs["env"]["APEX_SYNTHETIC_TELEMETRY_DIR"])
        write_robot_capture(output_dir / "robot.csv")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(capture_module, "import_run", flaky_import)
    result = capture_synthetic_batch(
        SyntheticCaptureConfig(torcs_binary, robot_preset(tmp_path)),
        runner=runner,
    )

    assert [outcome.status for outcome in result.outcomes] == [
        "complete",
        "failed",
        "complete",
    ]
    failed_manifest = json.loads(
        (result.outcomes[1].capture_dir / "manifest.json").read_text("utf-8")
    )
    assert failed_manifest["status"] == "registration_failed"
    assert "run store unavailable" in failed_manifest["error"]
    assert len(list_runs()) == 2


def test_synthetic_capture_refuses_a_symlinked_storage_root(
    torcs_binary, tmp_path
):
    workspace_path = Path(os.environ["APEX_WORKSPACE"])
    captures = workspace_path / "captures"
    captures.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (captures / "synthetic").symlink_to(outside, target_is_directory=True)
    launched = False

    def runner(*_args, **_kwargs):
        nonlocal launched
        launched = True

    with pytest.raises(ValueError, match="symbolic link|outside"):
        capture_synthetic_batch(
            SyntheticCaptureConfig(torcs_binary, robot_preset(tmp_path)),
            runner=runner,
        )

    assert not launched
    assert list(outside.iterdir()) == []


def test_batch_cancellation_prevents_later_sessions_starting(
    torcs_binary, tmp_path
):
    calls = 0
    stopped = False

    def runner(_command, **_kwargs):
        nonlocal calls, stopped
        calls += 1
        stopped = True
        return SimpleNamespace(returncode=-15)

    result = capture_synthetic_batch(
        SyntheticCaptureConfig(torcs_binary, robot_preset(tmp_path)),
        runner=runner,
        stop_requested=lambda: stopped,
    )

    assert calls == 1
    assert [outcome.status for outcome in result.outcomes] == [
        "cancelled",
        "not_started",
        "not_started",
    ]
    assert list_runs() == []
    manifest = json.loads((result.batch_dir / "batch-manifest.json").read_text("utf-8"))
    assert manifest["status"] == "cancelled"


def test_managed_synthetic_runner_terminates_the_active_process():
    wait_started = threading.Event()
    terminated = threading.Event()

    class FakeProcess:
        returncode = None

        def wait(self):
            wait_started.set()
            assert terminated.wait(1.0)
            self.returncode = -15
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            terminated.set()

    process = FakeProcess()
    runner = ManagedSyntheticTorcsRunner(popen_factory=lambda *a, **k: process)
    result = []
    thread = threading.Thread(
        target=lambda: result.append(runner(["torcs"], env={}, check=False))
    )
    thread.start()
    assert wait_started.wait(1.0)

    runner.request_stop()
    thread.join(1.0)

    assert not thread.is_alive()
    assert result[0].returncode == -15


def test_capture_synthetic_cli_builds_the_pinned_batch_config(
    torcs_binary, tmp_path, monkeypatch, capsys
):
    observed = {}
    batch_dir = tmp_path / "synthetic-batch"

    def fake_capture(config):
        observed["config"] = config
        batch_dir.mkdir()
        return SimpleNamespace(
            batch_dir=batch_dir,
            outcomes=(
                SimpleNamespace(
                    session_id="SIM-BERNIW9-001",
                    status="complete",
                    run_dirs=(tmp_path / "run-1",),
                    error=None,
                ),
            ),
        )

    monkeypatch.setattr(
        "racecoach.telemetry.synthetic_capture.capture_synthetic_batch", fake_capture
    )
    workspace_override = tmp_path / "research-workspace"
    original_workspace = os.environ["APEX_WORKSPACE"]

    assert (
        main(
            [
                "capture-synthetic",
                "--count",
                "1",
                "--torcs",
                str(torcs_binary),
                "--workspace",
                str(workspace_override),
            ]
        )
        == 0
    )

    config = observed["config"]
    assert config.count == 1
    assert config.preset.laps == 3
    assert config.robot == RobotIdentity("berniw", 9, "car7-trb1")
    assert "Synthetic batch" in capsys.readouterr().out
    assert os.environ["APEX_WORKSPACE"] == original_workspace


def test_capture_synthetic_cli_help_exposes_default_three(capsys):
    with pytest.raises(SystemExit) as stopped:
        main(["capture-synthetic", "--help"])

    assert stopped.value.code == 0
    help_text = capsys.readouterr().out
    assert "--count" in help_text and "default: 3" in help_text
