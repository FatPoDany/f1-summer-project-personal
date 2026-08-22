"""Background work for the written coach: fetching weights, then narrating.

Both jobs are slow enough to freeze the window if run on the GUI thread -- a
2.1 GB download and a model that takes a minute to load and answer -- and both
have to stay cancellable, because a participant who changes their mind about a
download should not have to kill the app.
"""

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, Signal

from f1coach_core.debrief import DebriefPoint
from racecoach.granite import model as gm
from racecoach.granite import server as gs
from racecoach.granite.narrate import NarratedDebrief, NarrationError, narrate_debrief


class DownloadSignals(QObject):
    progress = Signal(object, int, int)  # token, received, total
    finished = Signal(object, str)  # token, path
    failed = Signal(object, str, bool)  # token, message, resumable
    cancelled = Signal(object)


class ModelDownloadTask(QRunnable):
    """Fetch and verify the weights, reporting progress and honouring cancel."""

    def __init__(self, *, ensure: Callable[..., object] = gm.ensure_model) -> None:
        super().__init__()
        self.signals = DownloadSignals()
        self.token = object()
        self._ensure = ensure
        self._stop = threading.Event()

    def cancel(self) -> None:
        self._stop.set()

    def run(self) -> None:  # pragma: no cover - exercised through the view
        try:
            path = self._ensure(
                on_progress=lambda p: self.signals.progress.emit(
                    self.token, p.received, p.total
                ),
                should_cancel=self._stop.is_set,
            )
        except gm.ModelError as exc:
            if self._stop.is_set():
                self.signals.cancelled.emit(self.token)
            else:
                # A stopped-short transfer keeps its bytes, so the button should
                # offer to carry on rather than to start over.
                self.signals.failed.emit(
                    self.token, str(exc), isinstance(exc, gm.IncompleteDownload)
                )
            return
        self.signals.finished.emit(self.token, str(path))


class NarrationSignals(QObject):
    started = Signal(object)
    finished = Signal(object, object)  # token, NarratedDebrief
    failed = Signal(object, str)


class NarrationTask(QRunnable):
    """Start the server if needed, narrate one lap's debrief, then stand down.

    The server is stopped again afterwards rather than left resident: it holds
    2.1 GB while it lives, and a participant reviewing laps between sessions
    should not be paying for that the whole time.
    """

    def __init__(
        self,
        summary: str,
        points: list[DebriefPoint],
        *,
        server_factory: Callable[[], gs.GraniteServer] = gs.GraniteServer,
        narrate: Callable[..., NarratedDebrief] = narrate_debrief,
    ) -> None:
        super().__init__()
        self.signals = NarrationSignals()
        self.token = object()
        self._summary = summary
        self._points = points
        self._server_factory = server_factory
        self._narrate = narrate

    def run(self) -> None:  # pragma: no cover - exercised through the view
        try:
            self._run()
        except RuntimeError:
            # The panel went away while the model was thinking -- a participant
            # clicked another lap, or the window closed. Nobody is left to tell.
            return

    def _run(self) -> None:
        self.signals.started.emit(self.token)
        server = self._server_factory()
        try:
            base_url = server.start()
        except gs.ServerError as exc:
            self.signals.failed.emit(self.token, str(exc))
            return
        try:
            result = self._narrate(
                self._summary,
                self._points,
                base_url=base_url,
                model=gm.MODEL_REPO.replace("-GGUF", ""),
            )
        except NarrationError as exc:
            self.signals.failed.emit(self.token, str(exc))
            return
        finally:
            server.stop()
        self.signals.finished.emit(self.token, result)
