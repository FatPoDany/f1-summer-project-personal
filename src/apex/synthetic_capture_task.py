"""Background lifecycle for facilitator synthetic reference batches."""

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, Signal

from racecoach.telemetry.synthetic_capture import (
    ManagedSyntheticTorcsRunner,
    SyntheticBatchResult,
    SyntheticCaptureConfig,
    capture_synthetic_batch,
)

BatchFunction = Callable[..., SyntheticBatchResult]


class SyntheticCaptureTaskSignals(QObject):
    progress = Signal(object, object)
    completed = Signal(object, object)
    failed = Signal(object, str)


class SyntheticCaptureTask(QRunnable):
    """One cancellable sequential batch, always executed off the GUI thread."""

    def __init__(
        self,
        config: SyntheticCaptureConfig,
        *,
        capture_fn: BatchFunction = capture_synthetic_batch,
    ) -> None:
        super().__init__()
        self.config = config
        self.signals = SyntheticCaptureTaskSignals()
        self.token = object()
        self._capture_fn = capture_fn
        self._runner = ManagedSyntheticTorcsRunner()
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
                on_progress=lambda snapshot: self.signals.progress.emit(
                    self.token, snapshot
                ),
            )
        except Exception as exc:
            self.signals.failed.emit(self.token, f"{type(exc).__name__}: {exc}")
        else:
            self.signals.completed.emit(self.token, result)
        finally:
            self._done.set()
