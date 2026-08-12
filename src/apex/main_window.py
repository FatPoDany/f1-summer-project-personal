"""The window shell: Garage, analysis, comparison, and Live Pit Wall views,
plus menu, drag-and-drop, and status line.

Dropping or opening a canonical lap CSV goes straight to Lap Analysis;
a TORCS run export is split into laps and lands as a session in the Garage.
"""

from pathlib import Path

from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
)
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMessageBox, QStackedWidget

from apex.analysis_view import AnalysisView
from apex.compare_view import CompareView
from apex.garage_view import GarageView
from apex.live_view import LivePitWallView
from f1coach_core import (
    Lap,
    Session,
    TelemetrySchemaError,
    import_telemetry,
    is_torcs_export,
    load_telemetry_csv,
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Apex")
        self.setMinimumSize(960, 540)
        self.resize(1280, 720)
        self.setAcceptDrops(True)

        self._garage = GarageView(self)
        self._analysis = AnalysisView(self)
        self._compare = CompareView(self)
        self._live = LivePitWallView(self)
        self._stacked = QStackedWidget(self)
        self._stacked.addWidget(self._garage)
        self._stacked.addWidget(self._analysis)
        self._stacked.addWidget(self._compare)
        self._stacked.addWidget(self._live)
        self.setCentralWidget(self._stacked)

        self._garage.lapOpened.connect(self.show_analysis)
        self._garage.status.connect(lambda text: self.statusBar().showMessage(text))

        self._build_menu_and_toolbar()
        self._garage.refresh_sessions()

    # -- navigation ----------------------------------------------------------

    def show_garage(self) -> None:
        self._stacked.setCurrentWidget(self._garage)
        self._garage_action.setChecked(True)

    def show_analysis(self, lap: Lap, session: Session | None = None) -> None:
        self._analysis.set_context(lap, session)
        self._analysis_action.setEnabled(True)
        self._export_action.setEnabled(True)
        if session is not None and len(session.laps) >= 2:
            self._compare.set_session(session, lap_a=lap)
            self._compare_action.setEnabled(True)
        self._stacked.setCurrentWidget(self._analysis)
        self._analysis_action.setChecked(True)
        summary = (
            f"{lap.name}  ·  {lap.lap_time:.3f} s  ·  {lap.track_length / 1000:.3f} km"
            f"  ·  top {lap.top_speed_kmh:.0f} km/h"
            f"  ·  {lap.n_samples} samples @ {lap.sample_rate:.0f} Hz"
        )
        if lap.dist_derived:
            summary += "  ·  dist derived from speed"
        self.statusBar().showMessage(summary)

    # -- opening files ---------------------------------------------------------

    def open_path(self, path: str | Path) -> None:
        """Canonical lap -> Lap Analysis. TORCS run -> split into a Garage session."""
        path = Path(path)
        if is_torcs_export(path):
            try:
                summary = import_telemetry(path, session_name=path.stem)
            except (TelemetrySchemaError, OSError) as exc:
                QMessageBox.critical(self, "Can't import TORCS run", str(exc))
                return
            self._garage.refresh_sessions(select=path.stem)
            self.show_garage()
            self.statusBar().showMessage(summary)
            return
        try:
            lap = load_telemetry_csv(path)
        except (TelemetrySchemaError, OSError) as exc:
            QMessageBox.critical(self, "Can't load lap", str(exc))
        else:
            self.show_analysis(lap, None)

    # -- chrome ---------------------------------------------------------------

    def _build_menu_and_toolbar(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        open_action = QAction("&Open Telemetry CSV…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._pick_file)
        file_menu.addAction(open_action)

        self._export_action = QAction("&Export Analysis Report…", self)
        self._export_action.setShortcut(QKeySequence.StandardKey.Save)
        self._export_action.setEnabled(False)  # until a lap is on the analysis screen
        self._export_action.triggered.connect(self._export_report)
        file_menu.addAction(self._export_action)

        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        toolbar = self.addToolBar("Views")
        toolbar.setMovable(False)
        group = QActionGroup(self)
        self._garage_action = QAction("Garage", self, checkable=True, checked=True)
        self._garage_action.triggered.connect(
            lambda: self._stacked.setCurrentWidget(self._garage)
        )
        self._analysis_action = QAction("Lap Analysis", self, checkable=True)
        self._analysis_action.setEnabled(False)  # until a lap is opened
        self._analysis_action.triggered.connect(
            lambda: self._stacked.setCurrentWidget(self._analysis)
        )
        self._compare_action = QAction("Compare", self, checkable=True)
        self._compare_action.setEnabled(False)  # until a session with 2+ laps is opened
        self._compare_action.triggered.connect(
            lambda: self._stacked.setCurrentWidget(self._compare)
        )
        self._live_action = QAction("Live Pit Wall", self, checkable=True)
        self._live_action.triggered.connect(
            lambda: self._stacked.setCurrentWidget(self._live)
        )
        for action in (
            self._garage_action,
            self._analysis_action,
            self._compare_action,
            self._live_action,
        ):
            group.addAction(action)
            toolbar.addAction(action)
        self._stacked.currentChanged.connect(self._sync_view_actions)

    def _sync_view_actions(self, index: int) -> None:
        """Keep the toolbar checks honest however the view was switched."""
        widget = self._stacked.widget(index)
        for view, action in (
            (self._garage, self._garage_action),
            (self._analysis, self._analysis_action),
            (self._compare, self._compare_action),
            (self._live, self._live_action),
        ):
            if widget is view:
                action.setChecked(True)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Bound shutdown time while asking an active capture to brake and stop."""
        self._live.shutdown(timeout_s=2.0)
        super().closeEvent(event)

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open telemetry", "", "Telemetry CSV (*.csv)"
        )
        if path:
            self.open_path(path)

    def _export_report(self) -> None:
        lap = self._analysis.lap
        if lap is None:
            return
        suggested = str(Path.home() / f"apex-report-{lap.source.stem}.html")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export analysis report", suggested, "HTML report (*.html)"
        )
        if path:
            try:
                written = self._analysis.export_report(path)
            except OSError as exc:
                QMessageBox.critical(self, "Can't export report", str(exc))
                return
            self.statusBar().showMessage(f"Report written to {written}")

    # -- drag and drop -----------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].toLocalFile().lower().endswith(".csv"):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        self.open_path(event.mimeData().urls()[0].toLocalFile())
