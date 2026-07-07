"""The AI Race Engineer panel: findings rendered from the fixed coaching
contract, each with "◈ show" evidence-zoom. Providers run on QThreadPool so
the UI never blocks — the same path A3's streaming watsonx client will use."""

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
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
    build_evidence_summary,
    get_provider,
)

CARD_STYLE = (
    "QFrame#findingCard { background: #1f1f1f; border: 1px solid #393939;"
    " border-radius: 6px; }"
)
CHIP_STYLE = (
    f"background: #262626; color: {theme.TEXT_DIM}; border-radius: 4px;"
    " padding: 2px 8px; font-size: 11px;"
)


class _CoachSignals(QObject):
    finished = Signal(object)  # CoachingReport
    failed = Signal(str)


class _CoachTask(QRunnable):
    """Evidence summary + provider call, off the UI thread."""

    def __init__(self, provider: CoachProvider, lap: Lap, reference: Lap) -> None:
        super().__init__()
        self.signals = _CoachSignals()
        self._provider, self._lap, self._reference = provider, lap, reference

    def run(self) -> None:
        try:
            summary = build_evidence_summary(self._lap, self._reference)
            report = self._provider.generate(summary)
        except Exception as exc:  # any failure must land as readable text, not a crash
            self.signals.failed.emit(str(exc))
        else:
            self.signals.finished.emit(report)


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
        issue = QLabel(finding.issue)
        issue.setWordWrap(True)
        issue.setStyleSheet("font-weight: 600;")
        top.addWidget(issue, stretch=1)
        chip = self._confidence_chip(finding.confidence)
        top.addWidget(chip, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)

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


class CoachPanel(QWidget):
    evidenceRequested = Signal(float, float)
    viewResetRequested = Signal()
    reportReady = Signal(object)  # CoachingReport

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lap: Lap | None = None
        self._reference: Lap | None = None
        self._provider = get_provider("mock")
        self._task: _CoachTask | None = None
        self.report: CoachingReport | None = None

        title = QLabel("AI Race Engineer")
        title.setStyleSheet("font-weight: 600;")
        self._chip = QLabel(self._provider.name)
        self._chip.setStyleSheet(CHIP_STYLE)
        header = QHBoxLayout()
        header.addWidget(title)
        header.addWidget(self._chip)
        header.addStretch(1)

        self._coach_button = QPushButton("Coach me")
        self._coach_button.clicked.connect(self._run)
        reset_button = QPushButton("Reset view")
        reset_button.clicked.connect(self.viewResetRequested.emit)
        buttons = QHBoxLayout()
        buttons.addWidget(self._coach_button, stretch=1)
        buttons.addWidget(reset_button)

        self._cards_host = QWidget()
        self._cards = QVBoxLayout(self._cards_host)
        self._cards.setContentsMargins(0, 0, 0, 0)
        self._cards.setSpacing(8)
        self._placeholder = QLabel("")
        self._placeholder.setWordWrap(True)
        self._placeholder.setStyleSheet(f"color: {theme.TEXT_DIM};")
        self._cards.addWidget(self._placeholder)
        self._cards.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._cards_host)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 4)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addLayout(buttons)
        layout.addWidget(scroll, stretch=1)

    # -- context -----------------------------------------------------------

    def set_context(self, lap: Lap | None, reference: Lap | None) -> None:
        """New lap/reference invalidates any findings on screen."""
        self._lap, self._reference = lap, reference
        self.report = None
        self._clear_cards()
        self._chip.setText(self._provider.name)
        ready = lap is not None and reference is not None
        self._coach_button.setEnabled(ready)
        if ready:
            self._placeholder.setText(
                f"Ready — Coach me compares {lap.source.stem} with {reference.source.stem}."
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
        self._coach_button.setEnabled(False)
        self._coach_button.setText("Analyzing…")
        self._clear_cards()
        self._placeholder.setText("Reading the telemetry…")
        task = _CoachTask(self._provider, self._lap, self._reference)
        task.signals.finished.connect(self.show_report)
        task.signals.failed.connect(self._failed)
        self._task = task  # keep signals alive while the pool owns the runnable
        QThreadPool.globalInstance().start(task)

    def show_report(self, report: CoachingReport) -> None:
        self.report = report
        self._task = None
        self._coach_button.setEnabled(True)
        self._coach_button.setText("Coach me")
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
        self._coach_button.setText("Coach me")
        self._placeholder.setText(f"Coaching failed: {message}")

    def _clear_cards(self) -> None:
        for i in reversed(range(self._cards.count())):
            widget = self._cards.itemAt(i).widget()
            if isinstance(widget, FindingCard):
                self._cards.takeAt(i)
                widget.deleteLater()
