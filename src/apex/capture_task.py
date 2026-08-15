"""Background lifecycle for the participant-facing human capture flow."""

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, Signal

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
