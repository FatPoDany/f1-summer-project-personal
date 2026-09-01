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


def test_an_encoder_that_survives_kill_cannot_block_capture_completion(ffmpeg, tmp_path):
    """Footage is optional; an unkillable encoder must not strand the session."""

    class Unkillable(FakeProcess):
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("ffmpeg", timeout or 0)

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    process = Unkillable()
    recorder = sc.ScreenRecorder(
        tmp_path / "out.mp4", popen=lambda *_a, **_k: process, clock=lambda: 1.0
    )
    recorder.start()
    (tmp_path / "out.mp4").write_bytes(b"unfinished video")

    assert recorder.stop(timeout_s=0.01) is None
    assert process.terminated and process.killed
    assert "could not be stopped" in recorder.error


def test_encoder_stop_errors_are_contained_so_telemetry_can_finish(ffmpeg, tmp_path):
    """OS-level terminate/kill failures belong to optional footage, not capture."""

    class Unstoppable(FakeProcess):
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("ffmpeg", timeout or 0)

        def terminate(self):
            raise OSError("terminate denied")

        def kill(self):
            raise OSError("kill denied")

    recorder = sc.ScreenRecorder(
        tmp_path / "out.mp4",
        popen=lambda *_a, **_k: Unstoppable(),
        clock=lambda: 1.0,
    )
    recorder.start()

    assert recorder.stop(timeout_s=0.01) is None
    assert "could not be stopped" in recorder.error


def test_a_recording_that_produced_no_file_is_reported_not_returned(ffmpeg, tmp_path):
    recorder = sc.ScreenRecorder(
        tmp_path / "out.mp4", popen=lambda *_a, **_k: FakeProcess(), clock=lambda: 1.0
    )
    recorder.start()

    assert recorder.stop() is None
    assert "no video" in recorder.error


def test_a_nonzero_encoder_exit_cannot_publish_a_nonempty_but_failed_video(
    ffmpeg, tmp_path
):
    """File size alone is not proof that ffmpeg finalized the container."""

    class FailedEncoder(FakeProcess):
        def wait(self, timeout=None):
            self.returncode = 1
            return self.returncode

    process = FailedEncoder()
    recorder = sc.ScreenRecorder(
        tmp_path / "out.mp4", popen=lambda *_a, **_k: process, clock=lambda: 1.0
    )
    recorder.start()
    (tmp_path / "out.mp4").write_bytes(b"unfinished container")

    assert recorder.stop() is None
    assert "exited with code 1" in recorder.error


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


def _wrote(command):
    Path(command[command.index("-y") + 1]).write_bytes(b"cut")
    return subprocess.CompletedProcess(command, 0, b"", b"")


def test_the_stretches_left_after_a_pause_are_joined_back_together(ffmpeg, tmp_path):
    """Two takes out of one file, played one after the other."""
    captured = {}

    def runner(command, **kwargs):
        captured["command"] = command
        return _wrote(command)

    sc.condense(
        tmp_path / "session.mp4",
        tmp_path / "cut.mp4",
        ((0.0, 162.08), (990.14, 1248.2)),
        runner=runner,
    )

    graph = captured["command"][captured["command"].index("-filter_complex") + 1]
    assert "trim=start=0.000:end=162.080" in graph
    assert "trim=start=990.140:end=1248.200" in graph
    assert "concat=n=2:v=1:a=0" in graph
    assert captured["command"][captured["command"].index("-map") + 1] == "[cut]"


def test_a_recording_with_one_stretch_left_is_not_asked_to_join_anything(ffmpeg, tmp_path):
    captured = {}

    def runner(command, **kwargs):
        captured["command"] = command
        return _wrote(command)

    sc.condense(tmp_path / "session.mp4", tmp_path / "cut.mp4", ((0.0, 30.0),), runner=runner)

    graph = captured["command"][captured["command"].index("-filter_complex") + 1]
    assert "concat" not in graph
    assert captured["command"][captured["command"].index("-map") + 1] == "[keep0]"


def test_cutting_re_encodes_because_a_copy_can_only_cut_on_a_keyframe(ffmpeg, tmp_path):
    """A stream copy would leave a group of pictures of menu behind it."""
    captured = {}

    def runner(command, **kwargs):
        captured["command"] = command
        return _wrote(command)

    sc.condense(tmp_path / "session.mp4", tmp_path / "cut.mp4", ((0.0, 30.0),), runner=runner)

    assert captured["command"][captured["command"].index("-c:v") + 1] == "libx264"
    assert "copy" not in captured["command"]


