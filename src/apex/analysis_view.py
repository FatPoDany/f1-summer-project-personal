"""Lap Analysis screen: reference picker + sector ribbon + synced strips
over a corner table, with the AI Race Engineer panel docked on the right
("◈ show" and corner-row clicks zoom the strips onto the cited zone)."""

from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool
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
from apex.captions import reference_caption
from apex.coach_panel import CoachPanel
from apex.widgets.replay_window import ReplayWindow
from apex.widgets.strip_stack import StripStack
from apex.widgets.track_replay import TrackMap, span_indices
from f1coach_core import (
    DebriefPoint,
    Lap,
    Session,
    corner_review_points,
    corner_table,
    debrief_summary,
    lap_debrief,
    render_html_report,
    sector_times,
    single_lap_corner_table,
)
from f1coach_core.workspace import session_recording
from racecoach.granite.narrate import NarratedPoint
from racecoach.granite.server import GraniteServer

COMPARISON_HEADERS = (
    "Corner",
    "Brake point",
    "Min speed",
    "Throttle 50%",
    "Exit +200 m",
    "Δ vs ref",
    "Technique review",
)
SINGLE_LAP_HEADERS = (
    "Corner",
    "Brake point",
    "Min speed",
    "Throttle 50%",
    "Exit +200 m",
    "Technique review",
)
FLAG_THRESHOLD_S = 0.05  # a corner delta worth colouring / flagging at all


def _vs_m(mine: float | None, ref: float | None) -> str:
    """Mockup format: "4,362 · ref 4,404 m" — em-dash when never crossed."""

    def fmt(value: float | None) -> str:
        return f"{value:,.0f}" if value is not None else "—"

    if mine is None and ref is None:
        return "—"
    return f"{fmt(mine)} · ref {fmt(ref)} m"


def _single_m(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f} m"


def _single_speed(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f} km/h"


