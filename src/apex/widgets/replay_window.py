"""The review window: what to do differently, one moment of the lap at a time.

Organised around moments rather than around the lap. A participant does not need
to be told how their lap went -- they drove it -- they need to know that at Turn
3 they braked too early and what to do about it. So the window is a short list of
moments, each labelled with the kind of driving it is about, and picking one
shows the same few seconds three ways: the footage, the line against their best
lap, and one instruction to try next time.

The traces on the analysis screen answer a different question, for a different
reader. A time-series segment is a fine way to show a researcher what happened
and a poor way to tell a driver what to change.
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QVBoxLayout,
    QWidget,
)

from apex import theme
from apex.widgets.footage_pane import FootagePane
from apex.widgets.track_replay import TrackReplay
from f1coach_core import DebriefPoint, Lap
from f1coach_core.footage import Window, windows_for
from racecoach.telemetry.screen_capture import Recording


def _kind_suffix(point) -> str:
    """What kind of driving this corner is about.

    "unexplained" is kept for a corner that measurably cost time without any one
    measurement moving far enough to say why: the loss is real and the remedy was
    never measured, and saying so beats implying nobody looked. A corner reviewed
    without a reference has no loss to explain, so it says nothing.
    """
    if point.category:
        return f" · {point.category}"
    return " · unexplained" if point.time_lost_s is not None else ""


def _loss_suffix(point) -> str:
    """What the corner cost, or nothing at all.

    A single-lap review has no reference and therefore no loss. Printing "0.00 s
    lost" there would assert a measurement nobody made.
    """
    if point.time_lost_s is None:
        return ""
    return f"   —   {point.time_lost_s:.2f} s lost"


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

        # The moments, worst first, each labelled by what it is about. A list
        # rather than a dropdown: how many things there are to work on is part of
        # the answer, and hiding them behind a click makes the window look like
        # it has one finding when it has three.
        self._chooser = QListWidget()
        self._chooser.setMaximumHeight(96)
        self._chooser.setToolTip("The moments that cost the most time, worst first")
        self._chooser.currentRowChanged.connect(self._chosen)

        self._replay = TrackReplay(self)
        self._replay.cursorMoved.connect(self.cursorMoved)

        self._footage = FootagePane(self)
        self._ai_evidence_note = QLabel(
            "AI advice is based on validated telemetry. Video is shown only for "
            "your review and is not sent to the model."
        )
        self._ai_evidence_note.setWordWrap(True)
        self._ai_evidence_note.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 11px; padding: 2px 0;"
        )
        self._ai_evidence_note.setAccessibleName("AI evidence source")

        # The advice, beside the footage it is about. Reading "brake later here"
        # while watching the place it happened is a different thing from reading
        # it in a list of findings.
        self._headline = QLabel()
        self._headline.setWordWrap(True)
        self._headline.setStyleSheet("font-weight: 600; font-size: 15px;")
        self._observation = QLabel()
        self._observation.setWordWrap(True)
        self._advice = QLabel()
        self._advice.setWordWrap(True)
        self._advice.setStyleSheet(
            f"color: {theme.GREEN}; font-weight: 600; padding-top: 6px;"
        )
        self._measured = QLabel()
        self._measured.setWordWrap(True)
        self._measured.setStyleSheet(f"color: {theme.TEXT_DIM}; padding-top: 6px;")

        advice_column = QVBoxLayout()
        advice_column.setSpacing(4)
        advice_column.addWidget(self._headline)
        advice_column.addWidget(self._observation)
        advice_column.addWidget(self._advice)
        advice_column.addWidget(self._measured)
        advice_column.addStretch(1)

        right = QVBoxLayout()
        right.addWidget(self._footage, stretch=3)
        right.addWidget(self._ai_evidence_note)
        right.addLayout(advice_column, stretch=2)

        panes = QHBoxLayout()
        panes.addWidget(self._replay, stretch=3)
        panes.addLayout(right, stretch=3)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(QLabel("Corners, in the order they are driven"))
        layout.addWidget(self._chooser)
        layout.addLayout(panes, stretch=1)

        self._points: list[DebriefPoint] = []
        self._lap: Lap | None = None
        self._reference: Lap | None = None
        self._notes: dict[int, str] = {}
        self._advice_by_index: dict = {}
        self._advice_complete = False
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
        advice: dict | None = None,
        advice_complete: bool = False,
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
        self._advice_by_index = dict(advice or {})
        self._advice_complete = advice_complete
        self._recording = recording
        self._clips_dir = clips_dir
        self._points = list(points) if points else [point]
        if point not in self._points:
            self._points = [point]

        index = self._points.index(point)
        self._chooser.blockSignals(True)
        self._chooser.clear()
        for position, item in enumerate(self._points, start=1):
            self._chooser.addItem(
                f"{position}.  {item.corner}{_kind_suffix(item)}{_loss_suffix(item)}"
            )
        self._chooser.setCurrentRow(index)
        self._chooser.blockSignals(False)

        self._windows = windows_for(lap, self._points)
        self._load(index, fallback_note=note)
        self.show()
        self.raise_()

    def update_advice(self, advice: dict, *, complete: bool) -> None:
        """Refresh the visible instruction when background coaching finishes."""
        self._advice_by_index = dict(advice)
        self._advice_complete = complete
        index = self._chooser.currentRow()
        if 0 <= index < len(self._points):
            self._describe(index, self._points[index], self._notes.get(index, ""))

    def _chosen(self, index: int) -> None:
        if 0 <= index < len(self._points):
            self._load(index)
            self.stretchChanged.emit(index)

    def _describe(self, index: int, point, fallback_note: str) -> None:
        """Fill the advice column, saying plainly when there is no advice to give."""
        self._headline.setText(f"{point.corner}{_kind_suffix(point)}{_loss_suffix(point)}")

        spoken = self._advice_by_index.get(index)
        observation = spoken.narration if spoken else ""
        instruction = spoken.advice if spoken else ""

        self._observation.setText(observation or point.difference or fallback_note)
        if instruction:
            self._advice.setText(f"Next lap: {instruction}")
            self._advice.show()
        elif point.category and self._advice_complete:
            self._advice.setText(
                "The analysis is complete, but no validated AI advice cited this "
                "exact stretch. The measured telemetry remains below."
            )
            self._advice.show()
        elif point.category:
            # Measured, but not yet put into words. The analysis starts by itself
            # when a lap is opened, so this is a wait rather than an instruction.
            self._advice.setText(
                "Advice for this one is still being written — it appears here "
                "when the coach has read the lap."
            )
            self._advice.show()
        else:
            # An honest gap: the loss is real, the reason was never measured, and
            # an instruction here would be invented however plausible it sounded.
            self._advice.setText(
                "Nothing measured here says what to change, so there is no advice "
                "for this one — only that the time went."
            )
            self._advice.show()
        self._measured.setText(point.detail)

    def _load(self, index: int, *, fallback_note: str = "") -> None:
        if self._lap is None or not 0 <= index < len(self._points):
            return
        point = self._points[index]
        title = point.headline
        if point.difference:
            title += f"  —  {point.difference}"
        note = self._notes.get(index) or fallback_note or point.detail
        self._describe(index, point, note)
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
