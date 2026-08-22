"""Recording the race window: what it captures, and what it refuses to."""

import subprocess
from pathlib import Path

import pytest

from racecoach.telemetry import screen_capture as sc


class FakeProcess:
    def __init__(self, *, dies_with: int | None = None) -> None:
        self.returncode = dies_with
        self.stdin = FakeStdin()
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):  # pragma: no cover - only for a hung encoder
        self.killed = True
        self.returncode = -9


class FakeStdin:
    def __init__(self) -> None:
        self.written = b""
        self.closed = False

    def write(self, data):
        self.written += data

    def flush(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def ffmpeg(tmp_path, monkeypatch):
    binary = tmp_path / "ffmpeg"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setenv("APEX_FFMPEG", str(binary))
    return binary


def test_a_build_without_ffmpeg_reports_it_and_records_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_FFMPEG", str(tmp_path / "absent"))
    recorder = sc.ScreenRecorder(tmp_path / "out.mp4")

    assert recorder.start() is False
    assert "No ffmpeg" in recorder.error
    assert not recorder.running


def test_recording_captures_the_simulator_window_not_the_desktop(ffmpeg, tmp_path, monkeypatch):
    """A participant's own laptop has other things open, and they are not ours."""
    monkeypatch.setattr(sc.sys, "platform", "win32")
    command = sc.record_command(ffmpeg, tmp_path / "out.mp4")

    assert "gdigrab" in command
    assert f"title={sc.WINDOW_TITLE}" in command
    assert "desktop" not in " ".join(command)


def test_the_recorder_notes_when_it_started_so_clips_can_be_placed(ffmpeg, tmp_path):
    recorder = sc.ScreenRecorder(
        tmp_path / "out.mp4",
        popen=lambda *_a, **_k: FakeProcess(),
        clock=iter([1000.0, 1120.0]).__next__,
    )
    assert recorder.start() is True
    (tmp_path / "out.mp4").write_bytes(b"video")

    recording = recorder.stop()

    assert recording is not None
    assert recording.started_at == 1000.0
    assert recording.duration_s == 120.0
    # A telemetry sample twenty seconds in sits twenty seconds into the file.
    assert recording.offset_of(1020.0) == 20.0


def test_stopping_asks_ffmpeg_to_quit_rather_than_killing_it(ffmpeg, tmp_path):
    """A terminated encoder leaves an mp4 with no index: a corrupt file."""
    process = FakeProcess()
    recorder = sc.ScreenRecorder(
        tmp_path / "out.mp4", popen=lambda *_a, **_k: process, clock=lambda: 1.0
    )
    recorder.start()
    (tmp_path / "out.mp4").write_bytes(b"video")

    recorder.stop()

    assert process.stdin.written == b"q"
    assert process.stdin.closed
    assert not process.terminated


def test_an_encoder_that_will_not_quit_is_eventually_stopped(ffmpeg, tmp_path):
    class Stubborn(FakeProcess):
        def wait(self, timeout=None):
            if not self.terminated:
                raise subprocess.TimeoutExpired("ffmpeg", timeout or 0)
            return 0

    process = Stubborn()
    recorder = sc.ScreenRecorder(
        tmp_path / "out.mp4", popen=lambda *_a, **_k: process, clock=lambda: 1.0
    )
    recorder.start()
    (tmp_path / "out.mp4").write_bytes(b"video")

    recorder.stop()
    assert process.terminated


def test_a_recording_that_produced_no_file_is_reported_not_returned(ffmpeg, tmp_path):
    recorder = sc.ScreenRecorder(
        tmp_path / "out.mp4", popen=lambda *_a, **_k: FakeProcess(), clock=lambda: 1.0
    )
    recorder.start()

    assert recorder.stop() is None
    assert "no video" in recorder.error


def test_a_recorder_that_never_started_stops_quietly(ffmpeg, tmp_path):
    assert sc.ScreenRecorder(tmp_path / "out.mp4").stop() is None


def test_a_clip_is_cut_at_the_stretch_with_a_little_either_side(ffmpeg, tmp_path):
    """A corner makes no sense without the approach to it."""
    recording = sc.Recording(tmp_path / "session.mp4", started_at=1000.0, duration_s=600.0)
    captured = {}

    def runner(command, **kwargs):
        captured["command"] = command
        Path(command[command.index("-y") + 1]).write_bytes(b"clip")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    sc.cut_clip(recording, 1100.0, 1108.0, tmp_path / "clip.mp4", runner=runner)

    command = captured["command"]
    assert command[command.index("-ss") + 1] == f"{100.0 - sc.CLIP_LEAD_S:.3f}"
    assert command[command.index("-t") + 1] == (
        f"{8.0 + sc.CLIP_LEAD_S + sc.CLIP_TAIL_S:.3f}"
    )
    # Seeking before -i, or a ten minute recording is decoded in full per clip.
    assert command.index("-ss") < command.index("-i")


def test_a_clip_at_the_very_start_does_not_seek_before_the_file(ffmpeg, tmp_path):
    recording = sc.Recording(tmp_path / "session.mp4", started_at=1000.0, duration_s=600.0)

    def runner(command, **kwargs):
        assert float(command[command.index("-ss") + 1]) >= 0.0
        Path(command[command.index("-y") + 1]).write_bytes(b"clip")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    sc.cut_clip(recording, 1000.5, 1004.0, tmp_path / "clip.mp4", runner=runner)


def test_a_stretch_outside_the_recording_is_refused(ffmpeg, tmp_path):
    recording = sc.Recording(tmp_path / "session.mp4", started_at=1000.0, duration_s=60.0)

    with pytest.raises(sc.RecordingError, match="not inside the recording"):
        sc.cut_clip(recording, 1100.0, 1000.0, tmp_path / "clip.mp4", runner=lambda *a, **k: None)


def test_a_failed_cut_says_what_ffmpeg_said(ffmpeg, tmp_path):
    recording = sc.Recording(tmp_path / "session.mp4", started_at=1000.0, duration_s=600.0)

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, b"", b"moov atom not found")

    with pytest.raises(sc.RecordingError, match="moov atom not found"):
        sc.cut_clip(recording, 1100.0, 1108.0, tmp_path / "clip.mp4", runner=runner)