class AnalysisView(QWidget):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        coach_pool: QThreadPool | None = None,
        coach_server: GraniteServer | None = None,
    ) -> None:
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
        header.addWidget(QLabel("Compare with:"))
        header.addWidget(self._ref_combo)

        self._stack = StripStack(self)
        self._stack.cursorMoved.connect(self._update_readout)

        # The strips answer "what did the inputs do"; this answers "where".  A
        # cited stretch is a place on the circuit before it is a region of a
        # chart, and a participant who has driven the track reads the corner
        # long before they read the trace.
        self._track = TrackMap()
        self._track.setMinimumWidth(190)
        self._track.setMaximumWidth(320)
        self._track.setToolTip(
            "Where on the circuit the cited stretch is. The marker sits at its start."
        )
        self._track_caption = QLabel("Track")
        self._track_caption.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 11px;")

        self._corners = QTableWidget(0, len(COMPARISON_HEADERS))
        self._corners.setHorizontalHeaderLabels(COMPARISON_HEADERS)
        self._corners.horizontalHeader().setStretchLastSection(True)
        self._corners.verticalHeader().setVisible(False)
        self._corners.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._corners.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._corners.setMaximumHeight(190)
        self._corners.setToolTip(
            "Click a corner to zoom the strips and track onto it; "
            "double-click to review it with the race footage"
        )
        self._corners.cellClicked.connect(self._zoom_corner_row)
        self._corners.cellDoubleClicked.connect(self._review_corner)
        self._corners.hide()

        # A participant reads this, not the corner table: the few stretches that
        # cost time, in plain language. Clicking one zooms the strips onto it.
        self._debrief_heading = QLabel()
        self._debrief_heading.setWordWrap(True)
        self._debrief_heading.setStyleSheet("font-weight: 600;")
        self._debrief_heading.hide()
        # The list of costly stretches that used to sit here said the same thing
        # as the corner table three rows further down -- same corners, same
        # deltas, one ranked and one in lap order -- and was a second place to
        # click for the same review. The sentence above it is not in the table,
        # so that stays.
        debrief_header = QHBoxLayout()
        debrief_header.addWidget(self._debrief_heading, stretch=1)
        self._debrief_points: list[DebriefPoint] = []
        # Every corner, in lap order: the review window's own index space. The
        # debrief lists only the stretches that cost time, which is the wrong
        # set for a table where a participant picks a corner by name -- a corner
        # they were quick through still has footage of them being quick through
        # it, and they still want to see it.
        self._review_points: list[DebriefPoint] = []
        # Prose the coach produced, by row, so a replay can show what was said
        # about the stretch it is playing.
        self._narration: dict[int, str] = {}
        # The narrated points themselves, so the review window can separate what
        # was observed from what to do about it.
        self._advice: dict = {}
        self._advice_complete = False
        self._reference: Lap | None = None
        self._replay_window: ReplayWindow | None = None

        self._panel = CoachPanel(self, pool=coach_pool, server=coach_server)
        self._panel.setMinimumWidth(300)
        self._panel.evidenceRequested.connect(self._show_evidence)
        self._panel.viewResetRequested.connect(self._stack.reset_view)
        self._panel.reportReady.connect(self._apply_report_advice)
        self._panel.debriefNarrated.connect(self._apply_narration)

        track_column = QVBoxLayout()
        track_column.setContentsMargins(0, 0, 0, 0)
        track_column.setSpacing(2)
        track_column.addWidget(self._track_caption)
        track_column.addWidget(self._track, stretch=1)

        traces = QHBoxLayout()
        traces.setContentsMargins(0, 0, 0, 0)
        traces.setSpacing(8)
        traces.addWidget(self._stack, stretch=4)
        traces.addLayout(track_column, stretch=1)

        charts = QWidget()
        charts_layout = QVBoxLayout(charts)
        charts_layout.setContentsMargins(0, 0, 0, 0)
        charts_layout.setSpacing(4)
        charts_layout.addLayout(traces, stretch=1)
        charts_layout.addLayout(debrief_header)
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

    def clear_context(self) -> None:
        """Invalidate lap data after its managed session has been deleted."""
        self._lap = None
        self._session = None
        self._corner_rows = []
        self._title.setText("No lap loaded")
        self._readout.clear()
        self._theoretical.clear()
        self._ref_combo.blockSignals(True)
        self._ref_combo.clear()
        self._ref_combo.addItem("Single-lap analysis", None)
        self._ref_combo.blockSignals(False)
        self._corners.setRowCount(0)
        self._corners.hide()
        self._stack.clear_lap()
        self._track.clear()
        self._panel.set_context(None, None)

    # -- reference handling ------------------------------------------------

    def _rebuild_reference_combo(self) -> None:
        self._ref_combo.blockSignals(True)
        self._ref_combo.clear()
        self._ref_combo.addItem("Single-lap analysis", None)
        default = 0
        if self._session is not None and self._lap is not None:
            best = self._session.best_lap
            others = [lap for lap in self._session.laps if lap is not self._lap]
            for lap in others:
                self._ref_combo.addItem(reference_caption(lap, best), lap)
            # Every non-best lap opens against the session best so its debrief is
            # immediately useful. The best lap stays a single-lap technique review:
            # comparing it to a slower lap would create a different context from
            # the report the Garage has already generated and make Granite run twice.
            if others and self._lap is not best:
                default = self._ref_combo.findData(best)
        self._ref_combo.setCurrentIndex(max(default, 0))
        self._ref_combo.blockSignals(False)

    def _apply_reference(self) -> None:
        if self._lap is None:
            return
        reference = self._ref_combo.currentData()
        self._stack.set_lap(self._lap, self._sector_colors(reference))
        self._stack.set_reference(reference)
        self._draw_track(reference)
        self._panel.set_context(self._lap, reference)
        self._populate_debrief(reference)
        self._populate_corners(reference)

    def _draw_track(self, reference: Lap | None) -> None:
        """The whole lap, with nothing picked out until something is cited."""
        if self._lap is None or not self._lap.has_track_map:
            self._track.clear()
            return
        self._track.set_reference(reference if reference is not self._lap else None)
        self._track.set_lap(self._lap, 0, 0)

    def _show_evidence(self, d0: float, d1: float) -> None:
        self._stack.highlight_span(d0, d1)
        self._stack.zoom_to_span(d0, d1)
        if self._lap is not None and self._lap.has_track_map:
            self._track.set_lap(self._lap, *span_indices(self._lap, d0, d1))

    # -- driver debrief ----------------------------------------------------

    def _populate_debrief(self, reference: Lap | None) -> None:
        """Deterministic, and independent of whether a model ever runs."""
        if self._replay_window is not None:
            self._replay_window.close()
            self._replay_window = None
        self._reference = reference
        self._debrief_points = []
        self._narration = {}
        self._advice = {}
        self._advice_complete = False
        if self._lap is None or reference is None or reference is self._lap:
            self._debrief_heading.hide()
            return
        try:
            points = lap_debrief(self._lap, reference)
        except ValueError:  # laps too short to share a distance grid
            points = []
        self._debrief_heading.setText(debrief_summary(self._lap, reference, points))
        self._debrief_heading.show()
        self._debrief_points = points
        self._panel.set_debrief(self._debrief_heading.text(), points)

    def _apply_narration(self, result: object) -> None:
        """Keep the coach's words with the stretch they are about.

        They go on the row as a tooltip and into the replay, not into the row
        text: a list item does not wrap, and a sentence pushed into one runs off
        the end where nobody can read it.
        """
        narrated = getattr(result, "points", ())
        if len(narrated) != len(self._debrief_points):
            return
        for row, item in enumerate(narrated):
            # The coach narrates the debrief stretches; the review window is
            # indexed by corner. Same corners, different order and length, so
            # the note is carried across by name rather than by position.
            index = self._review_index(self._debrief_points[row].corner)
            if index is not None:
                self._advice[index] = item
            text = getattr(item, "full_text", "") or item.narration
            if not text:
                continue
            if index is not None:
                self._narration[index] = text
        summary = getattr(result, "summary", "")
        if summary:
            self._debrief_heading.setText(summary)

    def _apply_report_advice(self, report: object) -> None:
        """Reuse validated report prose only where its citation matches the stretch.

        The report has already passed the coaching response validator. Requiring
        both the deterministic corner label and exact evidence span here prevents
        a sound instruction for one turn from appearing beside another turn's
        footage.
        """
        self._narration = {}
        self._advice = {}
        findings = getattr(report, "findings", ())
        for index, point in enumerate(self._review_points):
            finding = next(
                (
                    item
                    for item in findings
                    if any(
                        evidence.corner == point.corner
                        and evidence.span == point.span_m
                        for evidence in item.evidence
                    )
                ),
                None,
            )
            if finding is None:
                continue
            observation = " ".join(
                text for text in (finding.issue, finding.cause) if text
            )
            narrated = NarratedPoint(
                point=point,
                narration=observation,
                advice=finding.action,
            )
            self._advice[index] = narrated
            self._narration[index] = narrated.full_text
        self._advice_complete = True
        if self._replay_window is not None:
            self._replay_window.update_advice(self._advice, complete=True)

    def coach_shutdown(self) -> None:
        self._panel.shutdown()

    def _session_dir(self) -> Path | None:
        """The folder this lap's canonical CSV lives in, which is its session."""
        return self._lap.source.parent if self._lap is not None else None

    def _session_recording(self):
        directory = self._session_dir()
        return session_recording(directory) if directory is not None else None

    def _recording_of(self, lap: Lap | None):
        """The recording behind whichever session a lap was driven in."""
        return session_recording(lap.source.parent) if lap is not None else None

    def _clips_dir(self) -> Path:
        """Beside the laps, so clips travel with the session they explain."""
        directory = self._session_dir()
        return (directory or Path.cwd()) / "clips"

    def _review_index(self, corner: str) -> int | None:
        """Where a named corner sits in the review window's list."""
        return next(
            (i for i, point in enumerate(self._review_points) if point.corner == corner),
            None,
        )

    def _review_corner(self, row: int, _col: int = 0) -> None:
        """Double-clicking a corner row reviews that corner."""
        self._open_review(row)

    def _open_review(self, index: int) -> None:
        """Play one corner back on the track and in the footage, in its own window.

        A separate window rather than another panel: the analysis screen is
        already dense, and a participant watching a replay is not reading traces
        at the same time.
        """
        if self._lap is None or not 0 <= index < len(self._review_points):
            return
        point = self._review_points[index]
        # Keep the traces and the track showing the corner being reviewed.
        self._show_evidence(*point.span_m)
        if self._replay_window is None:
            self._replay_window = ReplayWindow(self)
        self._replay_window.show_stretch(
            self._lap,
            point,
            reference=self._reference,
            note=self._narration.get(index, ""),
            # Every corner travels with it, so the review window can move
            # between them without sending the participant back here.
            points=self._review_points,
            notes=self._narration,
            advice=self._advice,
            advice_complete=self._advice_complete,
            recording=self._session_recording(),
            # The compared lap's footage comes from its own session's recording:
            # normally the same file, and named per lap so it stays right if a
            # reference ever comes from somewhere else.
            reference_recording=self._recording_of(self._reference),
            clips_dir=self._clips_dir(),
        )

    # -- corner table ------------------------------------------------------

    def _populate_corners(self, reference: Lap | None) -> None:
        self._corner_rows = []
        if self._lap is None:
            self._corners.hide()
            return
        try:
            rows = (
                corner_table(self._lap, reference)
                if reference is not None
                else single_lap_corner_table(self._lap)
            )
            # Built here, from the same call, so a row and its reviewable
            # stretch cannot drift apart: both walk the corners in lap order.
            self._review_points = corner_review_points(self._lap, reference)
        except ValueError:  # laps too short to share a distance grid
            rows = []
            self._review_points = []
        if not rows:
            self._corners.hide()
            return
        self._corner_rows = rows
        comparison = reference is not None
        headers = COMPARISON_HEADERS if comparison else SINGLE_LAP_HEADERS
        # The two modes differ by one column: a delta only exists against a
        # reference. Both now end on Technique review, which is the column worth
        # the stretched last section -- a delta is five characters wide and left
        # the rest of the row empty.
        self._corners.setColumnCount(len(headers))
        self._corners.setHorizontalHeaderLabels(headers)
        worst = (
            max(range(len(rows)), key=lambda i: rows[i]["delta_s"])
            if comparison
            else None
        )
        self._corners.setRowCount(len(rows))
        for i, row in enumerate(rows):
            flags = row["technique_flags"]
            review = "REVIEW · " + ", ".join(flags) if flags else "No flag"
            if comparison:
                delta = row["delta_s"]
                # In a comparison the warning marks where the time actually went.
                # Without one there is no time to attribute, so it marks technique.
                flagged = i == worst and delta > FLAG_THRESHOLD_S
                cells = (
                    f"{row['corner']} ⚠" if flagged else row["corner"],
                    _vs_m(row["brake_point_m"], row["ref_brake_point_m"]),
                    f"{row['min_speed_kmh']:.0f} / {row['ref_min_speed_kmh']:.0f}",
                    _vs_m(row["throttle_point_m"], row["ref_throttle_point_m"]),
                    f"{row['exit_speed_kmh']:.0f} / {row['ref_exit_speed_kmh']:.0f}",
                    f"{delta:+.3f}",
                    review,
                )
            else:
                flagged = bool(flags)
                cells = (
                    f"{row['corner']} ⚠" if flagged else row["corner"],
                    _single_m(row["brake_point_m"]),
                    _single_speed(row["min_speed_kmh"]),
                    _single_m(row["throttle_point_m"]),
                    _single_speed(row["exit_speed_kmh"]),
                    review,
                )
            review_col = len(cells) - 1
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 0 and flagged:
                    item.setForeground(QColor(theme.RED))
                elif col == review_col:
                    item.setForeground(QColor(theme.YELLOW if flags else theme.TEXT_DIM))
                elif comparison and col == review_col - 1:
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
