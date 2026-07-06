"""The window shell: menu, drag-and-drop, status line, and (for A0) one speed trace.

A1 replaces the central widget with the synced strip stack + sector ribbon.
"""

from pathlib import Path

from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMessageBox

from apex.widgets.speed_trace import SpeedTraceWidget
from f1coach_core import Lap, TelemetrySchemaError, load_sample_lap, load_telemetry_csv


class MainWindow(QMainWindow):
    def __init__(self, lap: Lap | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Apex")
        self.setMinimumSize(900, 500)
        self.resize(1200, 640)
        self.setAcceptDrops(True)

        self._trace = SpeedTraceWidget(self)
        self.setCentralWidget(self._trace)
        self._build_menu()

        if lap is not None:
            self.show_lap(lap)

    # -- loading ---------------------------------------------------------

    def show_lap(self, lap: Lap) -> None:
        self._trace.set_lap(lap)
        self.setWindowTitle(f"Apex — {lap.name}")
        summary = (
            f"{lap.name}  ·  {lap.lap_time:.3f} s  ·  {lap.track_length / 1000:.3f} km"
            f"  ·  top {lap.top_speed_kmh:.0f} km/h"
            f"  ·  {lap.n_samples} samples @ {lap.sample_rate:.0f} Hz"
        )
        if lap.dist_derived:
            summary += "  ·  dist derived from speed"
        self.statusBar().showMessage(summary)

    def load_path(self, path: str | Path) -> None:
        try:
            lap = load_telemetry_csv(path)
        except TelemetrySchemaError as exc:
            QMessageBox.critical(self, "Can't load lap", str(exc))
        else:
            self.show_lap(lap)

    # -- menu ------------------------------------------------------------

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        open_action = QAction("&Open Lap CSV…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._pick_file)
        file_menu.addAction(open_action)

        sample_action = QAction("Open &Sample Lap", self)
        sample_action.triggered.connect(lambda: self.show_lap(load_sample_lap()))
        file_menu.addAction(sample_action)

        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open lap telemetry", "", "Telemetry CSV (*.csv)"
        )
        if path:
            self.load_path(path)

    # -- drag and drop ----------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].toLocalFile().lower().endswith(".csv"):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        self.load_path(event.mimeData().urls()[0].toLocalFile())
