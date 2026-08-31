"""Translating a capture for the team's Coach server, and getting it there."""

import hashlib
import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd
import pytest

from f1coach_core.ibmf1 import (
    GRAVITY_MPS2,
    REQUIRED_COLUMNS,
    SIDECAR_COLUMNS,
    SIDECAR_FPS,
    SIDECAR_NAME,
    UNAVAILABLE_CHANNELS,
    Bundle,
    IbmF1ExportError,
    package,
    study_arm_for,
    to_ibmf1_columns,
)
from f1coach_core.participant import Background, save_background
from racecoach.telemetry import ibmf1_upload
from racecoach.telemetry.ibmf1_upload import (
    Endpoint,
    IbmF1UploadError,
    deliver,
    endpoint_from_env,
)

# The columns an Apex human capture actually writes that this translation reads.
# Deliberately not the whole 106: a fixture that repeats the recorder's header
# would pass even if the translation stopped reading any of them.
SOURCE_COLUMNS = [
    *REQUIRED_COLUMNS,
    "wall_clock_s",
    "track_width_m",
    "accel_body_x_mps2",
    "accel_body_y_mps2",
    "accel_body_z_mps2",
    "car_name",
]


# Epoch seconds, because that is what the recorder stamps. The recording in the
# fixtures below is placed against this, the way a real one is.
FIRST_SAMPLE_CLOCK = 1787735531.0


def _frame(rows: int = 3, *, start_clock: float = FIRST_SAMPLE_CLOCK) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "sim_time_s": [0.02 * n for n in range(rows)],
            "wall_clock_s": [start_clock + 0.02 * n for n in range(rows)],
            "race_lap": [1] * rows,
            "speed_body_x_mps": [40.0 + n for n in range(rows)],
            "pos_x_m": [100.0 + n for n in range(rows)],
            "pos_y_m": [-20.0 - n for n in range(rows)],
            "track_seg_type": [3] * rows,
            "track_width_m": [12.0] * rows,
            "accel_body_x_mps2": [9.80665] * rows,
            "accel_body_y_mps2": [-19.6133] * rows,
            "accel_body_z_mps2": [0.0] * rows,
            "car_name": ["Player"] * rows,
        }
    )


