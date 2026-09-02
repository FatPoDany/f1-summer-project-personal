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

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from apex import theme
from apex.coach_task import NarrationTask
from apex.coaching_queue import MANAGED_TIMEOUT_S
from f1coach_core import (
    CoachingReport,
    CoachProvider,
    Finding,
    GraniteCoach,
    Lap,
    SavedCoachingReport,
    corner_patterns,
    evidence_for,
    get_provider,
    latest_coaching_report,
    reference_name,
    run_audited_coaching,
)
from f1coach_core.features import (
    BRAKING,
    COASTING,
    CORNER_SPEED,
    THROTTLE,
    CornerScatter,
)
from f1coach_core.reference import CompositeReference
from racecoach.granite import host as gh
from racecoach.granite import model as gm
from racecoach.granite import server as gs
from racecoach.granite.server import GraniteServer, ServerError

CARD_STYLE = (
    f"QFrame#findingCard {{ background: {theme.SURFACE_2}; border: 1px solid "
    f"{theme.BORDER}; border-radius: 9px; }}"
)
CHIP_STYLE = (
    f"background: {theme.SURFACE_2}; color: {theme.TEXT_DIM}; border: 1px solid "
    f"{theme.BORDER}; border-radius: 9px; padding: 3px 8px; font-size: 10px;"
)
FOCUS_CHIPS = {
    "braking": ("Braking", theme.RED),
    "cornering": ("Cornering", theme.BLUE),
    "throttle": ("Throttle", theme.GREEN),
}
# Two habits is what a driver can take away and go and try. Every one that
# qualified is still in the measurement; this is how many are put on screen
# above the findings before they stop being the thing being read.
MAX_PATTERNS_SHOWN = 2


class _CoachSignals(QObject):
    finished = Signal(object)  # CoachingReport
    failed = Signal(str)
    progress = Signal(str)  # accumulated raw model text while streaming
    audited = Signal(str)  # path of the run's audit record ("" if it couldn't be written)


class _RestoreSignals(QObject):
    finished = Signal(object)  # SavedCoachingReport | None


class _RestoreTask(QRunnable):
    """Load and revalidate a saved report without blocking the Qt GUI thread."""

    def __init__(
        self, lap: Lap, reference: Lap | CompositeReference | None, provider: str
    ) -> None:
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
        try:
            self.signals.finished.emit(saved)
        except RuntimeError:
            pass  # the panel went away while this was reading from disk


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

    def __init__(
        self,
        provider: CoachProvider,
        lap: Lap,
        reference: Lap | None,
        *,
        restore_before_run: bool = False,
    ) -> None:
        super().__init__()
        self.signals = _CoachSignals()
        self._provider, self._lap, self._reference = provider, lap, reference
        self._restore_before_run = restore_before_run

    def run(self) -> None:
        if self._restore_before_run:
            try:
                saved = latest_coaching_report(
                    self._lap,
                    self._reference,
                    provider=self._provider.name,
                )
            except (OSError, ValueError):
                saved = None
            if saved is not None:
                self.signals.audited.emit(str(saved.path))
                self.signals.finished.emit(saved.report)
                return
        attempt = run_audited_coaching(
            self._lap,
            self._reference,
            provider_name=self._provider.name,
            provider_factory=lambda: self._provider,
            on_progress=self.signals.progress.emit,
        )
        if attempt.audit_error is not None:
            print(
                f"apex: couldn't write the coaching audit record: {attempt.audit_error}",
                file=sys.stderr,
            )
        self.signals.audited.emit(str(attempt.audit_path or ""))
        if attempt.error is not None:
            self.signals.failed.emit(attempt.error)
        else:
            self.signals.finished.emit(attempt.report)


