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

    def set_lap(self, lap: Lap, first: int, last: int) -> None:
        self._x = lap.df["x"].tolist()
        self._y = lap.df["y"].tolist()
        self._span = (first, last)
        self._cursor = first
        self.update()

    def clear(self) -> None:
        """A lap without world position: say so rather than draw an empty box."""
        self._x, self._y = [], []
        self.update()

    def set_cursor(self, index: int) -> None:
        self._cursor = index
        self.update()

    def _transform(self) -> tuple[float, float, float, float, float]:
        """Scale and offset that fit the whole lap in view, aspect preserved."""
        x0, x1 = min(self._x), max(self._x)
        y0, y1 = min(self._y), max(self._y)
        width = max(x1 - x0, 1e-6)
        height = max(y1 - y0, 1e-6)
        usable_w = max(self.width() - 2 * MARGIN, 1)
        usable_h = max(self.height() - 2 * MARGIN, 1)
        scale = min(usable_w / width, usable_h / height)
        # Centre the drawing in whichever direction has slack.
        offset_x = MARGIN + (usable_w - width * scale) / 2
        offset_y = MARGIN + (usable_h - height * scale) / 2
        return scale, x0, y0, offset_x, offset_y

    def _point(self, index: int, scale, x0, y0, ox, oy) -> QPointF:
        # Flip y: track coordinates count upward, widget coordinates downward.
        return QPointF(
            ox + (self._x[index] - x0) * scale,
            self.height() - (oy + (self._y[index] - y0) * scale),
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

        painter.setBrush(QColor(theme.GREEN))
        painter.setPen(QPen(QColor(theme.GREEN), 1))
        painter.drawEllipse(self._point(self._cursor, *transform), 6, 6)


class TrackReplay(QWidget):
    """Track map, transport controls, and the readout for the current instant."""

    cursorMoved = Signal(float)  # distance in metres

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lap: Lap | None = None
        self._first = 0
        self._last = 0

        self._title = QLabel()
        self._title.setStyleSheet("font-weight: 600;")
        self._map = TrackMap(self)
        self._readout = QLabel()
        self._readout.setStyleSheet(f"color: {theme.TEXT_DIM};")

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
        layout.addWidget(self._readout)
        layout.addLayout(controls)

    def set_stretch(self, lap: Lap, d0: float, d1: float, title: str) -> None:
        """Show the samples of `lap` between two distances, paused at the start."""
        self._play.setChecked(False)
        self._lap = lap
        self._title.setText(title)
        dist = lap.df["dist"]
        # searchsorted keeps this exact for any sampling rate.
        self._first = int(dist.searchsorted(d0, side="left"))
        # side="right" lands one past the stretch, so step back to the last
        # sample actually inside it: the replay must not run beyond what the
        # debrief point is talking about.
        self._last = int(min(dist.searchsorted(d1, side="right") - 1, len(dist) - 1))
        if self._last <= self._first:
            self._last = min(self._first + 1, len(dist) - 1)
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
        self._readout.setText(
            f"+{elapsed:4.1f} s   ·   {float(row['speed']) * 3.6:5.1f} km/h"
            f"   ·   throttle {_pedal_pct(row['throttle']):3.0f}%"
            f"   ·   brake {_pedal_pct(row['brake']):3.0f}%"
            f"   ·   gear {int(row['gear'])}"
        )
        self.cursorMoved.emit(float(row["dist"]))
