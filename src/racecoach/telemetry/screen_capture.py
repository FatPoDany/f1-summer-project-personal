"""Recording the race window, so coaching can point at what actually happened.

A line on a map says a participant lost time at a corner. Footage of that corner
says what they did there. The two together are the difference between being told
and being shown, which is what the coaching is for.

Two things this deliberately refuses to do:

* Record the desktop. gdigrab will happily capture the whole screen and it would
  be easier, but participants install this on their own laptops and whatever else
  they have open is not ours to record. Only the simulator window, found by the
  fixed title the overlay patch gives it.
* Cost anybody their session. Recording is a convenience layered on top of the
  telemetry; every failure here is reported and swallowed, never raised into the
  capture that the study actually depends on.

Clips are cut against the wall clock, not the simulator's. Telemetry rows carry
``wall_clock_s`` for exactly this reason: simulator time only tracks real time
while the machine keeps up, and a clip cut against a drifting clock points at the
wrong corner -- which is worse than having no clip at all.
"""

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

WINDOW_TITLE = "Apex TORCS"  # set by patches/screen-size-init.patch
FFMPEG_EXECUTABLE = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"

FRAMERATE = 30
# Constant Rate Factor: plenty for reviewing a driving line, and small enough
# that a ten minute session is a few hundred MB rather than several GB.
CRF = 28
# The encoder must not compete with the simulator for CPU. A participant whose
# frame rate drops because we are recording is driving a different car from one
# whose does not, and the study is comparing how they drove.
PRESET = "veryfast"

# Padding either side of a coaching stretch. A corner makes no sense without the
# approach to it, and a viewer needs a moment to recognise where they are.
CLIP_LEAD_S = 3.0
CLIP_TAIL_S = 2.0

# How long to wait for the simulator window to exist before giving up on
# recording. Loading a track takes a while on a laptop, and the recorder is
# useless if it gives up first.
WINDOW_WAIT_S = 90.0
WINDOW_POLL_S = 0.5
ENCODER_TERMINATE_WAIT_S = 5.0
ENCODER_KILL_WAIT_S = 2.0


def window_exists(title: str = WINDOW_TITLE) -> bool:
    """Whether a window with this exact title is open.

    gdigrab resolves the title once, at start-up, and fails outright if nothing
    matches -- it does not wait and does not retry. So the recorder has to know
    when the window is there, rather than being started alongside the simulator
    and hoping.
    """
    if sys.platform != "win32":
        return True  # X11 grabs a display, which exists before TORCS does
    import ctypes

    try:
        return bool(ctypes.windll.user32.FindWindowW(None, title))
    except (AttributeError, OSError):  # pragma: no cover - not reachable off Windows
        return False


def wait_for_window(
    *,
    timeout_s: float = WINDOW_WAIT_S,
    poll_s: float = WINDOW_POLL_S,
    exists=window_exists,
    sleep=time.sleep,
    clock=time.monotonic,
) -> bool:
    deadline = clock() + timeout_s
    while clock() < deadline:
        if exists():
            return True
        sleep(poll_s)
    return False


class RecordingError(RuntimeError):
    """Recording could not start, or a clip could not be cut."""


@dataclass(frozen=True)
class Recording:
    """A finished recording, and the wall-clock instant its first frame belongs to."""

    path: Path
    started_at: float  # seconds since the epoch
    duration_s: float

    def offset_of(self, wall_clock_s: float) -> float:
        """Where a telemetry instant sits inside this file."""
        return wall_clock_s - self.started_at

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "started_at": self.started_at,
            "duration_s": round(self.duration_s, 3),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Recording":
        return cls(
            path=Path(data["path"]),
            started_at=float(data["started_at"]),
            duration_s=float(data.get("duration_s", 0.0)),
        )


def ffmpeg_binary() -> Path | None:
    """The ffmpeg this install should use, or None if there is not one."""
    configured = os.environ.get("APEX_FFMPEG")
    if configured:
        candidate = Path(configured)
        return candidate if candidate.is_file() else None

    roots: list[Path] = []
    packaged = getattr(sys, "_MEIPASS", None)
    if packaged:
        roots.append(Path(packaged))
    roots.append(Path(sys.executable).resolve().parent)
    for root in roots:
        for candidate in (root / "ffmpeg" / FFMPEG_EXECUTABLE, root / FFMPEG_EXECUTABLE):
            if candidate.is_file():
                return candidate

    from shutil import which

    found = which(FFMPEG_EXECUTABLE)
    return Path(found) if found else None


