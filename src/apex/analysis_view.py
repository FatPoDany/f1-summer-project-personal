"""Lap Analysis screen: reference picker + sector ribbon + synced strips,
with the AI Race Engineer panel docked on the right ("◈ show" zooms the
strips onto a finding's evidence zone)."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from apex import theme
from apex.coach_panel import CoachPanel
from apex.widgets.strip_stack import StripStack
from f1coach_core import Lap, Session, render_html_report, sector_times


class AnalysisView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lap: Lap | None = None
        self._session: Session | None = None

        self._title = QLabel("No lap loaded")
        self._title.setStyleSheet("font-weight: 600;")
        self._readout = QLabel("")
        self._readout.setStyleSheet(f"color: {theme.TEXT_DIM}; font-family: monospace;")
        self._ref_combo = QComboBox()
        self._ref_combo.setMinimumWidth(220)
        self._ref_combo.currentIndexChanged.connect(self._apply_reference)

        header = QHBoxLayout()
        header.addWidget(self._title)
        header.addStretch(1)
        header.addWidget(self._readout)
        header.addSpacing(12)
        header.addWidget(QLabel("Reference:"))
        header.addWidget(self._ref_combo)

        self._stack = StripStack(self)
        self._stack.cursorMoved.connect(self._update_readout)

        self._panel = CoachPanel(self)
        self._panel.setMinimumWidth(300)
        self._panel.evidenceRequested.connect(self._show_evidence)
        self._panel.viewResetRequested.connect(self._stack.reset_view)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._stack)
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

    def _show_evidence(self, d0: float, d1: float) -> None:
        self._stack.highlight_span(d0, d1)
        self._stack.zoom_to_span(d0, d1)

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
