"""The review window: what to do differently, one moment of the lap at a time.

Organised around moments rather than around the lap. A participant does not need
to be told how their lap went -- they drove it -- they need to know that at Turn
3 they braked too early and what to do about it. So the window is a short list of
moments, each labelled with the kind of driving it is about, and picking one
shows the same few seconds three ways: the footage, the line against the lap
they are comparing with, and one instruction to try next time.

The traces on the analysis screen answer a different question, for a different
reader. A time-series segment is a fine way to show a researcher what happened
and a poor way to tell a driver what to change.
"""

import html
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
from apex.captions import lap_caption
from apex.widgets.footage_pane import FootagePane
from apex.widgets.track_replay import TrackReplay
from f1coach_core import DebriefPoint, Lap
from f1coach_core.footage import Window, windows_for
from racecoach.telemetry.screen_capture import Recording


def _matched_rate(mine: Window | None, theirs: Window | None) -> float:
    """How fast the compared lap's picture must run to stay at the same place.

    The track map has always moved the second marker by distance rather than by
    elapsed time -- that is what makes the gap on screen mean anything -- and a
    picture running at real time beside it ends up a corner away from the dot it
    belongs to. This is not a nicety: Turn 7 of the baseline session takes 6.6 s
    in one lap and 14.7 s in the other, so at real time the second picture is
    less than half way through the corner when the first one leaves it.

    Clamped, because past about four times a player stutters more than it shows.
    """
    if mine is None or theirs is None or mine.seconds <= 0 or theirs.seconds <= 0:
        return 1.0
    return min(max(theirs.seconds / mine.seconds, 0.25), 4.0)


def _caption_text(colour: str, text: str) -> str:
    """A dot in the colour of a lap's line, and what to call that lap."""
    return f'<span style="color:{colour}">●</span> {text}'


