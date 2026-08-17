"""Facilitator-only UI for unattended synthetic reference batches."""

import os
from pathlib import Path

from PySide6.QtCore import QThreadPool, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from apex import theme
from apex.synthetic_capture_task import BatchFunction, SyntheticCaptureTask
from racecoach.telemetry.synthetic_capture import (
    RobotStudyPreset,
    SyntheticBatchResult,
    SyntheticCaptureConfig,
    SyntheticProgress,
    capture_synthetic_batch,
    default_robot_study_preset,
    synthetic_command,
)
from racecoach.telemetry.torcs_runtime import default_torcs_binary


class SyntheticCaptureView(QWidget):
    """Run pinned robot sessions without exposing them as participant data."""

    batchFinished = Signal(str, list)
    batchStopped = Signal()
    batchFailed = Signal(str)
    resultsRequested = Signal(list)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        capture_fn: BatchFunction = capture_synthetic_batch,
        torcs_binary: str | Path | None = None,
        study_preset: RobotStudyPreset | None = None,
    ) -> None:
        super().__init__(parent)
        self._capture_fn = capture_fn
        self._torcs_binary = Path(torcs_binary or default_torcs_binary())
        self._study_preset = study_preset or default_robot_study_preset(
            self._torcs_binary
        )
        self._task: SyntheticCaptureTask | None = None
        self._result_run_dirs: list[str] = []
        self._build_ui()
        self._render_readiness()

    @property
    def running(self) -> bool:
        return self._task is not None

    def _build_ui(self) -> None:
        self._title = QLabel("Synthetic reference pilot")
        self._title.setStyleSheet("font-size: 20px; font-weight: 600;")
        self._warning = QLabel(
            "Synthetic — not participant data. These runs test the collection "
            "pipeline and provide a fixed robot reference only."
        )
        self._warning.setWordWrap(True)
        self._warning.setAccessibleName("Synthetic data warning")
        self._warning.setStyleSheet(
            f"background: {theme.NEUTRAL}; color: {theme.YELLOW}; padding: 8px;"
        )

        assignment = QGroupBox("Frozen reference assignment")
        assignment_layout = QVBoxLayout(assignment)
        self._preset_summary = QLabel(
            f"berniw 9  ·  {self._study_preset.track_id}  ·  "
            f"{self._study_preset.car_id}  ·  {self._study_preset.laps} laps"
        )
        self._preset_summary.setWordWrap(True)
        self._preset_summary.setAccessibleName("Robot, track, car, and lap assignment")
        assignment_layout.addWidget(self._preset_summary)

        setup = QGroupBox("Batch setup")
        setup_form = QFormLayout(setup)
        count_label = QLabel("Sessions")
        self._count = QSpinBox()
        self._count.setRange(1, 20)
        self._count.setValue(3)
        self._count.setAccessibleName("Number of synthetic sessions")
        count_label.setBuddy(self._count)
        setup_form.addRow(count_label, self._count)
        self._readiness = QLabel()
        self._readiness.setWordWrap(True)
        self._readiness.setAccessibleName("Synthetic capture readiness")
        setup_form.addRow("Simulator", self._readiness)

        controls = QHBoxLayout()
        self._start_button = QPushButton("Run synthetic batch")
        self._start_button.setMinimumHeight(44)
        self._stop_button = QPushButton("Stop batch")
        self._stop_button.setMinimumHeight(44)
        self._stop_button.setEnabled(False)
        controls.addWidget(self._start_button)
        controls.addWidget(self._stop_button)
        controls.addStretch(1)

        progress_group = QGroupBox("Batch progress")
        progress_layout = QVBoxLayout(progress_group)
        self._progress = QProgressBar()
        self._progress.setRange(0, self._count.value())
        self._progress.setAccessibleName("Completed synthetic sessions")
        self._status = QLabel("Ready to run a synthetic batch.")
        self._status.setWordWrap(True)
        self._status.setAccessibleName("Synthetic batch status")
        self._result_table = QTableWidget(0, 3)
        self._result_table.setHorizontalHeaderLabels(("Session", "Status", "Run"))
        self._result_table.horizontalHeader().setStretchLastSection(True)
        self._result_table.verticalHeader().setVisible(False)
        self._result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._result_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._result_table.setAccessibleName("Synthetic session results")
        self._open_results_button = QPushButton("Open completed runs in Garage")
        self._open_results_button.setMinimumHeight(44)
        self._open_results_button.setEnabled(False)
        progress_layout.addWidget(self._progress)
        progress_layout.addWidget(self._status)
        progress_layout.addWidget(self._result_table)
        progress_layout.addWidget(self._open_results_button)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 12, 16, 12)
        body_layout.setSpacing(10)
        body_layout.addWidget(self._title)
        body_layout.addWidget(self._warning)
        body_layout.addWidget(assignment)
        body_layout.addWidget(setup)
        body_layout.addLayout(controls)
        body_layout.addWidget(progress_group, stretch=1)

        scroll = QScrollArea(self)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

        self._count.valueChanged.connect(self._count_changed)
        self._start_button.clicked.connect(self.start_batch)
        self._stop_button.clicked.connect(self.stop_batch)
        self._open_results_button.clicked.connect(self._request_results)

    def start_batch(self) -> None:
        if self._task is not None:
            return
        try:
            config = SyntheticCaptureConfig(
                torcs_binary=self._torcs_binary,
                preset=self._study_preset,
                count=self._count.value(),
            )
            synthetic_command(config)
        except ValueError as exc:
            self._show_status(str(exc), error=True)
            return

        self._result_run_dirs = []
        self._result_table.setRowCount(0)
        self._progress.setRange(0, config.count)
        self._progress.setValue(0)
        self._count.setEnabled(False)
        self._start_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        self._open_results_button.setEnabled(False)
        self._show_status("Launching the first unattended TORCS session…")
        task = SyntheticCaptureTask(config, capture_fn=self._capture_fn)
        task.signals.progress.connect(self._on_progress)
        task.signals.completed.connect(self._on_completed)
        task.signals.failed.connect(self._on_failed)
        self._task = task
        QThreadPool.globalInstance().start(task)

    def stop_batch(self) -> None:
        if self._task is None:
            return
        self._show_status("Stopping the active TORCS session and preserving evidence…")
        self._stop_button.setEnabled(False)
        self._task.request_stop()

    def shutdown(self, timeout_s: float = 2.0) -> bool:
        task = self._task
        if task is None:
            return True
        task.request_stop()
        return task.wait(timeout_s)

    def _on_progress(self, token: object, snapshot: SyntheticProgress) -> None:
        if not self._is_current(token):
            return
        if snapshot.status == "complete":
            self._progress.setValue(snapshot.current)
        self._show_status(snapshot.message, error=snapshot.status == "failed")

    def _on_completed(self, token: object, result: SyntheticBatchResult) -> None:
        if not self._is_current(token):
            return
        self._task = None
        self._count.setEnabled(True)
        self._start_button.setEnabled(self._simulator_ready())
        self._stop_button.setEnabled(False)
        self._result_table.setRowCount(len(result.outcomes))
        self._result_run_dirs = [str(path) for path in result.run_dirs]
        for row, outcome in enumerate(result.outcomes):
            run_ids = ", ".join(path.name for path in outcome.run_dirs)
            for column, text in enumerate((outcome.session_id, outcome.status, run_ids)):
                self._result_table.setItem(row, column, QTableWidgetItem(text))
        self._open_results_button.setEnabled(bool(self._result_run_dirs))
        statuses = {outcome.status for outcome in result.outcomes}
        if "cancelled" in statuses or "not_started" in statuses:
            self._show_status("Batch cancelled. Completed evidence, if any, is preserved.")
            self.batchStopped.emit()
            return
        self._progress.setValue(len(result.outcomes))
        failures = sum(outcome.status == "failed" for outcome in result.outcomes)
        if failures:
            self._show_status(
                f"Batch finished with {failures} failed session(s). Raw evidence is preserved.",
                error=True,
            )
        else:
            self._show_status("Batch complete. All synthetic runs were validated.")
        self.batchFinished.emit(str(result.batch_dir), list(self._result_run_dirs))

    def _on_failed(self, token: object, message: str) -> None:
        if not self._is_current(token):
            return
        self._task = None
        self._count.setEnabled(True)
        self._start_button.setEnabled(self._simulator_ready())
        self._stop_button.setEnabled(False)
        self._show_status(f"Synthetic batch could not start or finish — {message}", error=True)
        self.batchFailed.emit(message)

    def _is_current(self, token: object) -> bool:
        return self._task is not None and token is self._task.token

    def _request_results(self) -> None:
        if self._result_run_dirs:
            self.resultsRequested.emit(list(self._result_run_dirs))

    def _count_changed(self, count: int) -> None:
        if self._task is None:
            self._progress.setRange(0, count)
            self._progress.setValue(0)

    def _simulator_ready(self) -> bool:
        return (
            self._torcs_binary.is_file()
            and os.access(self._torcs_binary, os.X_OK)
            and self._study_preset.race_config.is_file()
        )

    def _render_readiness(self) -> None:
        if self._simulator_ready():
            self._readiness.setText("Ready — simulator and robot preset are available.")
            self._readiness.setStyleSheet(f"color: {theme.GREEN};")
        else:
            self._readiness.setText(
                "Unavailable — install or repair the facilitator TORCS runtime."
            )
            self._readiness.setStyleSheet(f"color: {theme.RED};")
        self._start_button.setEnabled(self._simulator_ready())

    def _show_status(self, text: str, *, error: bool = False) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {theme.RED if error else theme.TEXT_DIM};")
