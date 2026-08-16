"""Compare screen: cumulative time-delta trace for any two laps of a session.

Doubles as the before/after-coaching view — pick the pre-coaching lap as A
and the post-coaching lap as B and the delta trace is the improvement.
"""

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from apex import theme
from f1coach_core import Lap, Session, corner_table, time_delta

AXIS_WIDTH = 64
NARRATE_THRESHOLD_S = 0.05  # corner deltas below this aren't worth a sentence


class CompareView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session: Session | None = None

        self._combo_a = QComboBox()
        self._combo_b = QComboBox()
        for combo in (self._combo_a, self._combo_b):
            combo.setMinimumWidth(200)
            combo.currentIndexChanged.connect(self._refresh)
        self._verdict = QLabel("")
        self._verdict.setStyleSheet(f"color: {theme.TEXT_DIM};")

        header = QHBoxLayout()
        header.addWidget(QLabel("Lap"))
        header.addWidget(self._combo_a)
        header.addSpacing(8)
        header.addWidget(QLabel("vs"))
        header.addWidget(self._combo_b)
        header.addStretch(1)
        header.addWidget(self._verdict)

        glw = pg.GraphicsLayoutWidget()
        glw.setBackground(theme.BG)
        self._speed_plot = glw.addPlot(row=0, col=0)
        self._speed_plot.setLabel("left", "speed (km/h)")
        self._speed_plot.hideAxis("bottom")
        self._delta_plot = glw.addPlot(row=1, col=0)
        self._delta_plot.setLabel("left", "Δ time (s)")
        self._delta_plot.setLabel("bottom", "distance", units="m")
        for plot in (self._speed_plot, self._delta_plot):
            plot.getAxis("left").enableAutoSIPrefix(False)
            plot.getAxis("left").setWidth(AXIS_WIDTH)
            plot.showGrid(x=True, y=True, alpha=theme.GRID_ALPHA)
        self._delta_plot.setXLink(self._speed_plot)
        glw.ci.layout.setRowStretchFactor(0, 3)
        glw.ci.layout.setRowStretchFactor(1, 2)

        self._speed_a = self._speed_plot.plot(pen=pg.mkPen(theme.BLUE, width=1.6))
        self._speed_b = self._speed_plot.plot(pen=pg.mkPen(theme.PURPLE, width=1.2))
        zero_pen = pg.mkPen("#6f6f6f", width=1, style=Qt.PenStyle.DashLine)
        self._delta_plot.addItem(pg.InfiniteLine(pos=0, angle=0, pen=zero_pen))
        self._delta_curve = self._delta_plot.plot(pen=pg.mkPen(theme.YELLOW, width=1.6))
        for curve in (self._speed_a, self._speed_b, self._delta_curve):
            curve.setDownsampling(auto=True, method="peak")
            # no clip-to-view here: it defers data until a paint happens, and
            # these traces are small enough that clipping buys nothing

        self._changed = QLabel("")
        self._remaining = QLabel("")
        for label in (self._changed, self._remaining):
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {theme.TEXT_DIM};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 4)
        layout.addLayout(header)
        layout.addWidget(glw, stretch=1)
        layout.addWidget(self._changed)
        layout.addWidget(self._remaining)

    def set_session(self, session: Session, lap_a: Lap | None = None) -> None:
        """Populate the pickers: A is the given lap (default: first non-best),
        B the session best — or the next-best when A already is the best."""
        self._session = session
        best = session.best_lap
        others = [lap for lap in session.laps if lap is not best]
        if lap_a is None:
            lap_a = others[0] if others else best
        if lap_a is best and others:
            lap_b = min(others, key=lambda lap: lap.lap_time)
        else:
            lap_b = best
        for combo, selected in ((self._combo_a, lap_a), (self._combo_b, lap_b)):
            combo.blockSignals(True)
            combo.clear()
            for lap in session.laps:
                combo.addItem(f"{lap.source.stem} · {lap.lap_time:.3f} s", lap)
                if lap is selected:
                    combo.setCurrentIndex(combo.count() - 1)
            combo.blockSignals(False)
        self._refresh()

    def clear_session(self) -> None:
        """Drop data whose managed session has been deleted."""
        self._session = None
        self._combo_a.clear()
        self._combo_b.clear()
        self._speed_a.setData([], [])
        self._speed_b.setData([], [])
        self._delta_curve.setData([], [])
        self._verdict.clear()
        self._changed.clear()
        self._remaining.clear()

    def _refresh(self) -> None:
        lap_a: Lap | None = self._combo_a.currentData()
        lap_b: Lap | None = self._combo_b.currentData()
        if lap_a is None or lap_b is None:
            return
        self._speed_a.setData(lap_a.df["dist"].to_numpy(dtype=float), lap_a.speed_kmh.to_numpy())
        self._speed_b.setData(lap_b.df["dist"].to_numpy(dtype=float), lap_b.speed_kmh.to_numpy())
        try:
            grid, delta = time_delta(lap_a, lap_b)
        except ValueError as exc:  # e.g. laps too short to share a distance grid
            self._delta_curve.setData([], [])
            self._verdict.setText(f"Can't compare: {exc}")
            self._changed.clear()
            self._remaining.clear()
            return
        self._delta_curve.setData(grid, delta)
        behind = float(delta[-1])
        verdict = "behind" if behind >= 0 else "ahead of"
        self._verdict.setText(
            f"{lap_a.source.stem} crosses the line {abs(behind):.3f} s {verdict} "
            f"{lap_b.source.stem}"
        )
        self._narrate(lap_a, lap_b)
        for plot in (self._speed_plot, self._delta_plot):
            plot.enableAutoRange()

    def _narrate(self, lap_a: Lap, lap_b: Lap) -> None:
        """The mockup's "What changed / Still on the table" lines, derived
        per corner with B (the after/right lap) measured against A."""
        rows = [] if lap_a is lap_b else corner_table(lap_b, lap_a)
        if not rows:
            self._changed.clear()
            self._remaining.clear()
            return
        b_name = lap_b.source.stem
        gained = min(rows, key=lambda row: row["delta_s"])
        lost = max(rows, key=lambda row: row["delta_s"])
        if gained["delta_s"] < -NARRATE_THRESHOLD_S:
            bits = []
            before, after = gained["ref_brake_point_m"], gained["brake_point_m"]
            if before is not None and after is not None and f"{before:,.0f}" != f"{after:,.0f}":
                bits.append(f"brake point {before:,.0f} → {after:,.0f} m")
            bits.append(
                f"min speed {gained['ref_min_speed_kmh']:.0f} →"
                f" {gained['min_speed_kmh']:.0f} km/h"
            )
            self._changed.setText(
                f"<b style='color:{theme.GREEN};'>What changed</b> — "
                f"{gained['corner']}: {' · '.join(bits)} · {gained['delta_s']:+.3f} s"
                f" for {b_name}"
            )
        else:
            self._changed.setText(
                f"<b style='color:{theme.GREEN};'>What changed</b> — no corner where "
                f"{b_name} gains more than {NARRATE_THRESHOLD_S:.2f} s"
            )
        if lost["delta_s"] > NARRATE_THRESHOLD_S:
            self._remaining.setText(
                f"<b style='color:{theme.YELLOW};'>Still on the table</b> — "
                f"{lost['corner']}: {b_name} loses {lost['delta_s']:+.3f} s. "
                f"Suggested focus for the next run."
            )
        else:
            self._remaining.setText(
                f"<b style='color:{theme.YELLOW};'>Still on the table</b> — nothing above "
                f"{NARRATE_THRESHOLD_S:.2f} s at corner level"
            )
