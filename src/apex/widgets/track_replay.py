"""Play a stretch of a lap back on the track it was driven on.

The strips answer "what did the inputs do"; this answers "where was I, and what
was I doing there". It is the view a driver reads without being taught to read a
trace, so it stays deliberately plain: the lap's own shape, the stretch under
discussion picked out, a marker running along it at the speed it happened, and
the four numbers that were true at that instant.

Nothing here derives telemetry: it renders `Lap.df` columns as recorded.
"""

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from apex import theme
from f1coach_core import Lap

FRAME_MS = 33  # ~30 fps; the marker advances by real elapsed lap time
MARGIN = 14


def span_indices(lap: Lap, d0: float, d1: float) -> tuple[int, int]:
    """The samples of `lap` lying between two distances along the track.

    searchsorted keeps this exact for any sampling rate. ``side="right"`` lands
    one past the stretch, so step back to the last sample actually inside it:
    nothing may be shown running beyond the stretch it claims to be about.
    """
    dist = lap.df["dist"]
    first = int(dist.searchsorted(d0, side="left"))
    last = int(min(dist.searchsorted(d1, side="right") - 1, len(dist) - 1))
    if last <= first:
        last = min(first + 1, len(dist) - 1)
    return first, last


def _pedal_pct(value: float) -> float:
    """A pedal position to show a driver, as a percentage of its actual travel.

    The stored channel is the command as recorded, which is not always inside
    the actuator range: berniw asks for up to 1.25 throttle and the simulator
    clamps it (`car.h:345-349`). Showing "125%" to someone reviewing their drive
    would describe something the car never did, so the display clamps while the
    recorded value stays untouched.
    """
    return min(max(float(value), 0.0), 1.0) * 100.0


