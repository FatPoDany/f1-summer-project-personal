"""MoTeC-style strip stack: sector ribbon + speed/throttle/brake on one
shared distance axis, with a single crosshair across all strips."""

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from apex import theme
from f1coach_core import Lap, sector_spans

pg.setConfigOptions(antialias=True)

CHANNELS = (  # (df column, axis label, pen colour)
    ("speed", "Speed (km/h)", theme.BLUE),
    ("throttle", "Throttle (%)", theme.GREEN),
    ("brake", "Brake (%)", theme.RED),
)
AXIS_WIDTH = 64


def _channel_series(lap: Lap, column: str) -> np.ndarray:
    if column == "speed":
        return lap.speed_kmh.to_numpy()
    return lap.df[column].to_numpy() * 100.0


class StripStack(pg.GraphicsLayoutWidget):
    cursorMoved = Signal(object)  # dict of values at the cursor, or None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setBackground(theme.SURFACE_1)
        self.lap: Lap | None = None
        self.reference: Lap | None = None
        self._dist = np.empty(0)
        self._series: dict[str, np.ndarray] = {}
        self._highlights: list[tuple[pg.PlotItem, pg.LinearRegionItem]] = []

        self._ribbon = self.addPlot(row=0, col=0)
        self._ribbon.setYRange(0, 1, padding=0)
        self._ribbon.setMouseEnabled(x=True, y=False)
        self._ribbon.hideButtons()
        self._ribbon.setMenuEnabled(False)
        self._ribbon.hideAxis("bottom")
        axis = self._ribbon.getAxis("left")
        axis.setStyle(showValues=False)
        axis.setTicks([])
        # TORCS exports no sectors, so S1-S3 are thirds of the track length that
        # Apex derives. Unlabelled they read as the circuit's official timing
        # sectors, which they are not, and a reader would compare them with
        # broadcast splits that mean something else.
        self._ribbon.setToolTip(
            "Sectors: equal thirds of the lap distance, derived by Apex — TORCS "
            "records no timing sectors. Colour is this lap against the reference."
        )
        self._ribbon_items: list[pg.GraphicsObject] = []

        self._strips: list[pg.PlotItem] = []
        self._curves: list[pg.PlotDataItem] = []
        self._ref_curves: list[pg.PlotDataItem] = []
        self._vlines: list[pg.InfiniteLine] = []
        for i, (column, label, colour) in enumerate(CHANNELS, start=1):
            strip = self.addPlot(row=i, col=0)
            strip.setLabel("left", label)
            strip.getAxis("left").enableAutoSIPrefix(False)
            strip.showGrid(x=True, y=True, alpha=theme.GRID_ALPHA)
            if i < len(CHANNELS):
                strip.hideAxis("bottom")
            else:
                strip.setLabel("bottom", "distance", units="m")
            ref = strip.plot(pen=pg.mkPen(theme.PURPLE, width=1.35))
            curve = strip.plot(pen=pg.mkPen(colour, width=2.0))
            if column != "speed":
                curve.setFillLevel(0)
                curve.setBrush(pg.mkBrush(colour + "14"))
            for item in (ref, curve):
                item.setDownsampling(auto=True, method="peak")
                item.setClipToView(True)
            vline = pg.InfiniteLine(
                angle=90, movable=False, pen=pg.mkPen(theme.TEXT_MUTED + "88", width=1)
            )
            vline.hide()
            strip.addItem(vline, ignoreBounds=True)
            self._strips.append(strip)
            self._curves.append(curve)
            self._ref_curves.append(ref)
            self._vlines.append(vline)

        base = self._strips[0]
        self._ribbon.setXLink(base)
        for strip in self._strips[1:]:
            strip.setXLink(base)
        for plot in (self._ribbon, *self._strips):
            for name in ("left", "bottom"):
                axis = plot.getAxis(name)
                axis.setPen(pg.mkPen(theme.BORDER))
                axis.setTextPen(pg.mkPen(theme.TEXT_MUTED))
            plot.getAxis("left").setWidth(AXIS_WIDTH)

        layout = self.ci.layout
        layout.setRowFixedHeight(0, 30)
        layout.setVerticalSpacing(6)
        for row, stretch in ((1, 5), (2, 3), (3, 3)):
            layout.setRowStretchFactor(row, stretch)

        self._proxy = pg.SignalProxy(
            self.scene().sigMouseMoved, rateLimit=60, slot=self._on_mouse
        )

    # -- data ------------------------------------------------------------

    def set_lap(self, lap: Lap, sector_colors: dict[int, str] | None = None) -> None:
        self.lap = lap
        self.clear_highlight()
        self._dist = lap.df["dist"].to_numpy(dtype=float)
        self._series = {col: _channel_series(lap, col) for col, _, _ in CHANNELS}
        for curve, (col, _, _) in zip(self._curves, CHANNELS, strict=True):
            curve.setData(self._dist, self._series[col])
        self._rebuild_ribbon(sector_colors or {})
        for strip in self._strips:
            strip.enableAutoRange()

    def set_reference(self, reference: Lap | None) -> None:
        self.reference = reference
        for ref_curve, (col, _, _) in zip(self._ref_curves, CHANNELS, strict=True):
            if reference is None:
                ref_curve.setData([], [])
            else:
                ref_curve.setData(
                    reference.df["dist"].to_numpy(dtype=float),
                    _channel_series(reference, col),
                )

    def clear_lap(self) -> None:
        """Drop plotted data when its owning session is removed."""
        self.lap = None
        self.reference = None
        self._dist = np.empty(0)
        self._series = {}
        self.clear_highlight()
        for curve in (*self._curves, *self._ref_curves):
            curve.setData([], [])
        for vline in self._vlines:
            vline.hide()
        self._rebuild_ribbon({})
        self.cursorMoved.emit(None)

    def values_at(self, x: float) -> dict | None:
        """Channel values at (or nearest to) distance x — what the readout shows."""
        if self.lap is None or self._dist.size == 0:
            return None
        idx = int(np.clip(np.searchsorted(self._dist, x), 0, self._dist.size - 1))
        values = {
            "dist": float(self._dist[idx]),
            "speed_kmh": float(self._series["speed"][idx]),
            "throttle_pct": float(self._series["throttle"][idx]),
            "brake_pct": float(self._series["brake"][idx]),
        }
        if "gear" in self.lap.df.columns:
            values["gear"] = int(self.lap.df["gear"].iloc[idx])
        return values

    # -- evidence-zoom ------------------------------------------------------

    def highlight_span(self, d0: float, d1: float) -> None:
        """Shade an evidence zone across every strip (the "◈ show" target)."""
        self.clear_highlight()
        for strip in self._strips:
            region = pg.LinearRegionItem(
                values=(d0, d1),
                movable=False,
                brush=pg.mkBrush(theme.BLUE + "26"),
                pen=pg.mkPen(theme.BLUE + "66"),
            )
            region.setZValue(-10)
            strip.addItem(region)
            self._highlights.append((strip, region))

    def clear_highlight(self) -> None:
        for strip, region in self._highlights:
            strip.removeItem(region)
        self._highlights.clear()

    def zoom_to_span(self, d0: float, d1: float, margin: float = 60.0) -> None:
        self._strips[0].setXRange(d0 - margin, d1 + margin, padding=0)

    def reset_view(self) -> None:
        self.clear_highlight()
        for strip in self._strips:
            strip.enableAutoRange()

    # -- internals ---------------------------------------------------------

    def _rebuild_ribbon(self, colors: dict[int, str]) -> None:
        for item in self._ribbon_items:
            self._ribbon.removeItem(item)
        self._ribbon_items.clear()
        if self.lap is None:
            return

        spans = sector_spans(self.lap)
        if not spans and self._dist.size:
            spans = [(0, float(self._dist[0]), float(self._dist[-1]))]
        x0 = np.array([s[1] for s in spans])
        x1 = np.array([s[2] for s in spans])
        brushes = [colors.get(s[0], theme.NEUTRAL) for s in spans]
        bars = pg.BarGraphItem(
            x0=x0,
            x1=x1,
            y0=0,
            y1=1,
            brushes=brushes,
            pen=pg.mkPen(theme.SURFACE_1, width=2),
        )
        self._ribbon.addItem(bars)
        self._ribbon_items.append(bars)
        for (sector, d0, d1), brush in zip(spans, brushes, strict=True):
            if sector == 0:
                continue  # placeholder span for laps without sector data
            text_colour = theme.BG if brush != theme.NEUTRAL else theme.TEXT_DIM
            label = pg.TextItem(f"S{sector}", color=text_colour, anchor=(0.5, 0.5))
            label.setPos((d0 + d1) / 2.0, 0.5)
            self._ribbon.addItem(label)
            self._ribbon_items.append(label)

    def _on_mouse(self, event: tuple) -> None:
        pos = event[0]
        inside = self.lap is not None and any(
            strip.sceneBoundingRect().contains(pos) for strip in self._strips
        )
        if not inside:
            for vline in self._vlines:
                vline.hide()
            self.cursorMoved.emit(None)
            return
        x = self._strips[0].vb.mapSceneToView(pos).x()
        x = float(np.clip(x, self._dist[0], self._dist[-1]))
        for vline in self._vlines:
            vline.setPos(x)
            vline.show()
        self.cursorMoved.emit(self.values_at(x))
