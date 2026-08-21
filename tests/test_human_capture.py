"""Human TORCS capture orchestration and research-data failure paths."""

import json
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
)
from racecoach.telemetry.run_store import list_runs, load_run
from racecoach.telemetry.torcs_runtime import torcs_launch_cwd


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
        f'apex-human-v2,0,0.00,0.0,0.5,0.0,0.0,1,"{car_name}",'
        f"{car_model},{driver_module},{track_id},1,{remaining_laps},10.0\n"
        f'apex-human-v2,1,0.02,0.2,0.6,0.0,0.1,1,"{car_name}",'
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
    # TORCS must start in its own data root: the Visual Studio build seeds a new
    # profile from the relative path config/raceman, so launching from elsewhere
    # leaves that profile without any race managers.
    assert observed["kwargs"]["cwd"] == str(torcs_launch_cwd(torcs_binary))
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
        "window_width": preset.window_width,
        "window_height": preset.window_height,
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


def test_a_crash_that_produced_nothing_still_fails_and_registers_nothing(torcs_binary):
    def runner(_command, **_kwargs):
        return SimpleNamespace(returncode=7)

    with pytest.raises(HumanCaptureError, match="status 7") as failure:
        capture_human_runs(
            HumanCaptureConfig("P002", "coached", torcs_binary),
            runner=runner,
        )

    manifest = json.loads((failure.value.capture_dir / "manifest.json").read_text("utf-8"))
    assert manifest["status"] == "no_data" and manifest["returncode"] == 7
    assert list_runs() == []


def test_a_crash_on_the_way_out_does_not_discard_a_completed_drive(torcs_binary):
    """TORCS can die closing its windows after the participant drove everything.

    0xC0000005 at shutdown says nothing about whether the laps were driven, and
    the telemetry is the evidence. Losing a participant's session to it is the
    expensive failure, so the data decides and the abnormal exit is recorded.
    """

    def runner(_command, **kwargs):
        output_dir = Path(kwargs["env"]["APEX_HUMAN_TELEMETRY_DIR"])
        write_capture(output_dir / "human-1.csv")
        return SimpleNamespace(returncode=3221225477)  # STATUS_ACCESS_VIOLATION

    result = capture_human_runs(
        HumanCaptureConfig("Y001", "baseline", torcs_binary),
        runner=runner,
    )

    assert len(result.run_dirs) == 1
    (meta,) = list_runs()
    assert meta.capture == "human-driver" and meta.driver == "Y001"

    manifest = json.loads((result.capture_dir / "manifest.json").read_text("utf-8"))
    assert manifest["status"] == "complete_after_abnormal_exit"
    assert manifest["returncode"] == 3221225477
    assert manifest["runs"][0]["run_id"] == meta.run_id


