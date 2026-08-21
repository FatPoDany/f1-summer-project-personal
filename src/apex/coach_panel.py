"""The Granite-only AI Race Engineer panel.

Findings use the fixed coaching contract and each "◈ show" action zooms to its
evidence. Granite runs on QThreadPool so model latency never blocks the UI.

Every run, whether successful or failed, writes an audit record next to the
session (see f1coach_core.audit); the Audit… button opens the latest one so a
claim can always be traced to the exact prompt and raw response."""

import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from apex import theme
from apex.coach_task import NarrationTask
from f1coach_core import (
    CoachingReport,
    CoachProvider,
    Finding,
    Lap,
    SavedCoachingReport,
    build_coach_prompt,
    build_evidence_summary,
    get_provider,
    latest_coaching_report,
    write_coaching_audit,
)
from racecoach.granite import host as gh
from racecoach.granite import model as gm
from racecoach.granite import server as gs
from racecoach.granite.server import GraniteServer, ServerError

CARD_STYLE = (
    "QFrame#findingCard { background: #1f1f1f; border: 1px solid #393939; border-radius: 6px; }"
)
CHIP_STYLE = (
    f"background: #262626; color: {theme.TEXT_DIM}; border-radius: 4px;"
    " padding: 2px 8px; font-size: 11px;"
)
FOCUS_CHIPS = {
    "braking": ("Braking", theme.RED),
    "cornering": ("Cornering", theme.BLUE),
    "throttle": ("Throttle", theme.GREEN),
}


class _CoachSignals(QObject):
    finished = Signal(object)  # CoachingReport
    failed = Signal(str)
    progress = Signal(str)  # accumulated raw model text while streaming
    audited = Signal(str)  # path of the run's audit record ("" if it couldn't be written)


class _RestoreSignals(QObject):
    finished = Signal(object)  # SavedCoachingReport | None


class _RestoreTask(QRunnable):
    """Load and revalidate a saved report without blocking the Qt GUI thread."""

    def __init__(self, lap: Lap, reference: Lap | None, provider: str) -> None:
        super().__init__()
        self.signals = _RestoreSignals()
        self._lap, self._reference, self._provider = lap, reference, provider

    def run(self) -> None:
        try:
            saved = latest_coaching_report(
                self._lap,
                self._reference,
                provider=self._provider,
            )
        except (OSError, ValueError):
            saved = None
        self.signals.finished.emit(saved)


def _endpoint_is_configured(managed: str | None = None) -> bool:
    """Whether somebody else's model server is what we should be talking to.

    Whether a researcher pointed Apex at their own server is a fact about how it
    was launched. The endpoint Apex starts for itself is not, and conflating the
    two was a real fault: after the first successful preparation the panel saw
    the variable it had just written, concluded somebody had configured a
    server, and stopped managing one. A later click then neither downloaded nor
    started anything -- it just failed to connect to whatever had died.
    """
    configured = os.environ.get("GRANITE_BASE_URL") or ""
    return bool(configured) and configured != (managed or "")


class _PrepareSignals(QObject):
    progress = Signal(str)
    ready = Signal(str)
    failed = Signal(str, bool)  # message, resumable


class _PrepareTask(QRunnable):
    """Get the weights and the server up so nobody has to open a terminal.

    This is the whole point of the coach being usable by a participant, and it
    used to be absent: the panel simply told people to start a server and set an
    environment variable, which is not something a classmate is going to do.
    """

    def __init__(self, server: GraniteServer, *, download: bool) -> None:
        super().__init__()
        self.signals = _PrepareSignals()
        self._server = server
        self._download = download
        self._stop = threading.Event()

    def cancel(self) -> None:
        self._stop.set()

    def run(self) -> None:
        if self._download:
            try:
                gm.ensure_model(
                    on_progress=lambda p: self.signals.progress.emit(
                        f"Downloading the coach: {p.received / 1e9:.1f} of "
                        f"{p.total / 1e9:.1f} GB"
                    ),
                    should_cancel=self._stop.is_set,
                )
            except gm.ModelError as exc:
                self.signals.failed.emit(
                    str(exc), isinstance(exc, gm.IncompleteDownload)
                )
                return
        self.signals.progress.emit("Starting the coach. The first time takes a minute…")
        try:
            base_url = self._server.start()
        except ServerError as exc:
            self.signals.failed.emit(str(exc), False)
            return
        self.signals.ready.emit(base_url)


