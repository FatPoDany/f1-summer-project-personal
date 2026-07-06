"""Speed-vs-distance strip.

A0 shows one strip; A1 stacks throttle/brake beneath it on a shared distance
axis with a single crosshair (MoTeC-style).
"""

import pyqtgraph as pg
from PySide6.QtWidgets import QWidget

from f1coach_core import Lap

pg.setConfigOptions(antialias=True)

BACKGROUND = "#161616"  # Carbon gray-100 — dark-first, pit-wall convention
TRACE = "#78a9ff"  # Carbon blue-40; purple #be95ff stays reserved for "session best"
GRID_ALPHA = 0.12


class SpeedTraceWidget(pg.PlotWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, background=BACKGROUND)
        self.lap: Lap | None = None
        self.setLabel("bottom", "distance", units="m")
        self.setLabel("left", "speed (km/h)")
        self.getAxis("left").enableAutoSIPrefix(False)
        self.showGrid(x=True, y=True, alpha=GRID_ALPHA)
        self._curve = self.plot(pen=pg.mkPen(TRACE, width=1.6))
        # keep the paint path flat even on high-rate telemetry (design risk #2)
        self._curve.setDownsampling(auto=True, method="peak")
        self._curve.setClipToView(True)

    def set_lap(self, lap: Lap) -> None:
        self.lap = lap
        self._curve.setData(lap.df["dist"].to_numpy(), lap.speed_kmh.to_numpy())
        self.getPlotItem().enableAutoRange()