class PatternBanner(QFrame):
    """One thing the driver does at most corners, not at one of them.

    The findings below are ranked by time lost and cut to three, so a driver who
    brakes early everywhere gets three cards that each say it about one corner,
    and the sentence worth most -- that it is how they brake -- is the one
    nobody says. This is that sentence.

    It is deliberately not a finding and does not look like one. A finding is a
    claim about a stretch of road with a zoom button that goes there; this is a
    claim about a habit, and there is nowhere for it to zoom to. Dressing it as
    a fourth card would make a driver look for the corner it is about.

    Nothing here comes from a model. The count, the share and the median are
    arithmetic over the same corner facts the cards are cited from.
    """

    COLOURS = {
        BRAKING: theme.RED,
        CORNER_SPEED: theme.BLUE,
        THROTTLE: theme.GREEN,
        COASTING: theme.YELLOW,
    }

    def __init__(self, pattern: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("patternBanner")
        colour = self.COLOURS.get(pattern.get("category", ""), theme.TEXT_DIM)
        self.setStyleSheet(
            f"QFrame#patternBanner {{ background: {theme.SURFACE_2};"
            f" border: 1px solid {theme.BORDER}; border-left: 3px solid {colour};"
            " border-radius: 9px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)

        scope = QLabel("ACROSS CORNERS")
        scope.setStyleSheet(
            f"color: {colour}; font-size: 10px; font-weight: 600; letter-spacing: 1px;"
        )
        layout.addWidget(scope)

        headline = QLabel(pattern["headline"])
        headline.setWordWrap(True)
        headline.setStyleSheet("font-weight: 600;")
        layout.addWidget(headline)

        # The corners are named because the claim is about which ones, and a
        # driver who recognises three of them will believe the other two.
        detail = QLabel(f"{pattern['detail']} · {', '.join(pattern['corners'])}")
        detail.setWordWrap(True)
        detail.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 11px;")
        layout.addWidget(detail)


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
        layout.setContentsMargins(12, 10, 12, 11)
        layout.setSpacing(6)

        top = QHBoxLayout()
        self._focus_chip = self._make_focus_chip(finding.focus)
        top.addWidget(self._focus_chip, alignment=Qt.AlignmentFlag.AlignTop)
        top.addStretch(1)
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
            comparison = "·" if reference_label == "review guide" else "vs"
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

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        pool: QThreadPool | None = None,
        server: GraniteServer | None = None,
    ) -> None:
        super().__init__(parent)
        # Model calls can occupy a worker for many minutes on a CPU. Keeping
        # them on a dedicated serial pool prevents those calls from consuming
        # every global Qt worker and leaving TORCS capture queued behind them.
        self._pool = pool or QThreadPool(self)
        if pool is None:
            self._pool.setMaxThreadCount(1)
        # Reading a saved report back is a disk read and a revalidation, never a
        # model call, so it gets a worker of its own. Sharing the serial model
        # pool made every context switch queue behind whatever multi-minute run
        # was in flight: returning to a lap that had already been analysed sat
        # on the "Ready" placeholder until that unrelated run finished, which
        # reads as the earlier answer having been lost.
        self._restore_pool = QThreadPool(self)
        self._restore_pool.setMaxThreadCount(1)
        self._lap: Lap | None = None
        self._reference: Lap | CompositeReference | None = None
        self._scatter: CornerScatter | None = None
        # How many habit banners the last report put on screen. Read by the
        # exposure log, which counts what a participant had in front of them:
        # a report with no findings and a banner is still coaching being read,
        # and was being recorded as an empty screen.
        self.patterns_shown = 0
        self._task: _CoachTask | None = None
        self._prepare: _PrepareTask | None = None
        self._restore_after_prepare = False
        self._server = server or GraniteServer()
        self._managed_endpoint: str | None = None
        self._debrief_summary = ""
        self._debrief_points: list = []
        self._narration_task: NarrationTask | None = None
        self._running: dict[tuple[str, str], _CoachTask] = {}
        self._auto = True
        # Switching the comparison a few times in a second should start one run,
        # for wherever the participant settled, not one per click.
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(400)
        self._settle.timeout.connect(self._auto_run)
        self._capability: gh.Capability | None = None
        self._restore_task: _RestoreTask | None = None
        self.report: CoachingReport | None = None
        self.audit_path: Path | None = None
        # What this session has already validated and shown, so returning to a
        # pair repaints it at once instead of deriving the evidence summary from
        # the lap CSVs a second time. An imported lap never changes, so an entry
        # stays true for as long as the window is open.
        self._shown: dict[tuple[str, str], tuple[CoachingReport, Path | None]] = {}

        title = QLabel("AI Coach")
        title.setObjectName("sectionTitle")
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
        self._coach_button.setObjectName("primary")
        self._coach_button.clicked.connect(self._run)
        reset_button = QPushButton("Reset view")
        reset_button.setObjectName("quiet")
        reset_button.clicked.connect(self.viewResetRequested.emit)
        self._audit_button = QPushButton("Audit…")
        self._audit_button.setObjectName("quiet")
        self._audit_button.setEnabled(False)
        self._audit_button.setToolTip(
            "The raw record of the last run: prompt, response, and what Apex did with it"
        )
        self._audit_button.clicked.connect(self._show_audit)
        buttons = QHBoxLayout()
        buttons.addWidget(self._coach_button, stretch=1)
        buttons.addWidget(reset_button)
        buttons.addWidget(self._audit_button)

        # Indeterminate on purpose: a 3B model on a CPU gives no honest estimate,
        # and a bar that claims one is worse than a bar that only says "still
        # going". Its presence is the signal.
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setMaximumHeight(6)
        self._progress.hide()

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
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)
        layout.addLayout(header)
        layout.addLayout(buttons)
        layout.addWidget(self._progress)
        layout.addWidget(self._scroll, stretch=1)

    # -- context -----------------------------------------------------------

    @property
    def provider_name(self) -> str:
        return "granite"

    def set_context(
        self,
        lap: Lap | None,
        reference: Lap | CompositeReference | None,
        *,
        scatter: CornerScatter | None = None,
    ) -> None:
        """Show this exact context, restoring its last validated Granite run.

        ``scatter`` is the driver's own spread through each corner of this
        comparison, used only for the cross-corner banner. It deliberately does
        not reach the evidence packet: that packet is the audit key every stored
        report is matched on, and a habit is arithmetic over corners already in
        it, so putting a bar in there would invalidate every analysis anybody
        has waited for a model to produce and buy nothing a reader could not
        recompute.
        """
        self._task = None  # an older worker may finish, but cannot mutate this context
        self._restore_task = None
        self._lap, self._reference, self._scatter = lap, reference, scatter
        self.patterns_shown = 0
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
                f"Ready — Granite will compare {lap.source.stem} "
                f"with {reference_name(reference)}."
            )
        elif lap is not None:
            self._placeholder.setText(
                f"Ready — Granite will analyze {lap.source.stem} as a single lap using "
                "deterministic technique checks."
            )
        else:
            self._placeholder.setText("Open a lap to run Granite analysis.")
        remembered = self._shown.get(self._context_key())
        if remembered is not None:
            report, audit_path = remembered
            self._on_audited("" if audit_path is None else str(audit_path))
            self.show_report(report)
            if self._context_key() in self._running:
                # Asked for again from the button. Keep the answer already on
                # screen rather than blanking it while the new one is read.
                self._show_working("Reading this lap again…")
            return
        if lap is not None:
            restore = _RestoreTask(lap, reference, self.provider_name)
            restore.signals.finished.connect(
                lambda saved, current=restore: self._restore_finished(current, saved)
            )
            self._restore_task = restore
            self._restore_pool.start(restore)

    def _context_key(self) -> tuple[str, str] | None:
        """What a run is *for*, so two of the same are never started."""
        if self._lap is None:
            return None
        reference = self._reference
        if reference is None:
            against = ""
        elif isinstance(reference, CompositeReference):
            # Named, not pathed: a composite is not a file. The name carries the
            # number of laps it was drawn from, so gaining a lap is a new context.
            against = reference.name
        else:
            against = str(reference.source)
        return (str(self._lap.source), against)

    def _restore_finished(
        self,
        task: _RestoreTask,
        saved: SavedCoachingReport | None,
    ) -> None:
        try:
            self._restore_into_view(task, saved)
        except RuntimeError:
            # The panel was closed while this was reading from disk. Its Qt
            # children are already gone; there is nobody left to show anything to.
            return

    def _restore_into_view(
        self,
        task: _RestoreTask,
        saved: SavedCoachingReport | None,
    ) -> None:
        if task is not self._restore_task:
            return
        self._restore_task = None
        if saved is not None:
            self._on_audited(str(saved.path))
            self.show_report(saved.report)
            return
        # Nothing on disk for this pair. Either one is being worked on already --
        # switching the comparison used to abandon a run and make somebody ask
        # for it again -- or nobody has asked for it yet, and waiting for a click
        # only means waiting longer.
        key = self._context_key()
        if key in self._running:
            self._show_working("Still reading this lap…")
        elif self._auto:
            self._settle.start()

    # -- running ---------------------------------------------------------------

    def _auto_run(self) -> None:
        """Start ready coaching automatically, but never an implicit download."""
        if self._lap is None or self._prepare is not None:
            return
        if _endpoint_is_configured(self._managed_endpoint):
            self._run(force=False)
            return
        capability = self._capability or gh.capability()
        self._capability = capability
        if not capability.can_run:
            self._refresh_button()
            return
        if capability.needs_download:
            self._placeholder.setText(capability.reason)
            self._refresh_button()
            return
        self._run(force=False)

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

    def _run(self, _checked: bool = False, *, force: bool = True) -> None:
        if self._lap is None:
            return
        if self._prepare is not None:  # a download is running; this click cancels it
            self._prepare.cancel()
            return

        if _endpoint_is_configured(self._managed_endpoint):
            # Somebody has pointed this at a server of their own -- a researcher's
            # workstation, or a test. Downloading 2.1 GB and starting a second
            # one on top of that would be presumptuous.
            self._start_analysis(restore_before_run=not force)
            return

        capability = self._capability or gh.capability()
        self._capability = capability
        if not capability.can_run:
            self._placeholder.setText(capability.reason)
            self._coach_button.setEnabled(False)
            return
        if capability.needs_download or not self._server.is_ready:
            self._restore_after_prepare = not force
            self._start_preparation(download=capability.needs_download)
            return
        self._start_analysis(restore_before_run=not force)

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
        self._pool.start(task)

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
        self._pool.start(task)

    def _prepared(self, task: _PrepareTask) -> None:
        if task is not self._prepare:
            return
        self._prepare = None
        self._capability = None  # the model is present now
        # The provider reads this, so a server we started is the one it talks to.
        self._managed_endpoint = self._server.base_url
        os.environ["GRANITE_BASE_URL"] = self._server.base_url
        # A 3B model on a laptop CPU takes minutes for its first answer, not the
        # 60 s a client talking to a GPU server would allow. Only set when the
        # server is ours: somebody else's endpoint keeps whatever they chose.
        os.environ.setdefault("GRANITE_TIMEOUT_S", str(MANAGED_TIMEOUT_S))
        os.environ.setdefault("GRANITE_MODEL", gm.MODEL_REPO.replace("-GGUF", ""))
        restore_before_run = self._restore_after_prepare
        self._restore_after_prepare = False
        self._start_analysis(restore_before_run=restore_before_run)

    def _preparation_failed(
        self, task: _PrepareTask, message: str, resumable: bool
    ) -> None:
        if task is not self._prepare:
            return
        self._prepare = None
        self._restore_after_prepare = False
        self._coach_button.setEnabled(True)
        self._coach_button.setText("Resume download" if resumable else "Try again")
        self._placeholder.setText(message)

    def _start_analysis(self, *, restore_before_run: bool = False) -> None:
        key = self._context_key()
        if key is not None and key in self._running:
            # Already being read. Starting a second run for the same pair would
            # compete for the same CPU and, because both would register under
            # this key, leave one of them unable to find itself when it finished.
            self._show_working("Still reading this lap…")
            return
        self._restore_task = None  # a late disk read must not replace this new run
        try:
            provider = (
                get_provider(self.provider_name)
                if _endpoint_is_configured(self._managed_endpoint)
                else GraniteCoach(
                    base_url=self._server.base_url,
                    timeout_s=MANAGED_TIMEOUT_S,
                )
            )
        except ValueError as exc:
            self._placeholder.setText(str(exc))
            return
        self._coach_button.setEnabled(False)
        self._coach_button.setText("Analyzing…")
        self._chip.clear()
        self._chip.hide()
        self._clear_cards()
        self._placeholder.setText("Asking Granite 4.1…")
        task = _CoachTask(
            provider,
            self._lap,
            self._reference,
            restore_before_run=restore_before_run,
        )
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
        key = self._context_key()
        if key is not None:
            self._running[key] = task
        self._show_working("Reading this lap…")
        self._pool.start(task)

    def _task_finished(self, task: _CoachTask, report: CoachingReport) -> None:
        # Judged by what the run was *for*, not by which task object happens to
        # be current. Somebody who opened another lap while this one was being
        # read, then came back, is looking at exactly this result -- and by then
        # `self._task` is a different object, so identity would have left them
        # watching a progress bar for a run that had already finished.
        # A tracked run is judged by what it was for; an untracked one by
        # whether it is still the current task. Both must hold: dropping the
        # context check would leave somebody watching a finished run's progress
        # bar, and dropping the identity check would let a stale result overwrite
        # the lap they moved on to.
        key = self._forget(task)
        current = key == self._context_key() if key is not None else task is self._task
        if not current:
            return  # still written to disk, so returning later restores it
        try:
            self.show_report(report)
        except RuntimeError:
            pass  # Qt children were deleted while this worker was finishing

    def _task_failed(self, task: _CoachTask, message: str) -> None:
        self._forget(task)
        if task is self._task:
            try:
                self._failed(message)
            except RuntimeError:
                pass  # the panel was closed while the request was in flight

    def _show_working(self, text: str) -> None:
        self._progress.show()
        self._coach_button.setEnabled(False)
        self._coach_button.setText("Reading…")
        self._placeholder.setText(text)

    def _done_working(self) -> None:
        self._progress.hide()
        self._coach_button.setEnabled(self._lap is not None)
        self._refresh_button()

    def _forget(self, task: _CoachTask) -> tuple[str, str] | None:
        """Stop tracking a finished run, and say what it was for."""
        for key, running in list(self._running.items()):
            if running is task:
                del self._running[key]
                return key
        return None

    def _is_current(self, task: _CoachTask) -> bool:
        """Whether this run is for the lap and comparison now on screen."""
        for key, running in self._running.items():
            if running is task:
                return key == self._context_key()
        return task is self._task

    def _task_progress(self, task: _CoachTask, text: str) -> None:
        if self._is_current(task):
            try:
                self._on_progress(text)
            except RuntimeError:
                pass  # the panel was closed while the stream was in flight

    def _task_audited(self, task: _CoachTask, path: str) -> None:
        if self._is_current(task):
            try:
                self._on_audited(path)
            except RuntimeError:
                pass  # the panel was closed while the audit was being written

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
        self._done_working()
        self.report = report
        key = self._context_key()
        if key is not None:
            self._shown[key] = (report, self.audit_path)
        self._task = None
        self._coach_button.setEnabled(True)
        self._coach_button.setText("Analyze lap")
        self._scroll.verticalScrollBar().setValue(0)  # outcome reads from the top
        self._chip.setText(f"{report.model} · {report.prompt_version}")
        self._chip.show()
        self._clear_cards()
        patterns = self._patterns()
        self.patterns_shown = len(patterns[:MAX_PATTERNS_SHOWN])
        for pattern in patterns[:MAX_PATTERNS_SHOWN]:
            self._cards.insertWidget(self._cards.count() - 2, PatternBanner(pattern))
        if not report.findings:
            self._placeholder.setText(self._nothing_found(bool(patterns)))
        else:
            self._placeholder.setText("")
            for finding in report.findings:
                card = FindingCard(finding, reference_label=self._reference_label())
                card.showRequested.connect(self.evidenceRequested.emit)
                self._cards.insertWidget(self._cards.count() - 2, card)
        self.reportReady.emit(report)

    def _reference_label(self) -> str:
        """What a citation's second number is, in two words, on every card."""
        if self._reference is None:
            return "review guide"
        if isinstance(self._reference, CompositeReference):
            return "best"
        return "reference"

    def _nothing_found(self, has_pattern: bool) -> str:
        if has_pattern:
            # A habit spread thinly over every corner costs time without any one
            # corner crossing the bar. Saying "nothing significant" above a
            # banner that just named five corners reads as a contradiction.
            return "No single corner stood out — the pattern above is what there is to say."
        if self._reference is None:
            return "No deterministic technique check crossed its review threshold."
        if isinstance(self._reference, CompositeReference):
            return "Nothing significant — no corner of this lap was beaten by another."
        return "Nothing significant — this lap matches the reference."

    def _patterns(self) -> list[dict]:
        """Habits across corners, measured here rather than asked of the model.

        Recomputed from the lap on screen instead of stored with the report:
        it is arithmetic over corner facts, so it cannot disagree with the
        cards, and a report restored from an audit written before any of this
        existed still gets one.
        """
        if self._lap is None:
            return []
        try:
            summary = evidence_for(self._lap, self._reference)
        except (ValueError, KeyError, OSError):  # laps too short to share a grid
            return []
        return corner_patterns(summary.get("corners", []), self._scatter)

    def _failed(self, message: str) -> None:
        self._task = None
        self._done_working()
        self._chip.clear()
        self._chip.hide()
        self._scroll.verticalScrollBar().setValue(0)  # outcome reads from the top
        # Never tell a participant to start a server or set an environment
        # variable: they installed a desktop app, and doing that is our job.
        text = f"Coaching failed: {message}"
        # A slow model is not a stopped one, and restarting the server would
        # only make the next answer slower still.
        if "did not finish within" not in text and (
            "GRANITE_BASE_URL" in text or "not reachable" in text
        ):
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
            if isinstance(widget, (FindingCard, PatternBanner)):
                self._cards.takeAt(i)
                widget.hide()  # stop painting now — deleteLater waits for the loop
                widget.deleteLater()