@pytest.fixture
def capture(tmp_path, monkeypatch):
    """A capture folder shaped like the ones the study actually produced."""
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    directory = tmp_path / "B0826-baseline-20260826-091209"
    directory.mkdir()
    _frame(5).to_csv(directory / "human-1-1787735531-9624-1.csv", index=False)
    # The race that was started and abandoned: a header and nothing else.
    pd.DataFrame(columns=SOURCE_COLUMNS).to_csv(
        directory / "human-1-1787736065-9624-1.csv", index=False
    )
    (directory / "session.mp4").write_bytes(b"not really an mp4")
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "apex-human-capture-v1",
                "participant_id": "B0826",
                "phase": "baseline",
                "started_at": "2026-08-26T09:12:09+00:00",
                "finished_at": "2026-08-26T09:21:13+00:00",
                "study_preset": {
                    "track_id": "aalborg",
                    "car_id": "car7-trb1",
                    "laps": 3,
                    "window_width": 1280,
                    "window_height": 720,
                },
                # Half a second of video over five samples: one frame of it lands
                # inside the telemetry at the ten frames a second the index is
                # written at, which is what makes this bundle importable at all.
                "recording": {
                    "path": r"C:\Users\admin\Apex\captures\session.mp4",
                    "started_at": FIRST_SAMPLE_CLOCK - 0.02,
                    "duration_s": 0.5,
                },
                "runs": [
                    {
                        "file": "human-1-1787735531-9624-1.csv",
                        "sha256": "unused-here",
                        "samples": 5,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return directory


def _members(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


def _session(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("session.json"))


# --- the translation -------------------------------------------------------


def test_every_column_their_pipeline_reads_is_present_afterwards():
    translated = to_ibmf1_columns(_frame())

    assert set(REQUIRED_COLUMNS).issubset(translated.columns)
    assert "track_seg_width_m" in translated.columns
    for axis in "xyz":
        assert f"accel_body_{axis}_g" in translated.columns
    for column in UNAVAILABLE_CHANNELS:
        assert column in translated.columns


def test_the_renamed_and_converted_columns_carry_the_measured_values():
    translated = to_ibmf1_columns(_frame())

    assert translated["track_seg_width_m"].tolist() == [12.0, 12.0, 12.0]
    assert translated["accel_body_x_g"].iloc[0] == pytest.approx(1.0)
    assert translated["accel_body_y_g"].iloc[0] == pytest.approx(-19.6133 / GRAVITY_MPS2)


def test_channels_this_recorder_cannot_obtain_are_empty_rather_than_zero():
    """A zero here would be a measurement nobody made.

    Their pipeline computes quantiles and means over these; NaN is skipped,
    while 0.0 would enter every statistic and could fire a detector.
    """
    translated = to_ibmf1_columns(_frame())

    for column in UNAVAILABLE_CHANNELS:
        assert translated[column].isna().all()


def test_a_column_the_recorder_does_measure_is_not_overwritten():
    frame = _frame()
    frame["fr_slip_angle_rad"] = [0.11, 0.12, 0.13]

    translated = to_ibmf1_columns(frame)

    assert translated["fr_slip_angle_rad"].tolist() == [0.11, 0.12, 0.13]


def test_telemetry_their_importer_would_ignore_is_refused_here_instead():
    frame = _frame().drop(columns=["track_seg_type"])

    with pytest.raises(IbmF1ExportError, match="track_seg_type"):
        to_ibmf1_columns(frame)


# --- which study group the race is declared to be in -----------------------


@pytest.mark.parametrize(
    ("phase", "arm"),
    [
        ("coached", "coached"),
        ("baseline", "baseline"),
        ("control", "control"),
        ("Coached", "coached"),
        (None, "unknown"),
        ("", "unknown"),
    ],
)
def test_only_a_coached_phase_is_declared_coachable(phase, arm):
    assert study_arm_for(phase) == arm


def test_a_capture_with_no_phase_still_declares_an_arm(capture):
    """An absent arm is the one value their server reads as "coaching is fine"."""
    manifest = json.loads((capture / "manifest.json").read_text())
    del manifest["phase"]
    (capture / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    bundle = package(capture, capture.parent / "out.zip")

    assert bundle.study_arm == "unknown"
    assert _session(bundle.path)["study_arm"] == "unknown"


def test_a_baseline_race_is_not_sent_as_coachable(capture):
    bundle = package(capture, capture.parent / "out.zip")

    assert bundle.study_arm == "baseline"
    assert _session(bundle.path)["attempt_label"] == "baseline"


# --- what ends up in the bundle -------------------------------------------


def test_a_bundle_carries_the_telemetry_the_video_and_one_session_json(capture):
    bundle = package(capture, capture.parent / "out.zip")

    members = _members(bundle.path)
    assert "session.json" in members
    assert "session.mp4" in members
    assert "human-1-1787735531-9624-1.csv" in members
    assert bundle.has_video is True
    assert isinstance(bundle, Bundle)


def test_the_race_that_was_abandoned_before_the_first_sample_is_not_sent(capture):
    """Their importer reads a header alone as a second run and shows it as a race."""
    bundle = package(capture, capture.parent / "out.zip")

    assert "human-1-1787736065-9624-1.csv" not in _members(bundle.path)
    assert bundle.runs == ("human-1-1787735531-9624-1",)
    assert bundle.focus_run_id == "human-1-1787735531-9624-1"
    assert bundle.rows == 5


def test_this_machine_and_its_paths_do_not_travel_with_the_race(capture):
    """The capture manifest names a local drive and user account; nothing needs it."""
    bundle = package(capture, capture.parent / "out.zip")

    members = _members(bundle.path)
    assert "manifest.json" not in members
    assert "handover.json" not in members
    assert "exposure.jsonl" not in members


def test_the_questionnaire_travels_when_there_is_one(capture):
    save_background(Background(participant_id="B0826", age_band="18-24"))

    bundle = package(capture, capture.parent / "out.zip")

    with zipfile.ZipFile(bundle.path) as archive:
        assert json.loads(archive.read("participant.json"))["age_band"] == "18-24"


def test_a_questionnaire_that_travelled_in_the_folder_is_not_dropped(capture):
    """An unpacked handover carries it in the folder; this machine may never have adopted it."""
    (capture / "participant.json").write_text(
        json.dumps({"participant_id": "B0826", "sim_racing": "casual"}), encoding="utf-8"
    )

    bundle = package(capture, capture.parent / "out.zip")

    with zipfile.ZipFile(bundle.path) as archive:
        assert json.loads(archive.read("participant.json"))["sim_racing"] == "casual"


def test_the_questionnaire_can_be_held_back(capture):
    save_background(Background(participant_id="B0826", age_band="18-24"))

    bundle = package(capture, capture.parent / "out.zip", include_background=False)

    assert "participant.json" not in _members(bundle.path)


def test_the_recording_can_be_left_out_to_fit_a_body_limit(capture):
    bundle = package(capture, capture.parent / "out.zip", include_video=False)

    assert "session.mp4" not in _members(bundle.path)
    assert bundle.has_video is False
    assert _session(bundle.path)["media"] == "telemetry_only"


def test_the_sent_file_can_be_tied_back_to_the_one_this_workspace_measured(capture):
    """The bundle carries a translated CSV, so its digest is not the source's."""
    source = capture / "human-1-1787735531-9624-1.csv"
    expected = hashlib.sha256(source.read_bytes()).hexdigest()

    session = _session(package(capture, capture.parent / "out.zip").path)

    assert session["apex_source_sha256"] == expected
    assert session["telemetry_sha256"] != expected
    with zipfile.ZipFile(capture.parent / "out.zip") as archive:
        sent = archive.read("human-1-1787735531-9624-1.csv")
    assert session["telemetry_sha256"] == hashlib.sha256(sent).hexdigest()
    assert session["apex_empty_columns"] == sorted(UNAVAILABLE_CHANNELS)


def test_a_capture_with_no_participant_id_is_refused(capture):
    (capture / "manifest.json").write_text(json.dumps({"phase": "baseline"}), encoding="utf-8")

    with pytest.raises(IbmF1ExportError, match="participant id"):
        package(capture, capture.parent / "out.zip")


def test_a_capture_whose_race_produced_nothing_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    directory = tmp_path / "C0826-control-20260826-102001"
    directory.mkdir()
    pd.DataFrame(columns=SOURCE_COLUMNS).to_csv(directory / "human-1.csv", index=False)
    (directory / "manifest.json").write_text(
        json.dumps({"participant_id": "C0826", "phase": "control"}), encoding="utf-8"
    )

    with pytest.raises(IbmF1ExportError, match="no completed telemetry"):
        package(directory, tmp_path / "out.zip")


def test_a_folder_that_is_not_a_capture_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    directory = tmp_path / "empty"
    directory.mkdir()

    with pytest.raises(IbmF1ExportError, match="manifest.json"):
        package(directory, tmp_path / "out.zip")


# --- the frame index -------------------------------------------------------


@pytest.fixture
def long_capture(tmp_path, monkeypatch):
    """Ten seconds of telemetry inside a twelve-second recording.

    Long enough that the index has an interior to check, and offset so that the
    recording starts a second before the first sample and runs on after the
    last -- which is what a real one does, the recorder being started before
    TORCS and stopped after it.
    """
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    directory = tmp_path / "B0826-coached-20260826-095423"
    directory.mkdir()
    _frame(500).to_csv(directory / "human-1-1787735531-9624-1.csv", index=False)
    (directory / "session.mp4").write_bytes(b"not really an mp4")
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "apex-human-capture-v1",
                "participant_id": "B0826",
                "phase": "coached",
                "started_at": "2026-08-26T09:54:23+00:00",
                "study_preset": {"window_width": 1280, "window_height": 720},
                "runs": [{"file": "human-1-1787735531-9624-1.csv", "samples": 500}],
                "recording": {
                    "started_at": FIRST_SAMPLE_CLOCK - 1.0,
                    "duration_s": 12.0,
                },
            }
        ),
        encoding="utf-8",
    )
    return directory


def _index(bundle_path) -> pd.DataFrame:
    with zipfile.ZipFile(bundle_path) as archive:
        return pd.read_csv(io.BytesIO(archive.read(SIDECAR_NAME)))


def test_a_recording_travels_with_the_index_their_importer_demands(capture):
    """prepare_media raises "Captured media needs a *.frames.csv sidecar"."""
    bundle = package(capture, capture.parent / "out.zip")

    members = _members(bundle.path)
    assert SIDECAR_NAME in members
    # Their find_inputs refuses a bundle carrying more than one of these.
    assert len([name for name in members if name.endswith(".frames.csv")]) == 1
    assert list(_index(bundle.path).columns) == list(SIDECAR_COLUMNS)
    assert bundle.frames == 1


def test_a_frame_is_indexed_at_the_instant_the_recording_puts_it(long_capture):
    """frame n is started_at + n/fps, and its sim time is read off the telemetry."""
    bundle = package(long_capture, long_capture.parent / "out.zip")
    index = _index(bundle.path)

    # The recording leads the telemetry by a second, so the first frame that has
    # any simulation time to report is the tenth at ten frames a second.
    assert int(index["frame_id"].iloc[0]) == 10
    assert index["sim_time_s"].iloc[0] == pytest.approx(0.0, abs=1e-3)
    assert int(index["frame_id"].iloc[-1]) == 109
    assert index["sim_time_s"].iloc[-1] == pytest.approx(9.9, abs=1e-3)
    assert (index["frame_id"].diff().dropna() == 1).all()
    assert index["sim_time_s"].is_monotonic_increasing


def test_frames_taken_before_and_after_the_telemetry_are_left_out(long_capture):
    """Not pinned to the first and last sample: nobody measured those instants."""
    bundle = package(long_capture, long_capture.parent / "out.zip")
    index = _index(bundle.path)

    # 120 frames fit in twelve seconds; ten seconds of telemetry indexes 100.
    assert len(index) == 100
    assert bundle.frames == 100
    # frame_id stays the number their ffmpeg call will give the frame, so the
    # rows that remain still name files that exist.
    assert index["image_file"].iloc[0] == "torcs-0001-00000010.png"
    assert index["display_width"].iloc[0] == 1280


def test_the_bundle_declares_the_rate_it_indexed_the_recording_at(long_capture):
    """Their importer cuts frames at session.json's video_fps; the index assumes it."""
    bundle = package(long_capture, long_capture.parent / "out.zip")

    assert _session(bundle.path)["video_fps"] == SIDECAR_FPS


def test_a_race_sent_without_its_recording_declares_no_frame_rate(capture):
    """They read it as float(metadata.get(...)), which a null would not survive."""
    bundle = package(capture, capture.parent / "out.zip", include_video=False)

    assert "video_fps" not in _session(bundle.path)
    assert SIDECAR_NAME not in _members(bundle.path)
    assert bundle.frames == 0


def test_a_recording_with_no_recorded_start_is_refused_not_sent_unindexed(capture):
    """Sending it anyway fails their whole import, and the race with it."""
    manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
    manifest.pop("recording")
    (capture / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(IbmF1ExportError, match="--no-video"):
        package(capture, capture.parent / "out.zip")


def test_a_run_with_no_wall_clock_cannot_place_the_recording(capture):
    """The wall clock is the only thing the two share; without it there is no map."""
    telemetry = capture / "human-1-1787735531-9624-1.csv"
    pd.read_csv(telemetry).drop(columns=["wall_clock_s"]).to_csv(telemetry, index=False)

    with pytest.raises(IbmF1ExportError, match="wall_clock_s"):
        package(capture, capture.parent / "out.zip")

    # The measurements still travel; only the recording cannot.
    bundle = package(capture, capture.parent / "quiet.zip", include_video=False)
    assert bundle.has_video is False
    assert bundle.rows == 5


# --- delivering it ---------------------------------------------------------


class _Coach(BaseHTTPRequestHandler):
    """Enough of their import API to exercise the client's paths."""

    script: list = []
    seen: list = []

    def log_message(self, *args):  # noqa: D102 - keep the test output readable
        pass

    def _reply(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        type(self).seen.append({
            "bytes": len(body),
            "token": self.headers.get("X-Player-Upload-Token"),
            "ingest": self.headers.get("X-IBMF1-Ingest"),
            "agent": self.headers.get("User-Agent"),
        })
        status, payload = type(self).script.pop(0)
        self._reply(status, payload)

    def do_GET(self):
        self._reply(200, {"status": "complete", "message": "Stored.", "session": {"id": "abc"}})


@pytest.fixture
def coach():
    _Coach.script = []
    _Coach.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Coach)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, _Coach
    server.shutdown()
    server.server_close()


def _endpoint(server) -> Endpoint:
    host, port = server.server_address[:2]
    return Endpoint(origin=f"http://{host}:{port}", token="test-key", max_upload_bytes=10_000_000)


def test_a_bundle_is_sent_and_the_client_waits_for_the_review(coach, tmp_path):
    server, handler = coach
    handler.script = [(202, {"job_id": "job-1"})]
    archive = tmp_path / "b.zip"
    archive.write_bytes(b"zip bytes")

    delivered = deliver(archive, endpoint=_endpoint(server), sleep=lambda _s: None)

    assert delivered.duplicate is False
    assert delivered.session == {"id": "abc"}
    assert handler.seen[0]["token"] == "test-key"
    assert handler.seen[0]["ingest"] == "player-app"
    # Their CDN rejects urllib's default signature, which reads as a retryable
    # failure that never succeeds.
    assert handler.seen[0]["agent"] == "IBMF1-Player"


def test_a_race_already_on_the_site_is_reported_rather_than_treated_as_a_failure(coach, tmp_path):
    server, handler = coach
    handler.script = [(409, {"error": "duplicate", "session": {"id": "abc"}})]
    archive = tmp_path / "b.zip"
    archive.write_bytes(b"zip bytes")

    delivered = deliver(archive, endpoint=_endpoint(server), sleep=lambda _s: None)

    assert delivered.duplicate is True


def test_a_busy_server_is_retried_and_then_succeeds(coach, tmp_path):
    server, handler = coach
    handler.script = [(409, {"message": "busy"}), (202, {"job_id": "job-2"})]
    archive = tmp_path / "b.zip"
    archive.write_bytes(b"zip bytes")

    delivered = deliver(
        archive, endpoint=_endpoint(server), delays_s=(1,), sleep=lambda _s: None
    )

    assert delivered.duplicate is False
    assert len(handler.seen) == 2


def test_a_rejected_key_says_which_variable_to_check(coach, tmp_path):
    server, handler = coach
    handler.script = [(401, {})]
    archive = tmp_path / "b.zip"
    archive.write_bytes(b"zip bytes")

    with pytest.raises(IbmF1UploadError, match="IBMF1_UPLOAD_TOKEN"):
        deliver(archive, endpoint=_endpoint(server), sleep=lambda _s: None)


def test_an_oversized_bundle_is_refused_before_anything_is_sent(coach, tmp_path):
    server, handler = coach
    archive = tmp_path / "b.zip"
    archive.write_bytes(b"x" * 2048)
    endpoint = Endpoint(origin=_endpoint(server).origin, token="k", max_upload_bytes=1024)

    with pytest.raises(IbmF1UploadError, match="--no-video"):
        deliver(archive, endpoint=endpoint, sleep=lambda _s: None)
    assert handler.seen == []


def test_a_missing_key_is_an_error_with_instructions_not_an_anonymous_attempt():
    with pytest.raises(IbmF1UploadError, match="IBMF1_UPLOAD_TOKEN"):
        endpoint_from_env({})


def test_the_upload_host_defaults_to_the_one_that_is_not_behind_the_cdn():
    endpoint = endpoint_from_env({"IBMF1_UPLOAD_TOKEN": "k"})

    assert endpoint.origin == ibmf1_upload.DEFAULT_ORIGIN
    assert endpoint.import_url.endswith("/api/import")


def test_the_review_link_points_at_the_site_not_the_host_it_was_sent_to():
    """The import host runs no site: a driver sent there is shown nothing."""
    endpoint = endpoint_from_env({"IBMF1_UPLOAD_TOKEN": "k"})

    assert endpoint.import_url.startswith(ibmf1_upload.DEFAULT_ORIGIN)
    assert endpoint.review_url == "https://demo.lzqqq.org/"
    assert endpoint.review_origin != endpoint.origin