def is_available() -> bool:
    return ffmpeg_binary() is not None


def record_command(binary: Path, destination: Path) -> list[str]:
    """Capture the simulator window only, never the desktop around it."""
    if sys.platform == "win32":
        source = ["-f", "gdigrab", "-i", f"title={WINDOW_TITLE}"]
    else:
        # X11 offers no title-based grab, so a display capture is the only option
        # here. This branch exists for development; the study runs on Windows.
        source = ["-f", "x11grab", "-i", os.environ.get("DISPLAY", ":0")]
    return [
        str(binary),
        "-hide_banner",
        "-loglevel",
        "error",
        "-framerate",
        str(FRAMERATE),
        *source,
        "-c:v",
        "libx264",
        "-preset",
        PRESET,
        "-crf",
        str(CRF),
        "-pix_fmt",
        "yuv420p",
        "-y",
        str(destination),
    ]


class ScreenRecorder:
    """An ffmpeg recording the race window for the length of one session."""

    def __init__(
        self,
        destination: str | Path,
        *,
        popen=subprocess.Popen,
        clock=time.time,
    ) -> None:
        self.destination = Path(destination)
        self._popen = popen
        self._clock = clock
        self._process = None
        self._started_at: float | None = None
        self._thread: threading.Thread | None = None
        self._abandon = threading.Event()
        self.error: str = ""

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start_when_window_appears(self, **wait_kwargs) -> None:
        """Start recording once the simulator window exists, without blocking.

        The caller launches TORCS and waits for it to exit, so this cannot wait
        inline. Beginning a few seconds late costs nothing: every clip is placed
        by the wall clock stamped on the telemetry, not by where the file starts.
        """

        def wait_then_start() -> None:
            if not wait_for_window(**wait_kwargs):
                self.error = (
                    "The simulator window never appeared, so nothing was recorded."
                )
                return
            if not self._abandon.is_set():
                self.start()

        self._thread = threading.Thread(target=wait_then_start, daemon=True)
        self._thread.start()

    def start(self) -> bool:
        """Begin recording. False, with `error` set, if it could not start."""
        binary = ffmpeg_binary()
        if binary is None:
            self.error = "No ffmpeg with this install, so the session was not recorded."
            return False
        try:
            self.destination.parent.mkdir(parents=True, exist_ok=True)
            self._process = self._popen(
                record_command(binary, self.destination),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                # Kept, not discarded. Throwing this away once left "produced no
                # video" as the only thing anybody could say about a failure, and
                # ffmpeg had explained itself on this pipe the whole time.
                stderr=subprocess.PIPE,
                **_no_console_window(),
            )
        except OSError as exc:
            self.error = f"Screen recording could not start: {exc}"
            return False
        self._started_at = self._clock()
        return True

    def stop(self, *, timeout_s: float = 20.0) -> Recording | None:
        """End the recording and return it, or None if there is nothing usable.

        ffmpeg is asked to quit through its own stdin rather than killed. A
        terminated encoder leaves an mp4 with no index, which players treat as a
        corrupt file -- throwing away the whole session's footage at the last
        moment, after the participant has already driven it.
        """
        self._abandon.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        process, self._process = self._process, None
        if process is None or self._started_at is None:
            return None
        if process.poll() is None:
            try:
                if process.stdin:
                    process.stdin.write(b"q")
                    process.stdin.flush()
                    process.stdin.close()
            except OSError:
                pass
            try:
                process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                try:
                    process.terminate()
                except OSError:
                    pass
                try:
                    process.wait(timeout=ENCODER_TERMINATE_WAIT_S)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=ENCODER_KILL_WAIT_S)
                    except subprocess.TimeoutExpired:
                        self.error = (
                            "The screen recorder could not be stopped, so its "
                            "unfinished video was not published."
                        )
                        return None

        if process.returncode not in (0, None):
            detail = _why(process)
            self.error = (
                f"The screen recorder exited with code {process.returncode}, so "
                f"its video was not published. {detail}"
            ).strip()
            return None

        duration = self._clock() - self._started_at
        try:
            usable = self.destination.is_file() and self.destination.stat().st_size > 0
        except OSError:
            usable = False
        if not usable:
            self.error = f"The screen recording produced no video. {_why(process)}".strip()
            return None
        return Recording(
            path=self.destination, started_at=self._started_at, duration_s=duration
        )


