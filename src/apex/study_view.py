"""The screen the evaluation is argued from: one condition against another, per participant.

Every other view answers "how was this lap". This one answers the question the
project is judged on -- whether coaching changed how people drove -- and it does
that by pairing each participant with themselves. A between-groups claim rests on
the same rows, so the export here is the file that goes into R or SPSS.

Which two conditions are compared is chosen here rather than fixed. It was
baseline against coached and nothing else, which was the whole study when every
participant was coached; it stopped being the whole study the moment a control
arm existed. Those participants drive `baseline` and `control`, and a screen that
only knows two names showed them in the table, never paired them, and told the
researcher their data was incomplete. It was not: the comparison the screen could
not name is the one that separates coaching from practice.

It measures and shows; it does not test. Declaring significance is the analyst's
call in their own tool, and a p-value computed quietly by a viewer would be worth
less than one they can defend. Comparing each arm against its own baseline in
turn gives the two numbers that argument is made from; it does not make it.
"""

from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
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
from f1coach_core.participant import background_summary, load_background
from f1coach_core.study import PhaseSummary, summarise_all, summary_csv
from f1coach_core.workspace import list_sessions

BASELINE = "baseline"
COACHED = "coached"
CONTROL = "control"
FAMILIARISATION = "familiarisation"

# What the pickers offer first, in the order a session runs, so the list reads
# like the protocol rather than like the alphabet. Anything else follows, sorted:
# phase is a free slug in the file format, and a study is allowed to invent a
# condition without waiting for this viewer to be taught the name.
PHASE_ORDER = (BASELINE, COACHED, CONTROL, FAMILIARISATION)