def test_a_recording_with_nothing_left_of_it_is_refused(ffmpeg, tmp_path):
    with pytest.raises(sc.RecordingError, match="Nothing of that recording"):
        sc.condense(tmp_path / "session.mp4", tmp_path / "cut.mp4", (), runner=lambda *a, **k: None)


def test_an_interrupted_condense_leaves_nothing_under_the_finished_name(ffmpeg, tmp_path):
    destination = tmp_path / "cut.mp4"

    def runner(command, **kwargs):
        Path(command[command.index("-y") + 1]).write_bytes(b"half a file")
        return subprocess.CompletedProcess(command, 255, b"", b"Interrupted")

    with pytest.raises(sc.RecordingError, match="Interrupted"):
        sc.condense(tmp_path / "session.mp4", destination, ((0.0, 30.0),), runner=runner)

    assert not destination.exists()
    assert list(tmp_path.glob("*.part.mp4")) == []


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


def test_recording_waits_for_the_window_rather_than_racing_the_simulator(ffmpeg, tmp_path):
    """gdigrab resolves the title once and fails if nothing matches.

    Starting the recorder alongside TORCS meant pointing it at a window that did
    not exist yet, which produced no video at all -- every time.
    """
    appeared = iter([False, False, True])
    slept = []

    assert sc.wait_for_window(
        exists=lambda: next(appeared), sleep=slept.append, clock=lambda: 0.0
    )
    assert slept == [sc.WINDOW_POLL_S, sc.WINDOW_POLL_S]


def test_a_window_that_never_appears_gives_up_and_says_so(ffmpeg, tmp_path):
    times = iter([0.0, 0.0, 1e9])
    assert not sc.wait_for_window(
        exists=lambda: False, sleep=lambda _s: None, clock=lambda: next(times)
    )

    recorder = sc.ScreenRecorder(tmp_path / "out.mp4")
    recorder.start_when_window_appears(
        exists=lambda: False, sleep=lambda _s: None, clock=iter([0.0, 0.0, 1e9]).__next__
    )
    recorder.stop()
    assert "never appeared" in recorder.error


def test_a_failure_reports_what_ffmpeg_said_about_it(ffmpeg, tmp_path):
    """Discarding stderr once left "produced no video" as the whole explanation."""

    class Explaining(FakeProcess):
        def __init__(self):
            super().__init__()
            self.stderr = _Bytes(b"Could not find window with title 'Apex TORCS'")

    recorder = sc.ScreenRecorder(
        tmp_path / "out.mp4", popen=lambda *_a, **_k: Explaining(), clock=lambda: 1.0
    )
    recorder.start()

    assert recorder.stop() is None
    assert "Could not find window" in recorder.error


class _Bytes:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self):
        return self._data


def test_an_interrupted_cut_leaves_nothing_under_the_clip_name(ffmpeg, tmp_path):
    """A short file named like a finished clip cannot be told from one.

    Apex being killed mid-cut left exactly that -- two of them, found on disk
    with the crash that made them -- and from then on those corners played a
    picture that stopped before the corner did, with nothing to say why.
    """
    recording = sc.Recording(tmp_path / "session.mp4", started_at=1000.0, duration_s=600.0)
    destination = tmp_path / "clip.mp4"

    def runner(command, **kwargs):
        written = Path(command[command.index("-y") + 1])
        assert written != destination, "ffmpeg must not write the clip's own name"
        # ffmpeg picks the container from the extension, so it has to survive.
        assert written.suffix == destination.suffix
        written.write_bytes(b"half a clip")
        return subprocess.CompletedProcess(command, 255, b"", b"killed")

    with pytest.raises(sc.RecordingError):
        sc.cut_clip(recording, 1100.0, 1108.0, destination, runner=runner)

    assert not destination.exists()
    assert list(tmp_path.glob("*.part.mp4")) == []  # and nothing left lying around


def test_a_finished_cut_is_moved_into_place_whole(ffmpeg, tmp_path):
    """So a clip on disk under its own name is that clip, cut in full."""
    recording = sc.Recording(tmp_path / "session.mp4", started_at=1000.0, duration_s=600.0)
    destination = tmp_path / "clip.mp4"

    def runner(command, **kwargs):
        Path(command[command.index("-y") + 1]).write_bytes(b"the whole clip")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    assert sc.cut_clip(recording, 1100.0, 1108.0, destination, runner=runner) == destination
    assert destination.read_bytes() == b"the whole clip"
    assert list(tmp_path.glob("*.part.mp4")) == []
