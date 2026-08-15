"""Human TORCS capture orchestration and research-data failure paths."""

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from racecoach.telemetry.human_capture import (
    HumanCaptureCancelled,
    HumanCaptureConfig,
    HumanCaptureError,
    ManagedTorcsRunner,
    TorcsStudyPreset,
    capture_human_runs,
    default_torcs_binary,
)
from racecoach.telemetry.run_store import list_runs, load_run


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


@pytest.fixture
def torcs_binary(tmp_path) -> Path:
    binary = tmp_path / "torcs"
    binary.write_bytes(b"test executable placeholder")
    binary.chmod(0o700)
    return binary


def write_capture(
    path: Path,
    *,
    car_name: str = "Human, Driver",
    car_model: str = "car7-trb1",
    driver_module: str = "human",
    track_id: str = "g-track-1",
    remaining_laps: int = 5,
) -> None:
    path.write_text(
        "schema_version,sample,sim_time_s,dist_from_start_m,accel_cmd,brake_cmd,"
        "steer_cmd,gear,car_name,car_model,driver_module,track_internal_name,"
        "race_lap,remaining_laps,total_speed_mps\n"
        f'apex-human-v1,0,0.00,0.0,0.5,0.0,0.0,1,"{car_name}",'
        f"{car_model},{driver_module},{track_id},1,{remaining_laps},10.0\n"
        f'apex-human-v1,1,0.02,0.2,0.6,0.0,0.1,1,"{car_name}",'
        f"{car_model},{driver_module},{track_id},1,{remaining_laps},10.2\n",
        encoding="utf-8",
    )


def study_preset(tmp_path: Path) -> TorcsStudyPreset:
    race_config = tmp_path / "apexstudy.xml"
    race_config.write_text("<params name='Apex Study v1'/>", encoding="utf-8")
    return TorcsStudyPreset(
        preset_id="apex-study-v1",
        display_name="Apex Study v1",
        track_id="g-track-1",
        track_category="road",
        car_id="car7-trb1",
        laps=5,
        race_config=race_config,
    )


def test_default_torcs_binary_prefers_runtime_bundled_with_the_app(tmp_path, monkeypatch):
    bundle = tmp_path / "apex-bundle"
    binary = bundle / "torcs-runtime" / "bin" / "torcs"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"bundled simulator")
    binary.chmod(0o700)
    monkeypatch.delenv("TORCS_PREFIX", raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    assert default_torcs_binary() == binary


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("participant_id", "Alice Smith"),
        ("participant_id", "alice@example.com"),
        ("participant_id", "../P001"),
        ("phase", "baseline/../../"),
        ("phase", ""),
    ],
)
def test_capture_metadata_requires_safe_pseudonymous_slugs(torcs_binary, field, value):
    values = {
        "participant_id": "P001",
        "phase": "baseline",
        "torcs_binary": torcs_binary,
    }
    values[field] = value
    with pytest.raises(ValueError, match=field.replace("_", " ")):
        HumanCaptureConfig(**values)


