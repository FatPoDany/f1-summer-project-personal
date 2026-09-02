"""Background lifecycle for the participant-facing human capture flow."""

import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from racecoach.telemetry.handoff import Handoff, finish_session
from racecoach.telemetry.human_capture import (
    HumanCaptureCancelled,
    HumanCaptureConfig,
    HumanCaptureError,
    HumanCaptureResult,
    ManagedTorcsRunner,
    capture_human_runs,
)

CaptureFunction = Callable[..., HumanCaptureResult]


class CaptureTaskSignals(QObject):
    completed = Signal(object, object)
    stopped = Signal(object, str)
    failed = Signal(object, str, str)


class HumanCaptureTask(QRunnable):
    """One cancellable TORCS process and capture import, off the GUI thread."""

    def __init__(
        self,
        config: HumanCaptureConfig,
        *,
        capture_fn: CaptureFunction = capture_human_runs,
    ) -> None:
        super().__init__()
        self.config = config
        self.signals = CaptureTaskSignals()
        self.token = object()
        self._capture_fn = capture_fn
        self._runner = ManagedTorcsRunner()
        self._stop = threading.Event()
        self._done = threading.Event()

    def request_stop(self) -> None:
        self._stop.set()
        self._runner.request_stop()

    def wait(self, timeout_s: float) -> bool:
        return self._done.wait(timeout_s)

    def run(self) -> None:
        try:
            result = self._capture_fn(
                self.config,
                runner=self._runner,
                stop_requested=self._stop.is_set,
            )
        except HumanCaptureCancelled as exc:
            self.signals.stopped.emit(self.token, str(exc.capture_dir))
        except HumanCaptureError as exc:
            self.signals.failed.emit(self.token, str(exc), str(exc.capture_dir))
        except Exception as exc:
            self.signals.failed.emit(self.token, f"{type(exc).__name__}: {exc}", "")
        else:
            self.signals.completed.emit(self.token, result)
        finally:
            self._done.set()


class HandoffTaskSignals(QObject):
    progress = Signal(object, str)
    finished = Signal(object, object)


class HandoffTask:
    """Closing the loop after a race, on a thread that never holds the window.

    A daemon thread rather than the capture pool, and deliberately not something
    the app waits for on the way out. Sending a race means uploading tens of
    megabytes and then polling their pipeline, which runs for minutes; a
    participant who closes Apex should not find it hanging on somebody else's
    server. The file they have to send is written before any of that starts, so
    quitting early costs the upload and nothing else -- and `racecoach
    upload-ibmf1` picks the bundle up again.
    """

    def __init__(
        self,
        capture_dir: Path,
        *,
        finish_fn: Callable[..., Handoff] = finish_session,
    ) -> None:
        self.capture_dir = Path(capture_dir)
        self.signals = HandoffTaskSignals()
        self.token = object()
        self._finish_fn = finish_fn
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()

    def wait(self, timeout_s: float) -> bool:
        if self._thread is None:
            return True
        self._thread.join(timeout_s)
        return not self._thread.is_alive()

    def run(self) -> None:
        try:
            handoff = self._finish_fn(
                self.capture_dir,
                progress=lambda line: self.signals.progress.emit(self.token, line),
            )
        except Exception as exc:  # noqa: BLE001 - the race is already saved
            self.signals.progress.emit(self.token, f"--  handing over: {exc}")
            handoff = Handoff(capture_dir=self.capture_dir)
        self.signals.finished.emit(self.token, handoff)
