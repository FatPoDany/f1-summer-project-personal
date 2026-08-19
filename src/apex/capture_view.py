"""Guided, no-terminal workflow for collecting human TORCS telemetry."""

import json
from pathlib import Path

from PySide6.QtCore import QThreadPool, Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from apex import theme
from apex.capture_pages import CaptureCompletePage, CaptureDrivePage, CaptureSetupPage
from apex.capture_task import CaptureFunction, HumanCaptureTask
from racecoach.telemetry.human_capture import (
    HumanCaptureConfig,
    HumanCaptureResult,
    TorcsStudyPreset,
    capture_human_runs,
    default_study_preset,
)
from racecoach.telemetry.torcs_runtime import default_torcs_binary


def _completed_laps(run_dirs) -> int | None:
    """How many full laps this session put in the Garage, or None if unknown.

    Counted from each run's own meta.json rather than from the number of
    recordings: one drive is a single file, so counting files would tell a
    participant "1 run" no matter how far they drove. laps_seen holds the labels
    that covered a whole track distance, which is the same judgement the Garage
    lists and the number the participant is actually asking about.
    """
    total = 0
    for run_dir in run_dirs:
        try:
            meta = json.loads((Path(run_dir) / "meta.json").read_text("utf-8"))
            total += len(meta["laps_seen"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
    return total


def _saved_summary(run_dirs) -> str:
    laps = _completed_laps(run_dirs)
    if laps is None:
        # The laps are saved either way; only the count is unavailable.
        return "Your driving is saved and split into individual laps you can review."
    if laps == 0:
        return (
            "The recording is saved, but it holds no complete lap — a lap counts "
            "only once the whole track has been driven."
        )
    return (
        f"{laps} complete {'lap' if laps == 1 else 'laps'} saved — each one is in "
        "the Garage for you to review."
    )


class CaptureGuideView(QWidget):
    """Three-stage research guide: prepare, drive, confirm saved evidence."""

    sessionFinished = Signal(str, list)
    sessionStopped = Signal()
    sessionFailed = Signal(str)
    resultsRequested = Signal(list)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        capture_fn: CaptureFunction = capture_human_runs,
        torcs_binary: str | Path | None = None,
        study_preset: TorcsStudyPreset | None = None,
    ) -> None:
        super().__init__(parent)
        self._capture_fn = capture_fn
        self._torcs_binary = Path(torcs_binary or default_torcs_binary())
        self._study_preset = study_preset or default_study_preset(self._torcs_binary)
        self._task: HumanCaptureTask | None = None
        self._result_run_dirs: list[str] = []
        self._build_ui()
        self._update_start_state()

    @property
    def running(self) -> bool:
        return self._task is not None

    def _build_ui(self) -> None:
        self._title = QLabel("Collect driving data")
        self._title.setStyleSheet("font-size: 20px; font-weight: 600;")
        self._intro = QLabel(
            "No terminal or CSV handling is needed. Apex starts the simulator, "
            "checks the recording, and files the completed run for analysis."
        )
        self._intro.setWordWrap(True)
        self._intro.setStyleSheet(f"color: {theme.TEXT_DIM};")

        steps = QLabel("1  PREPARE     →     2  DRIVE     →     3  SAVED")
        steps.setStyleSheet(
            f"background: {theme.NEUTRAL}; color: {theme.BLUE}; "
            "padding: 8px; font-weight: 600;"
        )
        steps.setAccessibleName("Collection progress: prepare, drive, saved")

        self._pages = QStackedWidget()
        self._setup_page = CaptureSetupPage(self._torcs_binary, self._study_preset)
        self._drive_page = CaptureDrivePage(self._study_preset)
        self._complete_page = CaptureCompletePage()
        self._bind_page_controls()
        for page in (self._setup_page, self._drive_page, self._complete_page):
            self._pages.addWidget(page)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 12, 16, 12)
        body_layout.setSpacing(10)
        body_layout.addWidget(self._title)
        body_layout.addWidget(self._intro)
        body_layout.addWidget(steps)
        body_layout.addWidget(self._pages, stretch=1)

        scroll = QScrollArea(self)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

    def _bind_page_controls(self) -> None:
        self._participant_id = self._setup_page.participant_id
        self._phase = self._setup_page.phase
        self._preset_summary = self._setup_page.preset_summary
        self._simulator_status = self._setup_page.simulator_status
        self._simulator_help = self._setup_page.simulator_help
        self._readiness_checks = self._setup_page.readiness_checks
        self._form_error = self._setup_page.form_error
        self._start_button = self._setup_page.start_button
        self._drive_status = self._drive_page.status
        self._stop_button = self._drive_page.stop_button
        self._result_summary = self._complete_page.result_summary
        self._result_path = self._complete_page.result_path
        self._new_session_button = self._complete_page.new_session_button
        self._open_results_button = self._complete_page.open_results_button
        self._participant_id.textChanged.connect(self._update_start_state)
        for check in self._readiness_checks:
            check.toggled.connect(self._update_start_state)
        self._start_button.clicked.connect(self.start_capture)
        self._stop_button.clicked.connect(self.stop_capture)
        self._new_session_button.clicked.connect(self.reset_guide)
        self._open_results_button.clicked.connect(self._request_results)

    def start_capture(self) -> None:
        if self._task is not None:
            return
        try:
            config = self._current_config()
        except ValueError as exc:
            self._show_form_message(str(exc), error=True)
            return
        if not all(check.isChecked() for check in self._readiness_checks):
            self._show_form_message("Complete all readiness checks before starting.", error=True)
            return
        self._show_form_message("")
        self._pages.setCurrentWidget(self._drive_page)
        self._drive_status.setText("Launching TORCS… recording starts with the Human driver.")
        self._stop_button.setEnabled(True)
        task = HumanCaptureTask(config, capture_fn=self._capture_fn)
        task.signals.completed.connect(self._on_completed)
        task.signals.stopped.connect(self._on_stopped)
        task.signals.failed.connect(self._on_failed)
        self._task = task
        QThreadPool.globalInstance().start(task)

    def stop_capture(self) -> None:
        if self._task is None:
            return
        self._drive_status.setText("Stopping TORCS and preserving any raw evidence…")
        self._stop_button.setEnabled(False)
        self._task.request_stop()

    def shutdown(self, timeout_s: float = 2.0) -> bool:
        task = self._task
        if task is None:
            return True
        task.request_stop()
        return task.wait(timeout_s)

    def reset_guide(self) -> None:
        if self._task is not None:
            return
        self._participant_id.clear()
        self._result_run_dirs = []
        for check in self._readiness_checks:
            check.setChecked(False)
        self._show_form_message("")
        self._pages.setCurrentWidget(self._setup_page)
        self._participant_id.setFocus()

    def _on_completed(self, token: object, result: HumanCaptureResult) -> None:
        if not self._is_current(token):
            return
        self._task = None
        self._result_summary.setText(_saved_summary(result.run_dirs))
        self._result_path.setText(f"Send this folder to the researchers: {result.capture_dir}")
        self._result_run_dirs = [str(path) for path in result.run_dirs]
        self._pages.setCurrentWidget(self._complete_page)
        self._new_session_button.setFocus()
        self.sessionFinished.emit(
            str(result.capture_dir), self._result_run_dirs
        )

    def _request_results(self) -> None:
        if self._result_run_dirs:
            self.resultsRequested.emit(list(self._result_run_dirs))

    def _on_stopped(self, token: object, _capture_dir: str) -> None:
        if not self._is_current(token):
            return
        self._task = None
        self._pages.setCurrentWidget(self._setup_page)
        self._show_form_message("Collection stopped. No run was registered.", error=True)
        self._update_start_state(keep_message=True)
        self.sessionStopped.emit()

    def _on_failed(self, token: object, message: str, capture_dir: str) -> None:
        if not self._is_current(token):
            return
        self._task = None
        self._pages.setCurrentWidget(self._setup_page)
        # `message` already names the capture directory: every failure raised by
        # capture_human_runs ends with "raw files remain in <dir>". Appending it
        # again printed the path twice in the one line the participant sees.
        self._show_form_message(f"Collection could not finish — {message}.", error=True)
        self._update_start_state(keep_message=True)
        self.sessionFailed.emit(message)

    def _is_current(self, token: object) -> bool:
        return self._task is not None and token is self._task.token

    def _current_config(self) -> HumanCaptureConfig:
        return HumanCaptureConfig(
            participant_id=self._participant_id.text().strip(),
            phase=str(self._phase.currentData()),
            torcs_binary=self._torcs_binary,
            preset=self._study_preset,
        )

    def _simulator_ready(self) -> bool:
        return self._setup_page.simulator_ready

    def _update_start_state(self, _value=None, *, keep_message: bool = False) -> None:
        valid_identity = False
        value = self._participant_id.text().strip()
        if value:
            try:
                self._current_config()
            except ValueError:
                if not keep_message:
                    self._show_form_message(
                        "Use a pseudonymous ID such as P001; do not enter a name or email.",
                        error=True,
                    )
            else:
                valid_identity = True
                if not keep_message:
                    self._show_form_message("")
        elif not keep_message:
            self._show_form_message("")
        ready = all(check.isChecked() for check in self._readiness_checks)
        self._start_button.setEnabled(valid_identity and ready and self._simulator_ready())

    def _show_form_message(self, text: str, *, error: bool = False) -> None:
        self._setup_page.show_message(text, error=error)