def test_capture_launches_without_shell_and_registers_valid_csv(torcs_binary):
    observed = {}

    def runner(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        output_dir = Path(kwargs["env"]["APEX_HUMAN_TELEMETRY_DIR"])
        write_capture(output_dir / "human-1.csv")
        return SimpleNamespace(returncode=0)

    result = capture_human_runs(
        HumanCaptureConfig(
            participant_id="P001",
            phase="baseline",
            torcs_binary=torcs_binary,
            torcs_args=("-r", "practice.xml"),
        ),
        runner=runner,
    )

    assert observed["command"] == [str(torcs_binary), "-r", "practice.xml"]
    assert observed["kwargs"]["check"] is False
    assert "shell" not in observed["kwargs"]
    assert observed["kwargs"]["env"]["APEX_HUMAN_PARTICIPANT_ID"] == "P001"
    assert observed["kwargs"]["env"]["APEX_HUMAN_PHASE"] == "baseline"

    assert len(result.run_dirs) == 1
    (meta,) = list_runs()
    assert meta.capture == "human-driver"
    assert meta.n_samples == 2 and meta.cadence_hz == pytest.approx(50.0)
    assert load_run(meta.run_id).df["car_name"].iloc[0] == "Human, Driver"

    manifest = json.loads((result.capture_dir / "manifest.json").read_text("utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["participant_id"] == "P001" and manifest["phase"] == "baseline"
    assert manifest["runs"][0]["run_id"] == meta.run_id
    assert len(manifest["runs"][0]["sha256"]) == 64


def test_study_preset_launches_graphical_race_and_records_context(tmp_path, torcs_binary):
    observed = {}
    preset = study_preset(tmp_path)

    def runner(command, **kwargs):
        observed["command"] = command
        observed["env"] = kwargs["env"]
        output_dir = Path(kwargs["env"]["APEX_HUMAN_TELEMETRY_DIR"])
        write_capture(output_dir / "human-study.csv")
        return SimpleNamespace(returncode=0)

    result = capture_human_runs(
        HumanCaptureConfig(
            participant_id="P010",
            phase="baseline",
            torcs_binary=torcs_binary,
            preset=preset,
        ),
        runner=runner,
    )

    assert observed["command"] == [str(torcs_binary), "-R", str(preset.race_config)]
    assert Path(observed["env"]["APEX_TORCS_LOCAL_DIR"]).name == preset.preset_id
    manifest = json.loads((result.capture_dir / "manifest.json").read_text("utf-8"))
    assert manifest["study_preset"] == {
        "preset_id": "apex-study-v1",
        "display_name": "Apex Study v1",
        "track_id": "g-track-1",
        "track_category": "road",
        "car_id": "car7-trb1",
        "laps": 5,
        "race_config": str(preset.race_config),
    }
    assert manifest["command"] == [
        str(torcs_binary),
        "-R",
        str(preset.race_config),
    ]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("track_id", "wrong-track", "track_internal_name"),
        ("car_model", "wrong-car", "car_model"),
        ("driver_module", "robot", "driver_module"),
        ("remaining_laps", 7, "remaining_laps"),
    ],
)
def test_study_capture_rejects_telemetry_that_does_not_match_preset(
    tmp_path, torcs_binary, field, value, message
):
    preset = study_preset(tmp_path)

    def runner(_command, **kwargs):
        output_dir = Path(kwargs["env"]["APEX_HUMAN_TELEMETRY_DIR"])
        write_capture(output_dir / "human-study.csv", **{field: value})
        return SimpleNamespace(returncode=0)

    with pytest.raises(HumanCaptureError, match=message) as failure:
        capture_human_runs(
            HumanCaptureConfig("P012", "baseline", torcs_binary, preset=preset),
            runner=runner,
        )

    manifest = json.loads((failure.value.capture_dir / "manifest.json").read_text("utf-8"))
    assert manifest["status"] == "invalid_telemetry"
    assert list_runs() == []


def test_missing_study_preset_is_rejected_before_process_launch(tmp_path, torcs_binary):
    preset = TorcsStudyPreset(
        preset_id="apex-study-v1",
        display_name="Apex Study v1",
        track_id="g-track-1",
        track_category="road",
        car_id="car7-trb1",
        laps=5,
        race_config=tmp_path / "missing.xml",
    )
    launched = False

    def runner(*_args, **_kwargs):
        nonlocal launched
        launched = True

    with pytest.raises(ValueError, match="race configuration"):
        capture_human_runs(
            HumanCaptureConfig("P011", "baseline", torcs_binary, preset=preset),
            runner=runner,
        )

    assert not launched


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("preset_id", "../study"),
        ("track_id", "road/g-track-1"),
        ("car_id", "car 7"),
        ("laps", 0),
    ],
)
def test_study_preset_rejects_unsafe_or_impossible_values(tmp_path, field, value):
    values = {
        "preset_id": "apex-study-v1",
        "display_name": "Apex Study v1",
        "track_id": "g-track-1",
        "track_category": "road",
        "car_id": "car7-trb1",
        "laps": 5,
        "race_config": tmp_path / "apexstudy.xml",
    }
    values[field] = value

    with pytest.raises(ValueError, match=field.replace("_", " ")):
        TorcsStudyPreset(**values)