class _CoachTask(QRunnable):
    """Evidence summary + provider call, off the UI thread."""

    def __init__(self, provider: CoachProvider, lap: Lap, reference: Lap | None) -> None:
        super().__init__()
        self.signals = _CoachSignals()
        self._provider, self._lap, self._reference = provider, lap, reference

    def run(self) -> None:
        raw = {"text": ""}

        def progress(text: str) -> None:
            raw["text"] = text
            self.signals.progress.emit(text)

        summary: dict | None = None
        prompt: str | None = None
        report: CoachingReport | None = None
        error: str | None = None
        try:
            summary = build_evidence_summary(self._lap, self._reference)
            prompt = build_coach_prompt(summary)
            report = self._provider.generate(summary, on_progress=progress)
        except Exception as exc:  # any failure must land as readable text, not a crash
            error = str(exc)
        self.signals.audited.emit(self._write_audit(summary, prompt, raw["text"], report, error))
        if error is not None:
            self.signals.failed.emit(error)
        else:
            self.signals.finished.emit(report)

    def _write_audit(self, summary, prompt, raw_text, report, error) -> str:
        try:
            return str(
                write_coaching_audit(
                    lap_source=self._lap.source,
                    provider=self._provider.name,
                    lap_name=self._lap.source.stem,
                    reference_name=(
                        self._reference.source.stem if self._reference is not None else None
                    ),
                    evidence_summary=summary,
                    prompt=prompt,
                    raw_response=raw_text,
                    report=report,
                    error=error,
                )
            )
        except OSError as exc:  # auditing must not break the run it audits
            print(f"apex: couldn't write the coaching audit record: {exc}", file=sys.stderr)
            return ""


