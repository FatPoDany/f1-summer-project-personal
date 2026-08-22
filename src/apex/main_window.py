"""The window shell: Garage, analysis, comparison, and Live Pit Wall views,
plus menu, drag-and-drop, and status line.

Dropping or opening a canonical lap CSV goes straight to Lap Analysis;
a TORCS run export is split into laps and lands as a session in the Garage.
"""

import json
import os
from pathlib import Path

from PySide6.QtCore import QSettings, QThreadPool
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
from apex.capture_view import CaptureGuideView, _saved_summary
from apex.coaching_queue import GarageCoachingQueue
from apex.compare_view import CompareView
from apex.garage_view import GarageView
from apex.live_view import LivePitWallView
from apex.study_view import StudyView
from apex.synthetic_capture_view import SyntheticCaptureView
from f1coach_core import (
    Lap,
    Session,
    StudyIdentity,
    TelemetrySchemaError,
    import_telemetry,
    is_torcs_export,
    load_telemetry_csv,
)
from f1coach_core.lap import NO_IDENTITY
from racecoach.granite.server import GraniteServer


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Apex")
        self.setMinimumSize(960, 540)
        self.resize(1280, 720)
        self.setAcceptDrops(True)
        self._opened_capture_runs: set[Path] = set()

        # One model request at a time, shared by automatic Garage work and the
        # interactive panel. Capture owns a different pool and can always start.
        self._coaching_pool = QThreadPool(self)
        self._coaching_pool.setMaxThreadCount(1)
        self._coaching_server = GraniteServer()
        self._coaching_queue = GarageCoachingQueue(
            self,
            pool=self._coaching_pool,
            server=self._coaching_server,
        )

        self._garage = GarageView(self)
        self._capture = CaptureGuideView(self)
        self._synthetic = SyntheticCaptureView(self) if _research_mode_enabled() else None
        self._analysis = AnalysisView(
            self,
            coach_pool=self._coaching_pool,
            coach_server=self._coaching_server,
        )
        self._compare = CompareView(self)
        # Live coaching is a different intervention from the post-drive coaching
        # the study is testing. A participant who used it is no longer a subject
        # who only received a debrief, so it stays out of their reach.
        research = _research_mode_enabled()
        self._live = LivePitWallView(self) if research else None
        self._study = StudyView(self) if research else None
        self._stacked = QStackedWidget(self)
        self._stacked.addWidget(self._garage)
        self._stacked.addWidget(self._capture)
        if self._synthetic is not None:
            self._stacked.addWidget(self._synthetic)
        self._stacked.addWidget(self._analysis)
        self._stacked.addWidget(self._compare)
        if self._study is not None:
            self._stacked.addWidget(self._study)
        if self._live is not None:
            self._stacked.addWidget(self._live)
        self.setCentralWidget(self._stacked)

        self._garage.lapOpened.connect(self.show_analysis)
        self._garage.coachingRequested.connect(self._coaching_queue.queue_session)
        self._coaching_queue.progress.connect(self._garage.apply_coaching_progress)
        self._garage.sessionDeleted.connect(self._session_deleted)
        self._garage.status.connect(lambda text: self.statusBar().showMessage(text))
        self._capture.resultsRequested.connect(self._open_captured_runs)
        self._capture.sessionFinished.connect(self._capture_completed)
        if self._synthetic is not None:
            self._synthetic.resultsRequested.connect(self._open_captured_runs)
            self._synthetic.batchFinished.connect(self._synthetic_completed)

        self._build_menu_and_toolbar()
        self._garage.refresh_sessions()

    # -- navigation ----------------------------------------------------------

    def _set_research_mode(self, enabled: bool) -> None:
        QSettings("Apex", "Apex").setValue(RESEARCH_SETTING, enabled)
        QMessageBox.information(
            self,
            "Research tools",
            "Research tools are "
            + ("on" if enabled else "off")
            + ". Restart Apex for the change to take effect.",
        )

    def _show_study(self) -> None:
        # Reloaded on entry: laps arrive from capture and from folders a
        # researcher drops in, so a cached view would quietly go stale.
        self._study.reload()
        self._stacked.setCurrentWidget(self._study)

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

    def _capture_completed(self, _capture_dir: str, run_dirs: list[str]) -> None:
        # Register the laps in the Garage straight away. A participant should not
        # have to know that a further click is what makes their drive appear, and
        # a run that is never opened is a run the researchers never receive.
        # Navigation is left alone so the hand-over instructions stay on screen.
        self._open_captured_runs(run_dirs, navigate=False)
        self.statusBar().showMessage(f"Driving data saved — {_saved_summary(run_dirs)}")

    def _synthetic_completed(self, _batch_dir: str, run_dirs: list[str]) -> None:
        self.statusBar().showMessage(
            f"Synthetic reference batch saved — {len(run_dirs)} validated run"
            f"{'s' if len(run_dirs) != 1 else ''} ready for analysis"
        )

    def _open_captured_runs(self, run_dirs: list[str], navigate: bool = True) -> None:
        summaries = []
        selected = None
        for value in run_dirs:
            run_dir = Path(value).resolve()
            selected = run_dir.name
            if run_dir in self._opened_capture_runs:
                summaries.append(f"Opened existing captured laps from {run_dir.name}")
                continue
            telemetry = run_dir / "telemetry.csv"
            try:
                summaries.append(
                    import_telemetry(
                        telemetry,
                        session_name=run_dir.name,
                        identity=_run_identity(run_dir),
                        recording=_run_recording(run_dir),
                    )
                )
            except (TelemetrySchemaError, OSError) as exc:
                QMessageBox.critical(self, "Can't open captured laps", str(exc))
                return
            self._opened_capture_runs.add(run_dir)
        self._garage.refresh_sessions(select=selected)
        if navigate:
            self.show_garage()
            self.statusBar().showMessage(" · ".join(summaries))

    def _session_deleted(self, path: str) -> None:
        deleted = Path(path)
        analysis_session = self._analysis._session
        if analysis_session is not None and analysis_session.path.resolve(strict=False) == deleted:
            self._analysis.clear_context()
            self._analysis_action.setEnabled(False)
            self._export_action.setEnabled(False)
        compare_session = self._compare._session
        if compare_session is not None and compare_session.path.resolve(strict=False) == deleted:
            self._compare.clear_session()
            self._compare_action.setEnabled(False)
        self.show_garage()

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
        view_menu = self.menuBar().addMenu("&View")
        self._research_action = QAction("Research tools", self, checkable=True)
        self._research_action.setChecked(_research_mode_enabled())
        self._research_action.setToolTip(
            "Study Results, Robot Pilot and Live Pit Wall. Off for participants: "
            "live coaching is a different intervention from the one being tested."
        )
        self._research_action.toggled.connect(self._set_research_mode)
        view_menu.addAction(self._research_action)

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
        self._capture_action = QAction("Collect Data", self, checkable=True)
        self._capture_action.triggered.connect(
            lambda: self._stacked.setCurrentWidget(self._capture)
        )
        self._synthetic_action = None
        if self._synthetic is not None:
            self._synthetic_action = QAction("Robot Pilot", self, checkable=True)
            self._synthetic_action.setToolTip(
                "Facilitator-only synthetic reference collection"
            )
            self._synthetic_action.triggered.connect(
                lambda: self._stacked.setCurrentWidget(self._synthetic)
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
        self._study_action = None
        if self._study is not None:
            self._study_action = QAction("Study Results", self, checkable=True)
            self._study_action.setToolTip(
                "Baseline against coached, per participant, and the export for statistics"
            )
            self._study_action.triggered.connect(self._show_study)
            self._study.status.connect(lambda text: self.statusBar().showMessage(text))
        self._live_action = None
        if self._live is not None:
            self._live_action = QAction("Live Pit Wall", self, checkable=True)
            self._live_action.triggered.connect(
                lambda: self._stacked.setCurrentWidget(self._live)
            )
        for action in (
            self._garage_action,
            self._capture_action,
            self._synthetic_action,
            self._analysis_action,
            self._compare_action,
            self._study_action,
            self._live_action,
        ):
            if action is None:
                continue
            group.addAction(action)
            toolbar.addAction(action)
        self._stacked.currentChanged.connect(self._sync_view_actions)

    def _sync_view_actions(self, index: int) -> None:
        """Keep the toolbar checks honest however the view was switched."""
        widget = self._stacked.widget(index)
        for view, action in (
            (self._garage, self._garage_action),
            (self._capture, self._capture_action),
            (self._synthetic, self._synthetic_action),
            (self._analysis, self._analysis_action),
            (self._compare, self._compare_action),
            (self._live, self._live_action),
        ):
            if action is not None and widget is view:
                action.setChecked(True)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Bound shutdown time while asking an active capture to brake and stop."""
        self._capture.shutdown(timeout_s=2.0)
        if self._synthetic is not None:
            self._synthetic.shutdown(timeout_s=2.0)
        if self._live is not None:
            self._live.shutdown(timeout_s=2.0)
        # The model server is a child process holding 2.1 GB; leaving it behind
        # would keep that resident after the window is gone.
        self._coaching_queue.shutdown()
        self._analysis.coach_shutdown()
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


RESEARCH_SETTING = "research_tools"


def _research_mode_enabled() -> bool:
    """Whether the researcher-facing screens exist in this window.

    An environment variable alone was the wrong gate: it hid the research tools
    from participants, which was the point, but also from the researchers, who
    are not going to set a variable before launching a desktop app they double
    clicked. The stored setting is the usable half; the variable still wins for
    scripted runs and tests.
    """
    value = os.environ.get("APEX_RESEARCH_MODE", "")
    if value.strip():
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(QSettings("Apex", "Apex").value(RESEARCH_SETTING, False, type=bool))


def _run_recording(run_dir: Path) -> dict | None:
    """Where this run's screen recording lives, from the run's own meta.json.

    Read from the run rather than passed down from the capture form, for the
    same reason the identity is: a run opened days later, or one captured on
    another machine, still knows what it came with.
    """
    try:
        meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    recording = meta.get("recording") if isinstance(meta, dict) else None
    return recording if isinstance(recording, dict) else None


def _run_identity(run_dir: Path) -> StudyIdentity:
    """Read the study identity a captured run recorded for itself.

    Read from the run's own meta.json rather than the Collect Data form, so
    opening a run later — or one captured by someone else — still shows who
    drove it. A run from outside the study workflow simply carries nothing.
    """
    try:
        meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return NO_IDENTITY
    if not isinstance(meta, dict):
        return NO_IDENTITY
    return StudyIdentity(
        driver=meta.get("driver"), phase=meta.get("phase"), setup=meta.get("setup")
    )
