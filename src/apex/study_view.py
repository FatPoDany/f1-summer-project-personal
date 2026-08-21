"""The screen the evaluation is argued from: baseline against coached, per participant.

Every other view answers "how was this lap". This one answers the question the
project is judged on -- whether coaching changed how people drove -- and it does
that by pairing each participant with themselves. A between-groups claim rests on
the same rows, so the export here is the file that goes into R or SPSS.

It measures and shows; it does not test. Declaring significance is the analyst's
call in their own tool, and a p-value computed quietly by a viewer would be worth
less than one they can defend.
"""

from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from apex import theme
from f1coach_core.lap import Lap
from f1coach_core.study import PhaseSummary, summarise_all, summary_csv
from f1coach_core.workspace import list_sessions

BASELINE = "baseline"
COACHED = "coached"

HEADERS = (
    "Driver",
    "Phase",
    "Laps",
    "Best",
    "Mean",
    "SD",
    "Off track",
    "Time off",
    "Incidents",
)


class StudyView(QWidget):
    """Paired per-participant results, and the export a statistical test consumes."""

    status = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._summaries: list[PhaseSummary] = []
        self._laps: list[Lap] = []

        title = QLabel("Study results")
        title.setStyleSheet("font-weight: 600;")
        self._headline = QLabel("")
        self._headline.setWordWrap(True)
        self._headline.setStyleSheet(f"color: {theme.TEXT_DIM};")

        refresh = QPushButton("Reload")
        refresh.clicked.connect(self.reload)
        self._export = QPushButton("Export CSV…")
        self._export.setToolTip(
            "One row per participant per phase, for a paired test in R, SPSS or Python"
        )
        self._export.clicked.connect(self._export_csv)
        self._export.setEnabled(False)

        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(refresh)
        header.addWidget(self._export)

        self._table = QTableWidget(0, len(HEADERS))
        self._table.setHorizontalHeaderLabels(HEADERS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.currentCellChanged.connect(lambda *_: self._draw_selected())

        self._plot = pg.PlotWidget()
        self._plot.setBackground(theme.BG)
        self._plot.showGrid(x=True, y=True, alpha=theme.GRID_ALPHA)
        self._plot.setLabel("bottom", "Lap")
        self._plot.setLabel("left", "Lap time", units="s")
        self._plot.addLegend()

        split = QSplitter(Qt.Orientation.Vertical)
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(self._table)
        split.addWidget(top)
        split.addWidget(self._plot)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.addLayout(header)
        layout.addWidget(self._headline)
        layout.addWidget(split, stretch=1)

    # -- data ---------------------------------------------------------------

    def reload(self, roots: list[Path] | None = None) -> None:
        """Read every session in the workspace, or the folders given."""
        from racecoach.granite.report import session_laps

        laps: list[Lap] = []
        for root in roots if roots is not None else list_sessions():
            laps.extend(session_laps(root))
        self._laps = laps
        self._summaries = summarise_all(laps)
        self._fill_table()
        self._headline.setText(self._describe())
        self._export.setEnabled(bool(self._summaries))
        self._draw_selected()

    def _describe(self) -> str:
        if not self._summaries:
            return (
                "No laps carrying both a participant id and a phase yet. Collect "
                "Data records both, so a session driven through Apex will appear here."
            )
        paired = self._paired()
        drivers = {summary.driver for summary in self._summaries}
        if not paired:
            phases = sorted({summary.phase for summary in self._summaries})
            return (
                f"{len(drivers)} participant(s), phases: {', '.join(phases)}. "
                f"A paired comparison needs the same participant in both "
                f"'{BASELINE}' and '{COACHED}'."
            )
        improved = sum(1 for _d, before, after in paired if after.best_lap_s < before.best_lap_s)
        deltas = [after.best_lap_s - before.best_lap_s for _d, before, after in paired]
        mean_delta = sum(deltas) / len(deltas)
        return (
            f"{len(paired)} of {len(drivers)} participant(s) have both phases. "
            f"Best lap changed by {mean_delta:+.2f} s on average; {improved} "
            f"of {len(paired)} improved. Export the rows to test whether that is "
            "more than chance."
        )

    def _paired(self) -> list[tuple[str, PhaseSummary, PhaseSummary]]:
        """Participants who drove both phases, which is what a paired test needs."""
        by_driver: dict[str, dict[str, PhaseSummary]] = {}
        for summary in self._summaries:
            by_driver.setdefault(summary.driver, {})[summary.phase] = summary
        return [
            (driver, phases[BASELINE], phases[COACHED])
            for driver, phases in sorted(by_driver.items())
            if BASELINE in phases and COACHED in phases
        ]

    def _fill_table(self) -> None:
        self._table.setRowCount(len(self._summaries))
        for row, summary in enumerate(self._summaries):
            values = summary.to_row()
            cells = [
                summary.driver,
                summary.phase,
                str(summary.laps),
                f"{summary.best_lap_s:.2f}",
                f"{summary.mean_lap_s:.2f}",
                f"{summary.sd_lap_s:.2f}",
                str(values["off_track_events"]),
                str(values["off_track_seconds"]),
                str(values["damage_events"]),
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column >= 6 and text == "":
                    # Blank means the recording lacked the channel. Saying so
                    # stops it being read as a measured zero.
                    item.setText("—")
                    item.setToolTip("Not recorded in this session")
                self._table.setItem(row, column, item)

    def _draw_selected(self) -> None:
        self._plot.clear()
        row = self._table.currentRow()
        driver = (
            self._summaries[row].driver
            if 0 <= row < len(self._summaries)
            else (self._summaries[0].driver if self._summaries else None)
        )
        if driver is None:
            return
        for phase, colour in ((BASELINE, theme.BLUE), (COACHED, theme.GREEN)):
            times = [
                lap.lap_time
                for lap in self._laps
                if lap.identity.driver == driver and lap.identity.phase == phase
            ]
            if not times:
                continue
            self._plot.plot(
                range(1, len(times) + 1),
                times,
                pen=pg.mkPen(colour, width=2),
                symbol="o",
                symbolSize=6,
                symbolBrush=colour,
                name=f"{driver} · {phase}",
            )
        self._plot.setTitle(f"{driver} — lap times by phase")

    def _export_csv(self) -> None:
        target, _ = QFileDialog.getSaveFileName(
            self, "Export study summary", "apex-study-summary.csv", "CSV (*.csv)"
        )
        if not target:
            return
        try:
            Path(target).write_text(summary_csv(self._summaries), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Can't export summary", str(exc))
            return
        self.status.emit(f"{len(self._summaries)} rows -> {target}")