def clip_start_offset(
    recording: "Recording", from_wall_clock: float, *, lead_s: float = CLIP_LEAD_S
) -> float:
    """Where in the recording a clip of this stretch begins.

    Exposed rather than inlined because a player has to answer the reverse
    question -- which moment of the clip a point on track is -- and near the
    start of a recording the lead is truncated. Assuming a fixed lead there puts
    the marker seconds away from the picture it is supposed to explain.
    """
    return max(0.0, recording.offset_of(from_wall_clock) - lead_s)


def cut_clip(
    recording: Recording,
    from_wall_clock: float,
    to_wall_clock: float,
    destination: str | Path,
    *,
    lead_s: float = CLIP_LEAD_S,
    tail_s: float = CLIP_TAIL_S,
    runner=subprocess.run,
) -> Path:
    """Cut the footage of one coaching stretch out of a session recording.

    Re-encoded rather than stream-copied: a copy can only cut on a keyframe, and
    with a multi-second GOP that lands the clip well away from the corner being
    discussed. Precision matters more here than a few seconds of CPU, because a
    clip that shows the wrong corner is worse than no clip.
    """
    binary = ffmpeg_binary()
    if binary is None:
        raise RecordingError("No ffmpeg with this install, so no clip can be cut.")

    start = clip_start_offset(recording, from_wall_clock, lead_s=lead_s)
    end = recording.offset_of(to_wall_clock) + tail_s
    if end <= start:
        raise RecordingError("That stretch is not inside the recording.")

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Cut beside the clip and moved into place once ffmpeg is done. An
    # interrupted cut -- Apex killed, the machine asleep -- otherwise leaves a
    # short file under the name of a finished one, and from then on that corner
    # plays a picture that stops before the corner does with nothing to say why.
    # Keeping the real extension: ffmpeg chooses the container from it, and a
    # bare ".part" is a format it has never heard of.
    partial = destination.with_name(f"{destination.stem}.part{destination.suffix}")
    command = [
        str(binary),
        "-hide_banner",
        "-loglevel",
        "error",
        # Before -i so ffmpeg seeks rather than decoding from the beginning,
        # then -t for the length: a ten minute recording would otherwise be
        # decoded in full for every clip.
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{end - start:.3f}",
        "-i",
        str(recording.path),
        "-c:v",
        "libx264",
        "-preset",
        PRESET,
        "-crf",
        str(CRF),
        "-pix_fmt",
        "yuv420p",
        "-an",  # the recording has no audio, and the study is not about sound
        "-y",
        str(partial),
    ]
    completed = runner(command, capture_output=True, **_no_console_window())
    if completed.returncode != 0 or not partial.is_file():
        partial.unlink(missing_ok=True)
        detail = (getattr(completed, "stderr", b"") or b"").decode("utf-8", "replace")
        raise RecordingError(f"Could not cut the clip: {detail.strip()[:200]}")
    os.replace(partial, destination)
    return destination


def _why(process) -> str:
    """Whatever ffmpeg said on its way out, trimmed to something readable."""
    stream = getattr(process, "stderr", None)
    if stream is None:
        return ""
    try:
        detail = stream.read() or b""
    except (OSError, ValueError):
        # The pipe is already closed or was never one. Reporting nothing is
        # better than failing inside the code that reports a failure.
        return ""
    if isinstance(detail, bytes):
        detail = detail.decode("utf-8", "replace")
    detail = " ".join(detail.split())
    return f"ffmpeg said: {detail[:300]}" if detail else ""


def _no_console_window() -> dict:
    """Keep a console window from flashing up in the participant's face."""
    if sys.platform != "win32":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
