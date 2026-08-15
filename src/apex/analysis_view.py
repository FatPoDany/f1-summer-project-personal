"""Lap Analysis screen: reference picker + sector ribbon + synced strips
over a corner table, with the AI Race Engineer panel docked on the right
("◈ show" and corner-row clicks zoom the strips onto the cited zone)."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from apex import theme
from apex.coach_panel import CoachPanel
from apex.widgets.strip_stack import StripStack
from f1coach_core import Lap, Session, corner_table, render_html_report, sector_times

CORNER_HEADERS = (
    "Corner",
    "Brake point",
    "Min speed",
    "Throttle 50%",
    "Exit +200 m",
    "Δ vs ref",
)
FLAG_THRESHOLD_S = 0.05  # a corner delta worth colouring / flagging at all


def _vs_m(mine: float | None, ref: float | None) -> str:
    """Mockup format: "4,362 · ref 4,404 m" — em-dash when never crossed."""

    def fmt(value: float | None) -> str:
        return f"{value:,.0f}" if value is not None else "—"

    if mine is None and ref is None:
        return "—"
    return f"{fmt(mine)} · ref {fmt(ref)} m"


class AnalysisView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lap: Lap | None = None
        self._session: Session | None = None
        self._corner_rows: list[dict] = []

        self._title = QLabel("No lap loaded")
        self._title.setStyleSheet("font-weight: 600;")
        self._readout = QLabel("")
        self._readout.setStyleSheet(f"color: {theme.TEXT_DIM}; font-family: monospace;")
        self._theoretical = QLabel("")
        self._theoretical.setStyleSheet(f"color: {theme.PURPLE};")
        self._theoretical.setToolTip("Best sectors combined — what a clean lap is worth")
        self._ref_combo = QComboBox()
        self._ref_combo.setMinimumWidth(220)
        self._ref_combo.currentIndexChanged.connect(self._apply_reference)

        header = QHBoxLayout()
        header.addWidget(self._title)
        header.addStretch(1)
        header.addWidget(self._readout)
        header.addSpacing(12)
        header.addWidget(self._theoretical)
        header.addSpacing(12)
        header.addWidget(QLabel("Reference:"))
        header.addWidget(self._ref_combo)

        self._stack = StripStack(self)
        self._stack.cursorMoved.connect(self._update_readout)

        self._corners = QTableWidget(0, len(CORNER_HEADERS))
        self._corners.setHorizontalHeaderLabels(CORNER_HEADERS)
        self._corners.horizontalHeader().setStretchLastSection(True)
        self._corners.verticalHeader().setVisible(False)
        self._corners.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._corners.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._corners.setMaximumHeight(190)
        self._corners.setToolTip("Click a corner to zoom the strips onto its zone")
        self._corners.cellClicked.connect(self._zoom_corner_row)
        self._corners.hide()

        self._panel = CoachPanel(self)
        self._panel.setMinimumWidth(300)
        self._panel.evidenceRequested.connect(self._show_evidence)
        self._panel.viewResetRequested.connect(self._stack.reset_view)

        charts = QWidget()
        charts_layout = QVBoxLayout(charts)
        charts_layout.setContentsMargins(0, 0, 0, 0)
        charts_layout.setSpacing(4)
        charts_layout.addWidget(self._stack, stretch=1)
        charts_layout.addWidget(self._corners)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(charts)
        splitter.addWidget(self._panel)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([920, 340])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 4)
        layout.addLayout(header)
        layout.addWidget(splitter, stretch=1)

    @property
    def lap(self) -> Lap | None:
        return self._lap

    def set_context(self, lap: Lap, session: Session | None) -> None:
        self._lap, self._session = lap, session
        title = f"{lap.source.stem} — {lap.lap_time:.3f} s"
        if session is not None:
            title = f"{session.name} · {title}"
        self._title.setText(title)
        best_sectors = session.best_sector_times if session is not None else {}
        self._theoretical.setText(
            f"Theoretical {sum(best_sectors.values()):.3f} s" if best_sectors else ""
        )
        self._rebuild_reference_combo()
        self._apply_reference()

    # -- reference handling ------------------------------------------------

    def _rebuild_reference_combo(self) -> None:
        self._ref_combo.blockSignals(True)
        self._ref_combo.clear()
        self._ref_combo.addItem("No reference", None)
        default_index = 0
        if self._session is not None and self._lap is not None:
            others = [lap for lap in self._session.laps if lap is not self._lap]
            default = self._default_reference(others)
            for lap in others:
                self._ref_combo.addItem(f"{lap.source.stem} · {lap.lap_time:.3f} s", lap)
                if lap is default:
                    default_index = self._ref_combo.count() - 1
        self._ref_combo.setCurrentIndex(default_index)
        self._ref_combo.blockSignals(False)

    def _default_reference(self, others: list[Lap]) -> Lap | None:
        """Session best — unless this lap IS the best, then the next-best lap."""
        if not others or self._session is None or self._lap is None:
            return None
        best = self._session.best_lap
        return min(others, key=lambda lap: lap.lap_time) if best is self._lap else best

    def _apply_reference(self) -> None:
        if self._lap is None:
            return
        reference = self._ref_combo.currentData()
        self._stack.set_lap(self._lap, self._sector_colors(reference))
        self._stack.set_reference(reference)
        self._panel.set_context(self._lap, reference)
        self._populate_corners(reference)

    def _show_evidence(self, d0: float, d1: float) -> None:
        self._stack.highlight_span(d0, d1)
        self._stack.zoom_to_span(d0, d1)

    # -- corner table ------------------------------------------------------

    def _populate_corners(self, reference: Lap | None) -> None:
        self._corner_rows = []
        if self._lap is None or reference is None:
            self._corners.hide()
            return
        try:
            rows = corner_table(self._lap, reference)
        except ValueError:  # laps too short to share a distance grid
            rows = []
        if not rows:
            self._corners.hide()
            return
        self._corner_rows = rows
        worst = max(range(len(rows)), key=lambda i: rows[i]["delta_s"])
        self._corners.setRowCount(len(rows))
        for i, row in enumerate(rows):
            delta = row["delta_s"]
            flagged = i == worst and delta > FLAG_THRESHOLD_S
            cells = (
                f"{row['corner']} ⚠" if flagged else row["corner"],
                _vs_m(row["brake_point_m"], row["ref_brake_point_m"]),
                f"{row['min_speed_kmh']:.0f} / {row['ref_min_speed_kmh']:.0f}",
                _vs_m(row["throttle_point_m"], row["ref_throttle_point_m"]),
                f"{row['exit_speed_kmh']:.0f} / {row['ref_exit_speed_kmh']:.0f}",
                f"{delta:+.3f}",
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 0 and flagged:
                    item.setForeground(QColor(theme.RED))
                if col == len(cells) - 1:
                    if delta > FLAG_THRESHOLD_S:
                        item.setForeground(QColor(theme.RED))
                    elif delta < -FLAG_THRESHOLD_S:
                        item.setForeground(QColor(theme.GREEN))
                    else:
                        item.setForeground(QColor(theme.TEXT_DIM))
                self._corners.setItem(i, col, item)
        self._corners.resizeColumnsToContents()
        self._corners.show()

    def _zoom_corner_row(self, row: int, _col: int = 0) -> None:
        if 0 <= row < len(self._corner_rows):
            d0, d1 = self._corner_rows[row]["span_m"]
            self._show_evidence(d0, d1)

    # -- report export ----------------------------------------------------

    def export_report(self, path: str | Path) -> Path:
        """Write the current analysis (lap, reference, findings) as one HTML file."""
        assert self._lap is not None, "no lap on screen to export"
        document = render_html_report(
            self._lap,
            self._ref_combo.currentData(),
            self._panel.report,
            session_name=self._session.name if self._session else None,
        )
        path = Path(path)
        path.write_text(document, encoding="utf-8")
        return path

    def _sector_colors(self, reference: Lap | None) -> dict[int, str]:
        """Timing-screen colours: purple = session-best sector, green = faster
        than the reference, yellow = slower."""
        if self._lap is None or reference is None or self._session is None:
            return {}
        mine = sector_times(self._lap)
        ref = sector_times(reference)
        session_best = self._session.best_sector_times
        colors: dict[int, str] = {}
        for sector, seconds in mine.items():
            if sector in session_best and seconds <= session_best[sector] + 1e-9:
                colors[sector] = theme.PURPLE
            elif sector in ref:
                colors[sector] = theme.GREEN if seconds < ref[sector] else theme.YELLOW
        return colors

    # -- readout -------------------------------------------------------------

    def _update_readout(self, values: dict | None) -> None:
        if not values:
            self._readout.setText("")
            return
        text = (
            f"{values['dist']:.0f} m · {values['speed_kmh']:.0f} km/h"
            f" · thr {values['throttle_pct']:.0f}% · brk {values['brake_pct']:.0f}%"
        )
        if "gear" in values:
            text += f" · gear {values['gear']}"
        self._readout.setText(text)
