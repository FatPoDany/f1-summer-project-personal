"""A replay of one stretch, in its own window beside the analysis screen."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget

from apex.widgets.track_replay import TrackReplay
from f1coach_core import DebriefPoint, Lap


class ReplayWindow(QDialog):
    """Non-modal so the driver can watch the replay and the traces together."""

    cursorMoved = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Replay")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.resize(560, 460)
        self._replay = TrackReplay(self)
        self._replay.cursorMoved.connect(self.cursorMoved)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self._replay)

    def show_stretch(self, lap: Lap, point: DebriefPoint) -> None:
        title = point.headline
        if point.difference:
            title += f"  —  {point.difference}"
        self._replay.set_stretch(lap, point.span_m[0], point.span_m[1], title)
        self.show()
        self.raise_()