class TrackMap(QWidget):
    """The lap's path, with one stretch picked out and a marker on it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(220)
        self._x: list[float] = []
        self._y: list[float] = []
        self._span: tuple[int, int] = (0, 0)
        self._cursor = 0
        # The reference lap's path, drawn behind. Seeing the two lines together
        # is the whole point: "you ran wide here" is an argument, but a line
        # that visibly goes somewhere else is not.
        self._ref_x: list[float] = []
        self._ref_y: list[float] = []
        self._ref_cursor: int | None = None

    def set_lap(self, lap: Lap, first: int, last: int) -> None:
        self._x = lap.df["x"].tolist()
        self._y = lap.df["y"].tolist()
        self._span = (first, last)
        self._cursor = first
        self.update()

    def set_reference(self, lap: Lap | None) -> None:
        if lap is None or not lap.has_track_map:
            self._ref_x, self._ref_y = [], []
        else:
            self._ref_x = lap.df["x"].tolist()
            self._ref_y = lap.df["y"].tolist()
        self._ref_cursor = None
        self.update()

    def set_reference_cursor(self, index: int | None) -> None:
        self._ref_cursor = index
        self.update()

    def clear(self) -> None:
        """A lap without world position: say so rather than draw an empty box."""
        self._x, self._y = [], []
        self._ref_x, self._ref_y = [], []
        self._ref_cursor = None
        self.update()

    def set_cursor(self, index: int) -> None:
        self._cursor = index
        self.update()

    def _transform(self) -> tuple[float, float, float, float, float]:
        """Scale and offset that fit both laps in view, aspect preserved.

        Both, deliberately: two lines drawn to different scales would show a
        difference that is not there, and hide one that is.
        """
        xs = self._x + self._ref_x
        ys = self._y + self._ref_y
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        width = max(x1 - x0, 1e-6)
        height = max(y1 - y0, 1e-6)
        usable_w = max(self.width() - 2 * MARGIN, 1)
        usable_h = max(self.height() - 2 * MARGIN, 1)
        scale = min(usable_w / width, usable_h / height)
        # Centre the drawing in whichever direction has slack.
        offset_x = MARGIN + (usable_w - width * scale) / 2
        offset_y = MARGIN + (usable_h - height * scale) / 2
        return scale, x0, y0, offset_x, offset_y

    def _point(self, index: int, scale, x0, y0, ox, oy, source=None) -> QPointF:
        # Flip y: track coordinates count upward, widget coordinates downward.
        xs, ys = (self._x, self._y) if source is None else source
        return QPointF(
            ox + (xs[index] - x0) * scale,
            self.height() - (oy + (ys[index] - y0) * scale),
        )

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(theme.BG))
        if len(self._x) < 2:
            painter.setPen(QColor(theme.TEXT_DIM))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "This lap was recorded without track position, so it cannot be mapped.",
            )
            return
        transform = self._transform()

        if len(self._ref_x) >= 2:
            ref = (self._ref_x, self._ref_y)
            path = QPainterPath(self._point(0, *transform, source=ref))
            for i in range(1, len(self._ref_x)):
                path.lineTo(self._point(i, *transform, source=ref))
            pen = QPen(QColor(theme.BLUE), 2)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawPath(path)

        whole = QPainterPath(self._point(0, *transform))
        for i in range(1, len(self._x)):
            whole.lineTo(self._point(i, *transform))
        painter.setPen(QPen(QColor(theme.TEXT_DIM), 2))
        painter.drawPath(whole)

        first, last = self._span
        if last > first:
            stretch = QPainterPath(self._point(first, *transform))
            for i in range(first + 1, last + 1):
                stretch.lineTo(self._point(i, *transform))
            painter.setPen(QPen(QColor(theme.PURPLE), 4))
            painter.drawPath(stretch)

        if self._ref_cursor is not None and len(self._ref_x) >= 2:
            painter.setBrush(QColor(theme.BLUE))
            painter.setPen(QPen(QColor(theme.BLUE), 1))
            painter.drawEllipse(
                self._point(
                    self._ref_cursor, *transform, source=(self._ref_x, self._ref_y)
                ),
                5,
                5,
            )

        painter.setBrush(QColor(theme.GREEN))
        painter.setPen(QPen(QColor(theme.GREEN), 1))
        painter.drawEllipse(self._point(self._cursor, *transform), 6, 6)


class TrackReplay(QWidget):
    """Track map, transport controls, and the readout for the current instant."""

    cursorMoved = Signal(float)  # distance in metres

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lap: Lap | None = None
        self._reference: Lap | None = None
        self._first = 0
        self._last = 0

        self._title = QLabel()
        self._title.setStyleSheet("font-weight: 600;")
        self._map = TrackMap(self)
        self._readout = QLabel()
        self._readout.setStyleSheet(f"color: {theme.TEXT_DIM};")
        # What the coach said about this stretch, next to the stretch itself.
        # Reading "you braked earlier here" while watching the place it happened
        # is a different thing from reading it in a list.
        self._note = QLabel()
        self._note.setWordWrap(True)
        self._note.hide()
        self._legend = QLabel(
            f'<span style="color:{theme.GREEN}">●</span> this lap'
            f'    <span style="color:{theme.BLUE}">●</span> your best lap'
        )
        self._legend.hide()

        self._play = QPushButton("Play")
        self._play.setCheckable(True)
        self._play.toggled.connect(self._toggle)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.valueChanged.connect(self._seek)

        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._advance)

        controls = QHBoxLayout()
        controls.addWidget(self._play)
        controls.addWidget(self._slider, stretch=1)

        layout = QVBoxLayout(self)
        layout.addWidget(self._title)
        layout.addWidget(self._map, stretch=1)
        layout.addWidget(self._legend)
        layout.addWidget(self._readout)
        layout.addWidget(self._note)
        layout.addLayout(controls)

    def set_stretch(
        self,
        lap: Lap,
        d0: float,
        d1: float,
        title: str,
        *,
        reference: Lap | None = None,
        note: str = "",
    ) -> None:
        """Show the samples of `lap` between two distances, paused at the start.

        With a reference lap, both paths are drawn and both markers move -- the
        reference one matched by distance along the track rather than by time, so
        the two dots are always at the same place on the circuit and the gap
        between them is the thing being explained.
        """
        self._play.setChecked(False)
        self._lap = lap
        self._reference = reference if reference is not lap else None
        self._map.set_reference(self._reference)
        self._legend.setVisible(self._reference is not None and lap.has_track_map)
        self._note.setText(note)
        self._note.setVisible(bool(note))
        self._title.setText(title)
        self._first, self._last = span_indices(lap, d0, d1)
        self._slider.setRange(self._first, self._last)
        self._slider.setValue(self._first)
        if lap.has_track_map:
            self._map.set_lap(lap, self._first, self._last)
        else:
            self._map.clear()
        self._update_readout(self._first)

    @property
    def playing(self) -> bool:
        return self._timer.isActive()

    def _toggle(self, on: bool) -> None:
        self._play.setText("Pause" if on else "Play")
        if on:
            if self._slider.value() >= self._last:
                self._slider.setValue(self._first)
            self._timer.start()
        else:
            self._timer.stop()

    def _advance(self) -> None:
        """Step by the real time the driver took, so playback matches the drive."""
        if self._lap is None:
            return
        t = self._lap.df["t"]
        target = float(t.iloc[self._slider.value()]) + FRAME_MS / 1000.0
        index = int(t.searchsorted(target, side="left"))
        if index >= self._last:
            self._slider.setValue(self._last)
            self._play.setChecked(False)  # stop at the end rather than looping
            return
        self._slider.setValue(index)

    def _seek(self, index: int) -> None:
        self._map.set_cursor(index)
        self._update_readout(index)

    def _update_readout(self, index: int) -> None:
        if self._lap is None:
            return
        row = self._lap.df.iloc[index]
        elapsed = float(row["t"]) - float(self._lap.df["t"].iloc[self._first])
        text = (
            f"+{elapsed:4.1f} s   ·   {float(row['speed']) * 3.6:5.1f} km/h"
            f"   ·   throttle {_pedal_pct(row['throttle']):3.0f}%"
            f"   ·   brake {_pedal_pct(row['brake']):3.0f}%"
            f"   ·   gear {int(row['gear'])}"
        )
        text += self._reference_readout(float(row["dist"]), float(row["speed"]))
        self._readout.setText(text)
        self.cursorMoved.emit(float(row["dist"]))

    def _reference_readout(self, distance: float, speed: float) -> str:
        """The best lap at the same point on track, and the difference."""
        if self._reference is None:
            self._map.set_reference_cursor(None)
            return ""
        ref_dist = self._reference.df["dist"]
        index = int(min(ref_dist.searchsorted(distance, side="left"), len(ref_dist) - 1))
        self._map.set_reference_cursor(index)
        ref_speed = float(self._reference.df["speed"].iloc[index])
        delta = (speed - ref_speed) * 3.6
        return f"      best lap here: {ref_speed * 3.6:5.1f} km/h  ({delta:+.1f})"