def test_a_recording_survives_being_written_to_a_manifest_and_read_back(tmp_path):
    """The clips are cut long after the session, from whatever was recorded then."""
    original = sc.Recording(tmp_path / "s.mp4", started_at=1234.5, duration_s=60.25)
    restored = sc.Recording.from_dict(original.to_dict())

    assert restored.path == original.path
    assert restored.started_at == original.started_at
    assert restored.offset_of(1294.5) == 60.0


def test_the_windows_payload_pins_ffmpeg_like_every_other_binary():
    """A download that quietly returns something else must fail the build."""
    build = Path("integrations/torcs-1.3.9/build-windows.ps1").read_text("utf-8")

    assert "$FfmpegSize = 170644098" in build
    assert "fe5a8f090b9fbc77d5e64c7d8b404b8837e05a09663ed9768ba19284cf929b20" in build
    # Staged where ffmpeg_binary() looks for it.
    assert "Join-Path $StageDir 'ffmpeg'" in build


def test_the_installer_refuses_a_payload_without_ffmpeg():
    nsi = Path("integrations/torcs-1.3.9/installer/apexstudy.nsi").read_text("utf-8")
    assert "ffmpeg\\ffmpeg.exe" in nsi


def test_the_window_title_the_recorder_captures_is_the_one_torcs_sets():
    """Capturing by title only works while both sides agree on it."""
    patch = Path("integrations/torcs-1.3.9/patches/screen-size-init.patch").read_text("utf-8")
    assert f'glutSetWindowTitle("{sc.WINDOW_TITLE}")' in patch
