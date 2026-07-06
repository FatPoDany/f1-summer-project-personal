"""The window shell: toolbar navigation between Garage and Lap Analysis,
menu, drag-and-drop, status line.

Dropping or opening a canonical lap CSV goes straight to Lap Analysis;
a TORCS run export is split into laps and lands as a session in the Garage.
"""

from pathlib import Path

from PySide6.QtGui import QAction, QActionGroup, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMessageBox, QStackedWidget

from apex.analysis_view import AnalysisView
from apex.garage_view import GarageView
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
        self._stacked = QStackedWidget(self)
        self._stacked.addWidget(self._garage)
        self._stacked.addWidget(self._analysis)
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
            except TelemetrySchemaError as exc:
                QMessageBox.critical(self, "Can't import TORCS run", str(exc))
                return
            self._garage.refresh_sessions(select=path.stem)
            self.show_garage()
            self.statusBar().showMessage(summary)
            return
        try:
            lap = load_telemetry_csv(path)
        except TelemetrySchemaError as exc:
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
        for action in (self._garage_action, self._analysis_action):
            group.addAction(action)
            toolbar.addAction(action)

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open telemetry", "", "Telemetry CSV (*.csv)"
        )
        if path:
            self.open_path(path)

    # -- drag and drop -----------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].toLocalFile().lower().endswith(".csv"):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        self.open_path(event.mimeData().urls()[0].toLocalFile())