def test_study_preset_requires_xml_and_cannot_be_overridden_by_torcs_args(
    tmp_path, torcs_binary
):
    values = {
        "preset_id": "apex-study-v1",
        "display_name": "Apex Study v1",
        "track_id": "g-track-1",
        "track_category": "road",
        "car_id": "car7-trb1",
        "laps": 5,
    }
    with pytest.raises(ValueError, match="XML"):
        TorcsStudyPreset(**values, race_config=tmp_path / "preset.txt")

    preset = TorcsStudyPreset(**values, race_config=tmp_path / "preset.xml")
    with pytest.raises(ValueError, match="cannot be combined"):
        HumanCaptureConfig(
            "P013",
            "baseline",
            torcs_binary,
            torcs_args=("-Rother.xml",),
            preset=preset,
        )


def test_nonzero_torcs_exit_preserves_failed_manifest_without_registering(torcs_binary):
    def runner(_command, **_kwargs):
        return SimpleNamespace(returncode=7)

    with pytest.raises(HumanCaptureError, match="status 7") as failure:
        capture_human_runs(
            HumanCaptureConfig("P002", "coached", torcs_binary),
            runner=runner,
        )

    manifest = json.loads((failure.value.capture_dir / "manifest.json").read_text("utf-8"))
    assert manifest["status"] == "simulator_failed" and manifest["returncode"] == 7
    assert list_runs() == []


def test_requested_stop_is_recorded_as_cancelled_not_simulator_failure(torcs_binary):
    with pytest.raises(HumanCaptureCancelled, match="stopped by the user") as failure:
        capture_human_runs(
            HumanCaptureConfig("P002", "coached", torcs_binary),
            runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=-15),
            stop_requested=lambda: True,
        )

    manifest = json.loads((failure.value.capture_dir / "manifest.json").read_text("utf-8"))
    assert manifest["status"] == "cancelled" and manifest["returncode"] == -15
    assert list_runs() == []


def test_managed_runner_terminates_the_active_torcs_process():
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
    runner = ManagedTorcsRunner(popen_factory=lambda *a, **k: process)
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


def test_successful_torcs_exit_without_rows_is_a_readable_failure(torcs_binary):
    def runner(_command, **kwargs):
        output_dir = Path(kwargs["env"]["APEX_HUMAN_TELEMETRY_DIR"])
        (output_dir / "human-empty.csv").write_text(
            "sim_time_s,dist_from_start_m,accel_cmd,brake_cmd,steer_cmd,gear\n"
        )
        return SimpleNamespace(returncode=0)

    with pytest.raises(HumanCaptureError, match="no non-empty telemetry CSV"):
        capture_human_runs(
            HumanCaptureConfig("P003", "baseline", torcs_binary),
            runner=runner,
        )
    assert list_runs() == []


def test_all_outputs_are_validated_before_any_run_is_registered(torcs_binary):
    def runner(_command, **kwargs):
        output_dir = Path(kwargs["env"]["APEX_HUMAN_TELEMETRY_DIR"])
        write_capture(output_dir / "human-valid.csv")
        (output_dir / "human-invalid.csv").write_text("not,the,telemetry,schema\n1,2,3,4\n")
        return SimpleNamespace(returncode=0)

    with pytest.raises(HumanCaptureError, match="human-invalid.csv"):
        capture_human_runs(
            HumanCaptureConfig("P004", "baseline", torcs_binary),
            runner=runner,
        )
    assert list_runs() == []
