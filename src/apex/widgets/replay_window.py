"""The review window: one stretch of track, seen every way Apex can show it.

Its own window rather than another panel on the analysis screen. Reviewing a
corner is a different activity from reading traces, and the screen behind is
already dense; a participant doing one is not doing the other.

The layout leaves room for footage of the same stretch beside the line, which is
the next thing to land here. Everything on this window is about the same few
seconds of driving, so a viewer only has to understand one place at a time.
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from apex.widgets.footage_pane import FootagePane
from apex.widgets.track_replay import TrackReplay
from f1coach_core import DebriefPoint, Lap
from f1coach_core.footage import Window, windows_for
from racecoach.telemetry.screen_capture import Recording


class ReplayWindow(QDialog):
    """Non-modal so the driver can watch the review and the traces together."""

    cursorMoved = Signal(float)
    stretchChanged = Signal(int)  # index into the debrief points

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.resize(880, 560)

        # Switching stretch without going back to the list behind: a participant
        # reviewing one corner usually wants the next one too.
        self._chooser = QComboBox()
        self._chooser.setToolTip("The stretches that cost the most time, worst first")
        self._chooser.currentIndexChanged.connect(self._chosen)
        chooser_row = QHBoxLayout()
        chooser_row.addWidget(QLabel("Stretch"))
        chooser_row.addWidget(self._chooser, stretch=1)

        self._replay = TrackReplay(self)
        self._replay.cursorMoved.connect(self.cursorMoved)

        self._footage = FootagePane(self)

        panes = QHBoxLayout()
        panes.addWidget(self._replay, stretch=3)
        panes.addWidget(self._footage, stretch=2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addLayout(chooser_row)
        layout.addLayout(panes, stretch=1)

        self._points: list[DebriefPoint] = []
        self._lap: Lap | None = None
        self._reference: Lap | None = None
        self._notes: dict[int, str] = {}
        self._recording: Recording | None = None
        self._windows: dict[int, Window] = {}
        self._clips_dir: Path | None = None

    def show_stretch(
        self,
        lap: Lap,
        point: DebriefPoint,
        *,
        reference: Lap | None = None,
        note: str = "",
        points: list[DebriefPoint] | None = None,
        notes: dict[int, str] | None = None,
        recording: Recording | None = None,
        clips_dir: Path | None = None,
    ) -> None:
        """Open on one stretch, with the rest of the lap's stretches to hand.

        The reference is what turns this from watching yourself into seeing a
        difference: the dashed line is where the quicker lap went, and the two
        markers sit at the same point on track so the gap is the thing on screen.
        """
        self._lap = lap
        self._reference = reference
        self._notes = dict(notes or {})
        self._recording = recording
        self._clips_dir = clips_dir
        self._points = list(points) if points else [point]
        if point not in self._points:
            self._points = [point]

        index = self._points.index(point)
        self._chooser.blockSignals(True)
        self._chooser.clear()
        for item in self._points:
            label = item.headline
            if item.difference:
                label += f"  —  {item.difference}"
            self._chooser.addItem(label)
        self._chooser.setCurrentIndex(index)
        self._chooser.setEnabled(len(self._points) > 1)
        self._chooser.blockSignals(False)

        self._windows = windows_for(lap, self._points)
        self._load(index, fallback_note=note)
        self.show()
        self.raise_()

    def _chosen(self, index: int) -> None:
        if 0 <= index < len(self._points):
            self._load(index)
            self.stretchChanged.emit(index)

    def _load(self, index: int, *, fallback_note: str = "") -> None:
        if self._lap is None or not 0 <= index < len(self._points):
            return
        point = self._points[index]
        title = point.headline
        if point.difference:
            title += f"  —  {point.difference}"
        note = self._notes.get(index) or fallback_note or point.detail
        self._replay.set_stretch(
            self._lap,
            point.span_m[0],
            point.span_m[1],
            title,
            reference=self._reference,
            note=note,
        )
        self._footage.show_stretch(
            index,
            self._recording,
            self._windows.get(index),
            self._clips_dir or Path.cwd(),
        )
