"""The AI Race Engineer panel: findings rendered from the fixed coaching
contract, each with "◈ show" evidence-zoom. Providers run on QThreadPool so
the UI never blocks — the same path A3's streaming watsonx client will use.

Every run — success or failure, mock included — writes an audit record next
to the session (see f1coach_core.audit); the Audit… button opens the latest
one so a claim can always be traced to the exact prompt and raw response."""

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSettings, Qt, QThreadPool, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QComboBox,
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
from f1coach_core import (
    CoachingReport,
    CoachProvider,
    Finding,
    Lap,
    available_providers,
    build_coach_prompt,
    build_evidence_summary,
    get_provider,
    write_coaching_audit,
)

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
PROVIDER_LABELS = {
    "granite": "Granite 4.1 (local)",
    "mock": "Mock",
    "ollama": "Ollama",
    "watsonx": "Watsonx",
}


class _CoachSignals(QObject):
    finished = Signal(object)  # CoachingReport
    failed = Signal(str)
    progress = Signal(str)  # accumulated raw model text while streaming
    audited = Signal(str)  # path of the run's audit record ("" if it couldn't be written)


class _CoachTask(QRunnable):
    """Evidence summary + provider call, off the UI thread."""

    def __init__(self, provider: CoachProvider, lap: Lap, reference: Lap) -> None:
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
                    reference_name=self._reference.source.stem,
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

    def __init__(self, finding: Finding, parent: QWidget | None = None) -> None:
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
            text = QLabel(
                f"{item.corner} {item.metric.replace('_', ' ')}:"
                f" {item.value:g} {item.unit} vs {item.ref:g} {item.unit}"
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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lap: Lap | None = None
        self._reference: Lap | None = None
        self._task: _CoachTask | None = None
        self.report: CoachingReport | None = None
        self.audit_path: Path | None = None
        self._settings = QSettings("BristolIBMF1", "Apex")

        title = QLabel("AI Race Engineer")
        title.setStyleSheet("font-weight: 600;")
        self._chip = QLabel("")
        self._chip.setStyleSheet(CHIP_STYLE)
        self._provider_combo = QComboBox()
        for name in available_providers():
            self._provider_combo.addItem(PROVIDER_LABELS.get(name, name.title()), name)
        saved = str(self._settings.value("coach/provider", "granite"))
        saved_index = self._provider_combo.findData(saved)
        if saved_index >= 0:
            self._provider_combo.setCurrentIndex(saved_index)
        self._provider_combo.currentIndexChanged.connect(self._provider_changed)
        header = QHBoxLayout()
        header.addWidget(title)
        header.addWidget(self._chip)
        header.addStretch(1)
        header.addWidget(self._provider_combo)

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
        return str(self._provider_combo.currentData())

    def _provider_changed(self, _index: int) -> None:
        name = self.provider_name
        self._settings.setValue("coach/provider", name)
        self._chip.setText(name)

    def set_context(self, lap: Lap | None, reference: Lap | None) -> None:
        """New lap/reference invalidates any findings on screen."""
        self._lap, self._reference = lap, reference
        self.report = None
        self._clear_cards()
        self._chip.setText(self.provider_name)
        ready = lap is not None and reference is not None
        self._coach_button.setEnabled(ready)
        if ready:
            self._placeholder.setText(
                f"Ready — Analyze lap compares {lap.source.stem} with {reference.source.stem}."
            )
        else:
            self._placeholder.setText(
                "Pick a reference lap to get coaching — findings are always relative "
                "to a reference."
            )

    # -- running ---------------------------------------------------------------

    def _run(self) -> None:
        if self._lap is None or self._reference is None:
            return
        try:
            provider = get_provider(self.provider_name)
        except ValueError as exc:
            self._placeholder.setText(str(exc))
            return
        self._coach_button.setEnabled(False)
        self._coach_button.setText("Analyzing…")
        self._clear_cards()
        self._placeholder.setText(f"Asking {self._provider_combo.currentText()}…")
        task = _CoachTask(provider, self._lap, self._reference)
        task.signals.finished.connect(self.show_report)
        task.signals.failed.connect(self._failed)
        task.signals.progress.connect(self._on_progress)
        task.signals.audited.connect(self._on_audited)
        self._task = task  # keep signals alive while the pool owns the runnable
        QThreadPool.globalInstance().start(task)

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
        self._clear_cards()
        if not report.findings:
            self._placeholder.setText("Nothing significant — this lap matches the reference.")
        else:
            self._placeholder.setText("")
            for finding in report.findings:
                card = FindingCard(finding)
                card.showRequested.connect(self.evidenceRequested.emit)
                self._cards.insertWidget(self._cards.count() - 2, card)
        self.reportReady.emit(report)

    def _failed(self, message: str) -> None:
        self._task = None
        self._coach_button.setEnabled(True)
        self._coach_button.setText("Analyze lap")
        self._scroll.verticalScrollBar().setValue(0)  # outcome reads from the top
        text = f"Coaching failed: {message}"
        if self.audit_path is not None:  # audited arrives first, so this is current
            text += "\n\nThe full run record (prompt and raw response) is under Audit…"
        self._placeholder.setText(text)

    def _clear_cards(self) -> None:
        for i in reversed(range(self._cards.count())):
            widget = self._cards.itemAt(i).widget()
            if isinstance(widget, FindingCard):
                self._cards.takeAt(i)
                widget.hide()  # stop painting now — deleteLater waits for the loop
                widget.deleteLater()
