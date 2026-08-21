"""The one control a participant uses to get their debrief put into words.

Deliberately a strip under the measured debrief rather than a screen of its own.
The measurements are the finding; the prose is a convenience on top, and the
layout should say so. Everything this widget can do -- decline, download,
narrate, fail -- ends with the measured debrief still on screen.
"""

from PySide6.QtCore import QThreadPool, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QWidget,
)

from apex.coach_task import ModelDownloadTask, NarrationTask
from f1coach_core.debrief import DebriefPoint
from racecoach.granite import host as gh


class CoachBar(QWidget):
    """Offers written coaching when this machine can give it, and explains when not."""

    narrated = Signal(object)  # NarratedDebrief

    def __init__(self, parent: QWidget | None = None, *, pool: QThreadPool | None = None):
        super().__init__(parent)
        self._pool = pool or QThreadPool.globalInstance()
        self._capability: gh.Capability | None = None
        self._summary = ""
        self._points: list[DebriefPoint] = []
        self._download: ModelDownloadTask | None = None
        self._narration_token: object | None = None

        self._status = QLabel()
        self._status.setWordWrap(True)
        self._button = QPushButton()
        self._button.clicked.connect(self._on_clicked)
        self._progress = QProgressBar()
        self._progress.setMaximumWidth(220)
        self._progress.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._status, stretch=1)
        layout.addWidget(self._progress)
        layout.addWidget(self._button)

    # -- state -------------------------------------------------------------

    def set_debrief(self, summary: str, points: list[DebriefPoint]) -> None:
        """Point the bar at a lap being compared against a reference.

        Stays put whether or not this lap has findings. Installing the coach is a
        one-off setup step, not a property of the lap on screen, and a button
        that came and went as the participant clicked between laps read as a
        fault rather than as a choice.
        """
        self._summary = summary
        self._points = list(points)
        self.show()
        self._refresh()

    def clear(self) -> None:
        """No lap, or nothing to compare it against."""
        self._points = []
        self.hide()

    def _refresh(self) -> None:
        if self._capability is None:
            self._capability = gh.capability()
        capability = self._capability

        if not capability.can_run:
            self._status.setText(capability.reason)
            self._button.hide()
            self._progress.hide()
            return

        self._button.show()
        if capability.needs_download:
            self._status.setText(capability.reason)
            self._button.setText("Download coach")
        elif self._points:
            self._status.setText(capability.reason)
            self._button.setText("Explain my lap")
        else:
            # Ready, but this lap has nothing that needs explaining.
            self._status.setText("Nothing on this lap needs explaining.")
            self._button.hide()

    # -- actions -----------------------------------------------------------

    def _on_clicked(self) -> None:
        if self._download is not None:
            self._download.cancel()
            return
        assert self._capability is not None
        if self._capability.needs_download:  # noqa: SIM102
            self._start_download()
        else:
            self._start_narration()

    def _start_download(self) -> None:
        task = ModelDownloadTask()
        self._download = task
        task.signals.progress.connect(self._on_progress)
        task.signals.finished.connect(self._on_downloaded)
        task.signals.failed.connect(self._on_download_failed)
        task.signals.cancelled.connect(self._on_cancelled)
        self._progress.setRange(0, 1000)
        self._progress.setValue(0)
        self._progress.show()
        self._button.setText("Cancel")
        self._status.setText("Downloading the coach. You can keep using Apex.")
        self._pool.start(task)

    def _on_progress(self, _token: object, received: int, total: int) -> None:
        if total:
            self._progress.setValue(int(1000 * received / total))
        self._status.setText(
            f"Downloading the coach: {received / 1e9:.1f} of {total / 1e9:.1f} GB."
        )

    def _on_downloaded(self, _token: object, _path: str) -> None:
        self._download = None
        self._progress.hide()
        # Re-ask rather than assume: this is what makes the button change from
        # "Download coach" to "Explain my lap".
        self._capability = gh.capability()
        self._refresh()

    def _on_download_failed(self, _token: object, message: str, resumable: bool) -> None:
        self._download = None
        self._progress.hide()
        # The message already carries the right advice for the case -- resume, or
        # get the file another way -- so it is shown whole rather than trimmed.
        # On a network that blocks the model hosts, "Try again" alone is a lie.
        self._status.setText(message)
        self._button.setText("Resume download" if resumable else "Try again")

    def _on_cancelled(self, _token: object) -> None:
        self._download = None
        self._progress.hide()
        self._status.setText(
            "Download stopped. What you already have is kept, so starting again "
            "resumes rather than restarts."
        )
        self._button.setText("Download coach")

    def _start_narration(self) -> None:
        task = NarrationTask(self._summary, self._points)
        self._narration_token = task.token
        task.signals.started.connect(self._on_narration_started)
        task.signals.finished.connect(self._on_narrated)
        task.signals.failed.connect(self._on_narration_failed)
        self._pool.start(task)

    def _on_narration_started(self, _token: object) -> None:
        self._button.setEnabled(False)
        self._status.setText("Reading your lap. This takes a moment the first time.")

    def _on_narrated(self, token: object, result: object) -> None:
        if token is not self._narration_token:
            return
        self._button.setEnabled(True)
        spoken = getattr(result, "spoken_count", 0)
        if spoken:
            self._status.setText("Written coaching added below.")
        else:
            # Silence here is a guard doing its job, not an empty result.
            self._status.setText(
                "The coach had nothing it could say about these stretches without "
                "guessing, so only the measurements are shown."
            )
        self._button.setText("Explain again")
        self.narrated.emit(result)

    def _on_narration_failed(self, token: object, message: str) -> None:
        if token is not self._narration_token:
            return
        self._button.setEnabled(True)
        self._button.setText("Try again")
        self._status.setText(f"{message} Your lap analysis below is unaffected.")