# Which second condition to offer against the baseline when the data first
# arrives. Both are a measured second run; a participant is in one arm or the
# other, so whichever is present is the one worth opening on.
PREFERRED_SECOND = (COACHED, CONTROL)

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
    # A paired test compares each participant with themselves, but whether the
    # groups were comparable in the first place is an argument about prior
    # experience -- and it cannot be made from a column nobody can see. The
    # export has carried these all along; the screen did not.
    "Background",
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
        # clicked emits `checked`, which would arrive as `roots` and be iterated.
        refresh.clicked.connect(self._reload_button_clicked)
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

        self._left_phase = QComboBox()
        self._left_phase.setAccessibleName("Compare this phase")
        self._right_phase = QComboBox()
        self._right_phase.setAccessibleName("Against this phase")
        for picker, partner in (
            (self._left_phase, self._right_phase),
            (self._right_phase, self._left_phase),
        ):
            picker.currentTextChanged.connect(
                lambda _text, one=picker, other=partner: self._phase_picked(one, other)
            )
        against = QLabel("against")
        against.setStyleSheet(f"color: {theme.TEXT_DIM};")
        pickers = QHBoxLayout()
        pickers.addWidget(QLabel("Compare"))
        pickers.addWidget(self._left_phase)
        pickers.addWidget(against)
        pickers.addWidget(self._right_phase)
        pickers.addStretch(1)

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
        layout.addLayout(pickers)
        layout.addWidget(self._headline)
        layout.addWidget(split, stretch=1)

    # -- data ---------------------------------------------------------------

    def _reload_button_clicked(self, _checked: bool = False) -> None:
        self.reload()

    def reload(self, roots: list[Path] | None = None) -> None:
        """Read every session in the workspace, or the folders given."""
        from racecoach.granite.report import session_laps

        laps: list[Lap] = []
        for root in roots if roots is not None else list_sessions():
            laps.extend(session_laps(root))
        self._laps = laps
        self._summaries = summarise_all(laps)
        self._fill_table()
        self._offer_phases()
        self._headline.setText(self._describe())
        self._export.setEnabled(bool(self._summaries))
        self._draw_selected()

    # -- which two conditions ------------------------------------------------

    def _phases_present(self) -> list[str]:
        """Every condition the loaded laps actually carry, in protocol order."""
        found = {summary.phase for summary in self._summaries}
        known = [phase for phase in PHASE_ORDER if phase in found]
        return known + sorted(found - set(PHASE_ORDER))

    def _offer_phases(self) -> None:
        """Repopulate the pickers, keeping a choice the new data can still honour.

        Reloading after collecting one more session must not silently move the
        comparison somebody is reading. It only re-picks when what they had
        chosen is no longer there to choose.
        """
        phases = self._phases_present()
        wanted = [picker.currentText() for picker in (self._left_phase, self._right_phase)]
        for picker in (self._left_phase, self._right_phase):
            picker.blockSignals(True)
            picker.clear()
            picker.setEnabled(len(phases) > 1)
        self._left_phase.addItems(phases)
        # One condition is nothing to compare against, and an empty right-hand
        # picker says that; a second copy of the only answer would not.
        self._right_phase.addItems(phases if len(phases) > 1 else [])

        left = wanted[0] if wanted[0] in phases else self._default_left(phases)
        right = wanted[1] if wanted[1] in phases and wanted[1] != left else ""
        if not right:
            right = self._default_right(phases, left)
        self._left_phase.setCurrentText(left)
        self._right_phase.setCurrentText(right)
        for picker in (self._left_phase, self._right_phase):
            picker.blockSignals(False)

    @staticmethod
    def _default_left(phases: list[str]) -> str:
        return BASELINE if BASELINE in phases else (phases[0] if phases else "")

    @staticmethod
    def _default_right(phases: list[str], left: str) -> str:
        for phase in PREFERRED_SECOND:
            if phase in phases and phase != left:
                return phase
        return next((phase for phase in phases if phase != left), "")

    def _phase_picked(self, changed: QComboBox, other: QComboBox) -> None:
        chosen = changed.currentText()
        if chosen and chosen == other.currentText():
            # A phase against itself pairs every participant with themselves and
            # reports a difference of zero: true, and useless. Move the other
            # side rather than refusing the choice somebody just made.
            other.blockSignals(True)
            other.setCurrentText(self._default_right(self._phases_present(), chosen))
            other.blockSignals(False)
        self._headline.setText(self._describe())
        self._draw_selected()

    def _selected_phases(self) -> tuple[str, str]:
        return self._left_phase.currentText(), self._right_phase.currentText()

    # -- what it all says ----------------------------------------------------

    def _describe(self) -> str:
        if not self._summaries:
            return (
                "No laps carrying both a participant id and a phase yet. Collect "
                "Data records both, so a session driven through Apex will appear here."
            )
        left, right = self._selected_phases()
        drivers = {summary.driver for summary in self._summaries}
        if not right:
            return (
                f"{len(drivers)} participant(s), and every lap here is '{left}'. "
                f"A paired comparison needs the same participant in both '{left}' "
                "and a second phase; collect the other run and it appears here."
            )
        paired = self._paired()
        if not paired:
            phases = self._phases_present()
            return (
                f"{len(drivers)} participant(s), phases: {', '.join(phases)}. "
                f"A paired comparison needs the same participant in both "
                f"'{left}' and '{right}'."
            )
        improved = sum(1 for _d, before, after in paired if after.best_lap_s < before.best_lap_s)
        deltas = [after.best_lap_s - before.best_lap_s for _d, before, after in paired]
        mean_delta = sum(deltas) / len(deltas)
        return (
            f"{len(paired)} of {len(drivers)} participant(s) drove both '{left}' "
            f"and '{right}'. Best lap changed by {mean_delta:+.2f} s on average; "
            f"{improved} of {len(paired)} improved. Export the rows to test "
            "whether that is more than chance."
        )

    def _paired(self) -> list[tuple[str, PhaseSummary, PhaseSummary]]:
        """Participants who drove both chosen phases, which is what a paired test needs.

        Anybody who drove only one of them is not missing data; with a control
        arm they are simply in the other one, and they pair up under the other
        choice of phases.
        """
        left, right = self._selected_phases()
        if not left or not right or left == right:
            return []
        by_driver: dict[str, dict[str, PhaseSummary]] = {}
        for summary in self._summaries:
            by_driver.setdefault(summary.driver, {})[summary.phase] = summary
        return [
            (driver, phases[left], phases[right])
            for driver, phases in sorted(by_driver.items())
            if left in phases and right in phases
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
                background_summary(load_background(summary.driver)),
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if 6 <= column < len(HEADERS) - 1 and text == "":
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
        left, right = self._selected_phases()
        for phase, colour in ((left, theme.BLUE), (right, theme.GREEN)):
            if not phase:
                continue
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