def test_a_crash_part_way_through_is_still_rejected(torcs_binary):
    """Salvaging data must not mean accepting a drive that did not happen."""
    preset = None

    def runner(_command, **kwargs):
        output_dir = Path(kwargs["env"]["APEX_HUMAN_TELEMETRY_DIR"])
        (output_dir / "human-1.csv").write_text(
            "schema_version,sample,sim_time_s,dist_from_start_m,accel_cmd,brake_cmd,"
            "steer_cmd,gear,car_name,car_model,driver_module,track_internal_name,"
            "race_lap,remaining_laps,total_speed_mps\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=3221225477)

    with pytest.raises(HumanCaptureError, match="status 3221225477") as failure:
        capture_human_runs(
            HumanCaptureConfig("Y002", "baseline", torcs_binary, preset=preset),
            runner=runner,
        )

    assert list_runs() == []
    manifest = json.loads((failure.value.capture_dir / "manifest.json").read_text("utf-8"))
    assert manifest["status"] == "no_data"


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


def test_study_session_fixes_the_torcs_window_size_before_launch(tmp_path, torcs_binary):
    """640x480 is too small to place a car, and maximising it does not work."""
    # Lay out a runtime the way the autotools install does, with TORCS's own
    # screen.xml as the source to be adapted.
    data_root = torcs_binary.parent.parent / "share" / "games" / "torcs"
    (data_root / "config" / "raceman").mkdir(parents=True)
    (data_root / "config" / "screen.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<params name="screen" type="params" mode="mw">\n'
        '  <section name="Screen Properties">\n'
        '    <attnum name="x" val="640"/>\n'
        '    <attnum name="y" val="480"/>\n'
        '    <attstr name="fullscreen" in="yes,no" val="no"/>\n'
        "  </section>\n"
        '  <section name="Menu Font">\n'
        '    <attnum name="size big" val="16"/>\n'
        "  </section>\n"
        "</params>\n",
        encoding="utf-8",
    )
    preset = study_preset(tmp_path)

    def runner(_command, **kwargs):
        output_dir = Path(kwargs["env"]["APEX_HUMAN_TELEMETRY_DIR"])
        write_capture(output_dir / "human-study.csv")
        return SimpleNamespace(returncode=0)

    capture_human_runs(
        HumanCaptureConfig("P011", "baseline", torcs_binary, preset=preset),
        runner=runner,
    )

    from f1coach_core.workspace import workspace_root

    written = (
        workspace_root() / "torcs-profiles" / preset.preset_id / "config" / "screen.xml"
    ).read_text(encoding="utf-8")
    assert f'<attnum name="x" val="{preset.window_width}"/>' in written
    assert f'<attnum name="y" val="{preset.window_height}"/>' in written
    # Only the window size changes; the rest of TORCS's configuration survives.
    assert '<attnum name="size big" val="16"/>' in written
    assert 'name="fullscreen" in="yes,no" val="no"' in written


def test_a_missing_screen_config_never_costs_a_participant_their_session(
    tmp_path, torcs_binary
):
    """No runtime screen.xml to adapt: run anyway, at TORCS's own default size."""
    preset = study_preset(tmp_path)

    def runner(_command, **kwargs):
        output_dir = Path(kwargs["env"]["APEX_HUMAN_TELEMETRY_DIR"])
        write_capture(output_dir / "human-study.csv")
        return SimpleNamespace(returncode=0)

    result = capture_human_runs(
        HumanCaptureConfig("P013", "baseline", torcs_binary, preset=preset),
        runner=runner,
    )

    assert len(result.run_dirs) == 1


def test_a_simulator_that_never_started_tells_the_participant_to_retry(
    torcs_binary, tmp_path
):
    """No file at all means it never reached a race, which is a retry, not a redo."""

    def runner(_command, **_kwargs):
        return SimpleNamespace(returncode=3221225477)  # died before any race

    with pytest.raises(HumanCaptureError) as caught:
        capture_human_runs(
            HumanCaptureConfig("A001", "baseline", torcs_binary), runner=runner
        )
    message = str(caught.value)
    assert "start the session again" in message
    assert "Nothing was lost" in message
    # The exit code still travels, for whoever has to diagnose it later.
    assert "3221225477" in message
    assert list_runs() == []


def test_a_simulator_that_ran_but_recorded_nothing_says_so_instead(
    torcs_binary, tmp_path
):
    """An empty file means TORCS ran; the participant has to actually drive."""

    def runner(_command, **kwargs):
        output_dir = Path(kwargs["env"]["APEX_HUMAN_TELEMETRY_DIR"])
        # Header but no samples: TORCS started the recorder and nothing drove.
        (output_dir / "human-1.csv").write_text(
            "schema_version,sample,sim_time_s,dist_from_start_m,accel_cmd,brake_cmd,"
            "steer_cmd,gear,car_name,car_model,driver_module,track_internal_name,"
            "race_lap,remaining_laps,total_speed_mps\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    with pytest.raises(HumanCaptureError) as caught:
        capture_human_runs(
            HumanCaptureConfig("A002", "baseline", torcs_binary), runner=runner
        )
    assert "Select a human driver" in str(caught.value)


def test_recovering_a_capture_a_crash_left_behind(torcs_binary, tmp_path):
    """The folder on disk plus its manifest is enough to complete the session."""
    from racecoach.telemetry.human_capture import finish_capture, human_captures_root

    capture_dir = human_captures_root() / "Y001-baseline-20260819-085225"
    capture_dir.mkdir(parents=True)
    write_capture(capture_dir / "human-1.csv")
    (capture_dir / "manifest.json").write_text(
        json.dumps(
            {
                "participant_id": "Y001",
                "phase": "baseline",
                "study_preset": {"preset_id": "apex-study-v1"},
                "returncode": 3221225477,
                "status": "simulator_failed",
                "runs": [],
            }
        ),
        encoding="utf-8",
    )

    result = finish_capture(capture_dir)

    assert len(result.run_dirs) == 1
    (meta,) = list_runs()
    # The identity is recovered from the manifest, not re-typed by a facilitator.
    assert meta.driver == "Y001" and meta.phase == "baseline"
    assert meta.setup == "apex-study-v1"
    manifest = json.loads((capture_dir / "manifest.json").read_text("utf-8"))
    assert manifest["status"] == "complete_after_recovery"
    assert manifest["runs"][0]["run_id"] == meta.run_id


def test_recovery_refuses_a_folder_with_nothing_to_register(torcs_binary, tmp_path):
    from racecoach.telemetry.human_capture import finish_capture, human_captures_root

    capture_dir = human_captures_root() / "Y003-baseline-20260819-090000"
    capture_dir.mkdir(parents=True)
    (capture_dir / "manifest.json").write_text(
        json.dumps({"participant_id": "Y003", "phase": "baseline"}), encoding="utf-8"
    )

    with pytest.raises(HumanCaptureError, match="no usable telemetry"):
        finish_capture(capture_dir)
    assert list_runs() == []


def test_the_study_assignment_matches_the_race_configuration_it_ships(torcs_binary):
    """A preset that names a different track from the XML would silently mislead."""
    from racecoach.telemetry.human_capture import default_study_preset

    preset = default_study_preset(torcs_binary)
    xml = Path("integrations/torcs-1.3.9/overlay/src/raceman/apexstudy.xml").read_text("utf-8")

    assert f'val="{preset.track_id}"' in xml
    assert f'name="laps" val="{preset.laps}"' in xml


def test_every_participant_is_assigned_the_same_car(torcs_binary):
    """An uncontrolled car would confound the comparison the study is built on.

    The raceman does not choose the human's car -- TORCS takes it from the driver
    profile -- so the control lives in the shipped human.xml that each fresh
    profile is seeded from, and the preset must agree with it.
    """
    from racecoach.telemetry.human_capture import default_study_preset
    from racecoach.telemetry.torcs_runtime import torcs_data_root

    preset = default_study_preset(torcs_binary)
    template = torcs_data_root(torcs_binary) / "drivers" / "human" / "human.xml"
    if not template.is_file():
        pytest.skip("no installed TORCS runtime on this machine")
    assert f'val="{preset.car_id}"' in template.read_text("latin-1")


def test_damage_is_recorded_but_can_never_end_a_participants_session():
    """Incidents are a measurement. Being retired by them would destroy the data."""
    xml = Path("integrations/torcs-1.3.9/overlay/src/raceman/apexstudy.xml").read_text("utf-8")

    assert 'name="damage factor" val="1"' in xml  # damage accrues, so it can be counted
    assert 'name="maximum dammage" val="0"' in xml  # 0 disables retirement in simu.cpp