def _caption(colour: str, text: str) -> QLabel:
    """A dot and a word saying whose driving a picture is of."""
    label = QLabel(_caption_text(colour, text))
    label.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 11px;")
    label.setWordWrap(True)
    return label


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
        # One Play button for both halves of the corner: the marker runs the
        # transport and the footage follows it, re-anchored whenever the
        # transport changes so the two cannot drift apart on a machine where the
        # simulator did not keep real time.
        self._replay.playbackToggled.connect(self._playback_toggled)
        self._replay.scrubbed.connect(self._footage_seek)

        # Two pictures of the same corner in a comparison: the participant's own
        # drive and the lap being held up against it. One picture can only show
        # that a corner went badly; the pair shows what the other car did
        # instead, which is the thing the whole window is arguing about.
        self._footage = FootagePane(self)
        self._ref_footage = FootagePane(self)
        for pane in (self._footage, self._ref_footage):
            pane.busyChanged.connect(self._footage_busy_changed)
        self._ref_showing = False
        # Captioned in the colours the two lines on the track map already use,
        # because "which car am I watching" must not be a question. Both are
        # renamed after the laps themselves as soon as there are laps to name:
        # a role is not an identity, and a driver who has opened three reviews
        # cannot tell from "this lap" which lap this one is.
        self._footage_caption = _caption(theme.GREEN, "this lap")
        self._ref_caption = _caption(theme.BLUE, "the lap you are comparing with")
        self._footage_caption.hide()

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

        this_column = QVBoxLayout()
        this_column.setSpacing(2)
        this_column.addWidget(self._footage_caption)
        this_column.addWidget(self._footage, stretch=1)

        self._ref_column = QWidget()
        ref_column = QVBoxLayout(self._ref_column)
        ref_column.setContentsMargins(0, 0, 0, 0)
        ref_column.setSpacing(2)
        ref_column.addWidget(self._ref_caption)
        ref_column.addWidget(self._ref_footage, stretch=1)
        self._ref_column.hide()  # a single-lap review has nothing to put beside it

        videos = QHBoxLayout()
        videos.addLayout(this_column, stretch=1)
        videos.addWidget(self._ref_column, stretch=1)

        right = QVBoxLayout()
        right.addLayout(videos, stretch=3)
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
        self._ref_recording: Recording | None = None
        self._ref_windows: dict[int, Window] = {}
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
        reference_recording: Recording | None = None,
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
        self._ref_recording = reference_recording
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
        # The same corners located in the compared lap's own recorded time: it
        # drove them at a different moment, and for the two clips to be of the
        # same piece of track each has to be cut from its own driver's clock.
        comparing = reference is not None and reference is not lap
        self._ref_windows = windows_for(reference, self._points) if comparing else {}
        if comparing and self._ref_recording is not None and self.width() < 1100:
            self.resize(1180, max(self.height(), 620))  # room for two pictures
        self._name_the_laps()
        self._load(index, fallback_note=note)
        self.show()
        self.raise_()

    def _name_the_laps(self) -> None:
        """Say which two laps are on screen: over each picture and on the window.

        The captions named the roles -- "this lap", "the lap you are comparing
        with" -- and never the laps, so which two laps a review was of had to be
        carried in the driver's head from the picker on the analysis screen.
        They are named here exactly as that picker names them, and the window
        itself carries both so a review left open beside another one still says
        what it is.
        """
        if self._lap is None:
            return
        mine = lap_caption(self._lap)
        self._footage_caption.setText(
            _caption_text(theme.GREEN, f"this lap — {html.escape(mine)}")
        )
        if self._reference is None or self._reference is self._lap:
            # Nothing beside it, so nothing may keep the name of a lap that was
            # compared here before this one.
            self._ref_caption.setText(
                _caption_text(theme.BLUE, "the lap you are comparing with")
            )
            self.setWindowTitle(f"Review — {mine}")
            return
        theirs = lap_caption(self._reference)
        self._ref_caption.setText(
            _caption_text(theme.BLUE, f"comparing with — {html.escape(theirs)}")
        )
        self.setWindowTitle(f"Review — {mine}  vs  {theirs}")

    def _panes(self) -> tuple[FootagePane, ...]:
        """The footage the transport drives: one, or two in a comparison."""
        return (self._footage, self._ref_footage) if self._ref_showing else (self._footage,)

    def _playback_toggled(self, playing: bool) -> None:
        if not playing:
            for pane in self._panes():
                pane.pause()
            return
        self._anchor_footage()
        for pane in self._panes():
            pane.resume()

    def _anchor_footage(self) -> None:
        """Put every picture on the moment the marker is at."""
        moment = self._replay.current_wall_clock
        if moment is not None:
            self._footage.seek_to(moment)
        reference = self._replay.current_reference_wall_clock
        if self._ref_showing and reference is not None:
            self._ref_footage.seek_to(reference)

    def _footage_seek(self, _wall_clock: float) -> None:
        # The signal carries the marker's own clock; the compared lap's clock
        # for the same point on track is matched by distance rather than time,
        # so both are read together and the two pictures cannot disagree.
        self._anchor_footage()

    def _footage_busy_changed(self, _busy: bool) -> None:
        """Play waits until there is something to play in step with."""
        working = any(pane.busy for pane in self._panes())
        self._replay.set_transport_enabled(
            not working,
            reason="The footage of this corner is still being cut." if working else "",
        )
        if not working and self._replay.playing:
            self._anchor_footage()
            for pane in self._panes():
                pane.resume()

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
        reference_window = self._ref_windows.get(index)
        # Decided before either pane is told anything: the panes report whether
        # they are busy as they go, and that answer depends on how many of them
        # the transport is waiting for.
        self._ref_showing = self._ref_recording is not None and reference_window is not None
        self._ref_column.setVisible(self._ref_showing)
        self._footage_caption.setVisible(self._ref_showing)
        clips_dir = self._clips_dir or Path.cwd()
        self._footage.show_stretch(
            index,
            self._recording,
            self._windows.get(index),
            clips_dir,
        )
        if self._ref_showing:
            self._ref_footage.set_rate(
                _matched_rate(self._windows.get(index), reference_window)
            )
            # Both laps' clips share the folder: each is named after the seconds
            # of recording it holds, so two laps cannot claim the same file.
            self._ref_footage.show_stretch(
                index, self._ref_recording, reference_window, clips_dir
            )
        else:
            self._ref_footage.clear()