class FindingCard(QFrame):
    showRequested = Signal(float, float)  # evidence span to zoom/highlight

    def __init__(
        self,
        finding: Finding,
        parent: QWidget | None = None,
        *,
        reference_label: str = "reference",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("findingCard")
        self.setStyleSheet(CARD_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        top = QHBoxLayout()
        self._focus_chip = self._make_focus_chip(finding.focus)
        top.addWidget(self._focus_chip, alignment=Qt.AlignmentFlag.AlignTop)
        top.addStretch(1)
        confidence = self._confidence_chip(finding.confidence)
        top.addWidget(confidence, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)

        issue = QLabel(finding.issue)
        issue.setWordWrap(True)
        issue.setStyleSheet("font-weight: 600;")
        layout.addWidget(issue)

        cause = QLabel(finding.cause)
        cause.setWordWrap(True)
        cause.setStyleSheet(f"color: {theme.TEXT_DIM};")
        layout.addWidget(cause)

        action = QLabel(f"→ {finding.action}")
        action.setWordWrap(True)
        layout.addWidget(action)

        for item in finding.evidence:
            row = QHBoxLayout()
            show = QPushButton("◈ show")
            show.setFlat(True)
            show.setCursor(Qt.CursorShape.PointingHandCursor)
            show.setStyleSheet(f"color: {theme.BLUE}; border: none; padding: 0 4px;")
            span = item.span
            show.clicked.connect(lambda _=False, s=span: self.showRequested.emit(s[0], s[1]))
            comparison = "vs" if reference_label == "reference" else "·"
            text = QLabel(
                f"{item.corner} {item.metric.replace('_', ' ')}:"
                f" {item.value:g} {item.unit} {comparison} {reference_label}"
                f" {item.ref:g} {item.unit}"
            )
            text.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 11px;")
            row.addWidget(show)
            row.addWidget(text, stretch=1)
            layout.addLayout(row)

    @staticmethod
    def _make_focus_chip(focus: str) -> QLabel:
        label, colour = FOCUS_CHIPS[focus]
        chip = QLabel(label)
        chip.setStyleSheet(
            CHIP_STYLE + f" color: {colour}; border: 1px solid {colour}; font-weight: 600;"
        )
        return chip

    @staticmethod
    def _confidence_chip(confidence: float) -> QLabel:
        if confidence >= 0.8:
            level, colour = "high", theme.GREEN
        elif confidence >= 0.6:
            level, colour = "medium", theme.YELLOW
        else:
            level, colour = "low", theme.TEXT_DIM
        chip = QLabel(f"{level} · {confidence:.2f}")
        chip.setStyleSheet(CHIP_STYLE + f" color: {colour};")
        return chip


class AuditDialog(QDialog):
    """The raw record of one coaching run: prompt, response, verdict — verbatim."""

    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Coaching audit — {path.name}")
        self.resize(760, 540)
        location = QLabel(str(path))
        location.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 11px;")
        location.setWordWrap(True)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        try:
            text.setPlainText(path.read_text(encoding="utf-8"))
        except OSError as exc:
            text.setPlainText(f"Can't read the audit record: {exc}")
        layout = QVBoxLayout(self)
        layout.addWidget(location)
        layout.addWidget(text, stretch=1)


class CoachPanel(QWidget):
    evidenceRequested = Signal(float, float)
    viewResetRequested = Signal()
    reportReady = Signal(object)  # CoachingReport
    debriefNarrated = Signal(object)  # NarratedDebrief, for the replay to show

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lap: Lap | None = None
        self._reference: Lap | None = None
        self._task: _CoachTask | None = None
        self._prepare: _PrepareTask | None = None
        self._server = GraniteServer()
        self._managed_endpoint: str | None = None
        self._debrief_summary = ""
        self._debrief_points: list = []
        self._narration_task: NarrationTask | None = None
        self._capability: gh.Capability | None = None
        self._restore_task: _RestoreTask | None = None
        self.report: CoachingReport | None = None
        self.audit_path: Path | None = None

        title = QLabel("AI Race Engineer")
        title.setStyleSheet("font-weight: 600;")
        self._chip = QLabel("")
        self._chip.setStyleSheet(CHIP_STYLE)
        self._chip.hide()
        self._provider_label = QLabel("Granite 4.1 · local")
        self._provider_label.setStyleSheet(CHIP_STYLE)
        self._provider_label.setToolTip(
            "Validated local IBM Granite analysis; other providers are not exposed here"
        )
        header = QHBoxLayout()
        header.addWidget(title)
        header.addWidget(self._provider_label)
        header.addStretch(1)
        header.addWidget(self._chip)

        self._coach_button = QPushButton("Analyze lap")
        self._coach_button.clicked.connect(self._run)
        reset_button = QPushButton("Reset view")
        reset_button.clicked.connect(self.viewResetRequested.emit)
        self._audit_button = QPushButton("Audit…")
        self._audit_button.setEnabled(False)
        self._audit_button.setToolTip(
            "The raw record of the last run: prompt, response, and what Apex did with it"
        )
        self._audit_button.clicked.connect(self._show_audit)
        buttons = QHBoxLayout()
        buttons.addWidget(self._coach_button, stretch=1)
        buttons.addWidget(reset_button)
        buttons.addWidget(self._audit_button)

        self._cards_host = QWidget()
        self._cards = QVBoxLayout(self._cards_host)
        self._cards.setContentsMargins(0, 0, 0, 0)
        self._cards.setSpacing(8)
        self._placeholder = QLabel("")
        self._placeholder.setWordWrap(True)
        self._placeholder.setStyleSheet(f"color: {theme.TEXT_DIM};")
        self._cards.addWidget(self._placeholder)
        self._cards.addStretch(1)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidget(self._cards_host)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 4)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addLayout(buttons)
        layout.addWidget(self._scroll, stretch=1)

    # -- context -----------------------------------------------------------

    @property
    def provider_name(self) -> str:
        return "granite"

    def set_context(self, lap: Lap | None, reference: Lap | None) -> None:
        """Show this exact context, restoring its last validated Granite run."""
        self._task = None  # an older worker may finish, but cannot mutate this context
        self._restore_task = None
        self._lap, self._reference = lap, reference
        self.report = None
        self.audit_path = None
        self._audit_button.setEnabled(False)
        self._clear_cards()
        self._chip.setText("")
        self._chip.hide()
        ready = lap is not None
        self._coach_button.setEnabled(ready)
        self._refresh_button()
        if lap is not None and reference is not None:
            self._placeholder.setText(
                f"Ready — Granite will compare {lap.source.stem} with {reference.source.stem}."
            )
        elif lap is not None:
            self._placeholder.setText(
                f"Ready — Granite will analyze {lap.source.stem} as a single lap using "
                "deterministic technique checks."
            )
        else:
            self._placeholder.setText("Open a lap to run Granite analysis.")
        if lap is not None:
            restore = _RestoreTask(lap, reference, self.provider_name)
            restore.signals.finished.connect(
                lambda saved, current=restore: self._restore_finished(current, saved)
            )
            self._restore_task = restore
            QThreadPool.globalInstance().start(restore)

    def _restore_finished(
        self,
        task: _RestoreTask,
        saved: SavedCoachingReport | None,
    ) -> None:
        if task is not self._restore_task:
            return
        self._restore_task = None
        if saved is None:
            return
        self._on_audited(str(saved.path))
        self.show_report(saved.report)

    # -- running ---------------------------------------------------------------

    def _refresh_button(self) -> None:
        """Say what the next click will do.

        Installing the coach is a one-off setup step, so the wording must not
        depend on which lap happens to be on screen -- a button that changed as
        the participant clicked between laps read as a fault, not a choice.
        """
        if _endpoint_is_configured(self._managed_endpoint):
            self._coach_button.setText("Analyze lap")
            return
        capability = self._capability or gh.capability()
        self._capability = capability
        if not capability.can_run:
            self._coach_button.setEnabled(False)
            self._coach_button.setToolTip(capability.reason)
        elif capability.needs_download:
            self._coach_button.setText("Download coach")
            self._coach_button.setToolTip(capability.reason)
        else:
            self._coach_button.setText("Analyze lap")
            self._coach_button.setToolTip("")

    def _run(self) -> None:
        if self._lap is None:
            return
        if self._prepare is not None:  # a download is running; this click cancels it
            self._prepare.cancel()
            return

        if _endpoint_is_configured(self._managed_endpoint):
            # Somebody has pointed this at a server of their own -- a researcher's
            # workstation, or a test. Downloading 2.1 GB and starting a second
            # one on top of that would be presumptuous.
            self._start_analysis()
            return

        capability = self._capability or gh.capability()
        self._capability = capability
        if not capability.can_run:
            self._placeholder.setText(capability.reason)
            self._coach_button.setEnabled(False)
            return
        if capability.needs_download or not self._server.is_ready:
            self._start_preparation(download=capability.needs_download)
            return
        self._start_analysis()

    def set_debrief(self, summary: str, points: list) -> None:
        """The measured stretches this lap's coaching should talk about."""
        self._debrief_summary = summary
        self._debrief_points = list(points)

    def _narrate_debrief(self) -> None:
        """Put the measured stretches into words, for the replay to show.

        Run after the analysis rather than instead of it: the cards answer a
        researcher's question about the lap, and this answers the driver's
        question about one corner, standing where it happened. The server is
        already up by now, so this adopts it rather than loading 2.1 GB again.
        """
        if not self._debrief_points:
            return
        task = NarrationTask(self._debrief_summary, self._debrief_points)
        self._narration_task = task
        task.signals.finished.connect(
            lambda result, current=task: self._debrief_narrated(current, result)
        )
        task.signals.failed.connect(
            lambda _message, current=task: self._debrief_narrated(current, None)
        )
        QThreadPool.globalInstance().start(task)

    def _debrief_narrated(self, task, result) -> None:
        if task is not self._narration_task:
            return
        self._narration_task = None
        if result is not None:
            self.debriefNarrated.emit(result)

    def coach_state(self) -> str:
        """What the coach would do next, and why. Shown when it fails.

        A failure that does not say which of download, start-up or the request
        went wrong costs a round trip to find out, and on a participant's own
        laptop there may not be a second chance to ask.
        """
        weights = gm.model_path()
        try:
            size = weights.stat().st_size
        except OSError:
            size = 0
        binary = gs.server_binary()
        return (
            f"model server: {binary or 'not installed'} · "
            f"weights: {size / 1e9:.2f} of {gm.MODEL_SIZE / 1e9:.2f} GB at {weights} · "
            f"endpoint: {os.environ.get('GRANITE_BASE_URL') or self._server.base_url}"
        )

    def _start_preparation(self, *, download: bool) -> None:
        task = _PrepareTask(self._server, download=download)
        self._prepare = task
        self._coach_button.setText("Cancel" if download else "Starting…")
        self._coach_button.setEnabled(download)
        self._placeholder.setText(
            f"The coach needs a one-off {gm.MODEL_SIZE / 1e9:.1f} GB download. "
            "It then runs entirely on this computer."
            if download
            else "Starting the coach…"
        )
        task.signals.progress.connect(self._placeholder.setText)
        task.signals.ready.connect(lambda _url, current=task: self._prepared(current))
        task.signals.failed.connect(
            lambda message, resumable, current=task: self._preparation_failed(
                current, message, resumable
            )
        )
        QThreadPool.globalInstance().start(task)

    def _prepared(self, task: _PrepareTask) -> None:
        if task is not self._prepare:
            return
        self._prepare = None
        self._capability = None  # the model is present now
        # The provider reads this, so a server we started is the one it talks to.
        self._managed_endpoint = self._server.base_url
        os.environ["GRANITE_BASE_URL"] = self._server.base_url
        os.environ.setdefault("GRANITE_MODEL", gm.MODEL_REPO.replace("-GGUF", ""))
        self._start_analysis()

    def _preparation_failed(
        self, task: _PrepareTask, message: str, resumable: bool
    ) -> None:
        if task is not self._prepare:
            return
        self._prepare = None
        self._coach_button.setEnabled(True)
        self._coach_button.setText("Resume download" if resumable else "Try again")
        self._placeholder.setText(message)

    def _start_analysis(self) -> None:
        self._restore_task = None  # a late disk read must not replace this new run
        try:
            provider = get_provider(self.provider_name)
        except ValueError as exc:
            self._placeholder.setText(str(exc))
            return
        self._coach_button.setEnabled(False)
        self._coach_button.setText("Analyzing…")
        self._chip.clear()
        self._chip.hide()
        self._clear_cards()
        self._placeholder.setText("Asking Granite 4.1…")
        task = _CoachTask(provider, self._lap, self._reference)
        task.signals.finished.connect(
            lambda report, current=task: self._task_finished(current, report)
        )
        task.signals.failed.connect(
            lambda message, current=task: self._task_failed(current, message)
        )
        task.signals.progress.connect(
            lambda text, current=task: self._task_progress(current, text)
        )
        task.signals.audited.connect(
            lambda path, current=task: self._task_audited(current, path)
        )
        self._task = task  # keep signals alive while the pool owns the runnable
        QThreadPool.globalInstance().start(task)

    def _task_finished(self, task: _CoachTask, report: CoachingReport) -> None:
        if task is self._task:
            self.show_report(report)
            self._narrate_debrief()

    def _task_failed(self, task: _CoachTask, message: str) -> None:
        if task is self._task:
            self._failed(message)

    def _task_progress(self, task: _CoachTask, text: str) -> None:
        if task is self._task:
            self._on_progress(text)

    def _task_audited(self, task: _CoachTask, path: str) -> None:
        if task is self._task:
            self._on_audited(path)

    def _on_audited(self, path: str) -> None:
        self.audit_path = Path(path) if path else None
        self._audit_button.setEnabled(self.audit_path is not None)

    def _show_audit(self) -> None:
        if self.audit_path is not None:
            AuditDialog(self.audit_path, self).exec()

    def _on_progress(self, text: str) -> None:
        """Live tail of the model's raw output while it streams."""
        tail = text[-500:]
        self._placeholder.setText(f"…{tail}" if len(text) > 500 else tail)

    def show_report(self, report: CoachingReport) -> None:
        self.report = report
        self._task = None
        self._coach_button.setEnabled(True)
        self._coach_button.setText("Analyze lap")
        self._scroll.verticalScrollBar().setValue(0)  # outcome reads from the top
        self._chip.setText(f"{report.model} · {report.prompt_version}")
        self._chip.show()
        self._clear_cards()
        if not report.findings:
            if self._reference is None:
                self._placeholder.setText(
                    "No deterministic technique check crossed its review threshold."
                )
            else:
                self._placeholder.setText("Nothing significant — this lap matches the reference.")
        else:
            self._placeholder.setText("")
            for finding in report.findings:
                card = FindingCard(
                    finding,
                    reference_label=(
                        "reference" if self._reference is not None else "review guide"
                    ),
                )
                card.showRequested.connect(self.evidenceRequested.emit)
                self._cards.insertWidget(self._cards.count() - 2, card)
        self.reportReady.emit(report)

    def _failed(self, message: str) -> None:
        self._task = None
        self._coach_button.setEnabled(True)
        self._coach_button.setText("Analyze lap")
        self._chip.clear()
        self._chip.hide()
        self._scroll.verticalScrollBar().setValue(0)  # outcome reads from the top
        # Never tell a participant to start a server or set an environment
        # variable: they installed a desktop app, and doing that is our job.
        text = f"Coaching failed: {message}"
        if "GRANITE_BASE_URL" in text or "not reachable" in text:
            self._server.stop()
            self._managed_endpoint = None
            os.environ.pop("GRANITE_BASE_URL", None)
            text = (
                "The coach stopped responding. Press Analyze lap to start it again. "
                "Your lap analysis below is measured from telemetry and is unaffected."
            )
        text += f"\n\n{self.coach_state()}"
        if self.audit_path is not None:  # audited arrives first, so this is current
            text += "\n\nThe full run record (prompt and raw response) is under Audit…"
        self._placeholder.setText(text)

    def shutdown(self) -> None:
        """Stop the model server we started. Called when the window closes."""
        if self._prepare is not None:
            self._prepare.cancel()
            self._prepare = None
        self._server.stop()

    def _clear_cards(self) -> None:
        for i in reversed(range(self._cards.count())):
            widget = self._cards.itemAt(i).widget()
            if isinstance(widget, FindingCard):
                self._cards.takeAt(i)
                widget.hide()  # stop painting now — deleteLater waits for the loop
                widget.deleteLater()
