"""Session Debrief — a whole run of laps, measured, and put into words.

The AI Race Engineer panel answers "what happened in this lap". The study's
intervention is a different question: what a participant reads once they have
finished driving, before they drive again. Until now the only way to produce
that was ``racecoach debrief`` from a terminal — so the whole-session debrief
existed, was tested, and nobody being studied could reach it.

Measurement never waits for the model. The numbers are on screen as soon as the
laps are read and the written coaching lands lap by lap on top of them: a
laptop that cannot run a 3B model still shows a complete debrief, and one that
can does not make the participant watch an empty screen while it thinks.

Nothing here computes telemetry truth. It renders what
``racecoach.granite.report`` returns, which is the report the CLI writes too.
"""

import os
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from apex import theme
from f1coach_core import Lap, Session
from racecoach.granite import host as gh
from racecoach.granite import model as gm
from racecoach.granite.report import LapReport, SessionReport, build_report, render_markdown
from racecoach.granite.server import GraniteServer, ServerError

CARD_STYLE = (
    "QFrame#lapCard { background: #1f1f1f; border: 1px solid #393939; border-radius: 6px; }"
)
HEADING_STYLE = "font-size: 18px; font-weight: 600;"
DIM = f"color: {theme.TEXT_DIM};"

# Written coaching is offered from Lap Analysis, which owns the download and its
# cancel button. Two screens asking for the same 2.1 GB would be two progress
# bars for one transfer, so this one points at that one rather than growing a
# second copy of it.
DOWNLOAD_ELSEWHERE = (
    "Written coaching needs a one-off download. Open a lap in Lap Analysis to "
    "start it; the measured debrief below is complete without it."
)


def lap_title(lap: Lap) -> str:
    """What to call a lap in a heading a participant reads.

    ``Lap.label`` is the race lap number when the capture recorded one and the
    file stem when it did not, and "Lap telemetry-lap01" is not a sentence.
    """
    return f"Lap {lap.label}" if lap.lap_number is not None else lap.label


def identity_line(report: SessionReport) -> str:
    """Who drove this session, in the same words the markdown report uses."""
    if not report.laps:
        return ""
    identity = report.laps[0].lap.identity
    return "  ·  ".join(
        f"{name}: {value}"
        for name, value in (
            ("Driver", identity.driver),
            ("Phase", identity.phase),
            ("Setup", identity.setup),
        )
        if value
    )


class _DebriefSignals(QObject):
    measured = Signal(object)  # SessionReport — every lap measured, nothing spoken
    narrated = Signal(object)  # SessionReport — one more lap spoken for
    finished = Signal(object)  # SessionReport — final
    failed = Signal(str)


class _DebriefTask(QRunnable):
    """Measure a session off the GUI thread, then narrate it if a model answers.

    The endpoint is resolved inside ``run`` rather than handed in: starting the
    bundled server can take three minutes off a cold disk, and doing that on the
    GUI thread is the freeze this class exists to prevent. ``resolve_endpoint``
    of None means measure only, which is a complete debrief and not a failure.
    """

    def __init__(
        self,
        laps,
        *,
        resolve_endpoint=None,
        model: str = "",
        build=build_report,
    ) -> None:
        super().__init__()
        self.signals = _DebriefSignals()
        self.token = object()
        self.narrating = resolve_endpoint is not None
        self._laps = list(laps)
        self._resolve_endpoint = resolve_endpoint
        self._model = model
        self._build = build

    def run(self) -> None:
        try:
            self._run()
        except RuntimeError:
            # The window went away while the model was thinking. Nobody is left
            # to tell, and the measured report was never at risk.
            return

    def _run(self) -> None:
        base_url = None
        unavailable = ""
        if self._resolve_endpoint is not None:
            try:
                base_url = self._resolve_endpoint()
            except ServerError as exc:
                unavailable = str(exc)
        try:
            report = self._build(
                self._laps,
                base_url=base_url,
                model=self._model if base_url else None,
                on_measured=self.signals.measured.emit,
                on_narrated=self.signals.narrated.emit,
            )
        except Exception as exc:  # imported telemetry is a trust boundary
            self.signals.failed.emit(str(exc))
            return
        if unavailable and not report.narration_error:
            # A server that would not start is the same class of thing as one
            # that stopped answering: the report says why it is quiet.
            report = replace(report, narration_error=unavailable)
        self.signals.finished.emit(report)


class SessionDebriefView(QWidget):
    """Every lap of one session against its best, worst stretches first."""

    status = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        pool: QThreadPool | None = None,
        server: GraniteServer | None = None,
    ) -> None:
        super().__init__(parent)
        # The same serial pool the per-lap coach uses: one model request at a
        # time on a participant's CPU, whichever screen asked for it.
        self._pool = pool or QThreadPool(self)
        if pool is None:
            self._pool.setMaxThreadCount(1)
        self._server = server or GraniteServer()
        self._session: Session | None = None
        self._report: SessionReport | None = None
        self._task: _DebriefTask | None = None
        self._build_ui()
        self._refresh_buttons()

    # -- construction --------------------------------------------------------

    def _build_ui(self) -> None:
        self._heading = QLabel("Session debrief")
        self._heading.setStyleSheet(HEADING_STYLE)
        self._subject = QLabel()
        self._subject.setWordWrap(True)
        self._subject.setStyleSheet(DIM)
        self._subject.setAccessibleName("Session and participant")
        self._state = QLabel()
        self._state.setWordWrap(True)
        self._state.setAccessibleName("Debrief status")

        self._coach_button = QPushButton("Write the coaching")
        self._coach_button.setToolTip(
            "Ask the local Granite model to put the measured stretches into words"
        )
        self._coach_button.clicked.connect(lambda: self._start(narrate=True))
        self._save_button = QPushButton("Save debrief…")
        self._save_button.setToolTip("Write this debrief to a file you can keep or send")
        self._save_button.clicked.connect(self._save)

        buttons = QHBoxLayout()
        buttons.addWidget(self._coach_button)
        buttons.addWidget(self._save_button)
        buttons.addStretch(1)

        self._cards_host = QWidget()
        self._cards = QVBoxLayout(self._cards_host)
        self._cards.setContentsMargins(0, 0, 0, 0)
        self._cards.setSpacing(8)
        self._cards.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._cards_host)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 4)
        layout.addWidget(self._heading)
        layout.addWidget(self._subject)
        layout.addLayout(buttons)
        layout.addWidget(self._state)
        layout.addWidget(scroll, stretch=1)

    # -- context -------------------------------------------------------------

    @property
    def session(self) -> Session | None:
        return self._session

    @property
    def report(self) -> SessionReport | None:
        return self._report

    def set_session(self, session: Session | None) -> None:
        """Show this session, measuring at once and narrating if the model is ready.

        Returning to a session already on screen leaves it alone: re-measuring
        would throw away prose that took minutes to produce, to arrive at
        exactly the same numbers.
        """
        if (
            session is not None
            and self._session is not None
            and self._report is not None
            and self._session.path == session.path
            and len(self._session.laps) == len(session.laps)
        ):
            return
        self._session = session
        self._report = None
        self._task = None
        self._clear_cards()
        self._subject.setText(session.name if session is not None else "")
        if session is None or not session.laps:
            self._state.setText("This session has no readable laps to debrief.")
            self._refresh_buttons()
            return
        # Never an implicit download: a participant who has not asked for 2.1 GB
        # should not have it start because they opened a screen.
        self._start(narrate=self._coach_ready())

    def clear_session(self) -> None:
        self.set_session(None)

    # -- running -------------------------------------------------------------

    def _coach_ready(self) -> bool:
        """Whether written coaching can run right now without downloading first."""
        if os.environ.get("GRANITE_BASE_URL"):
            return True
        capability = gh.capability()
        return capability.can_run and not capability.needs_download

    def _coach_blocked_reason(self) -> str:
        """Why the model cannot be asked, in a sentence, or empty when it can."""
        if os.environ.get("GRANITE_BASE_URL"):
            return ""
        capability = gh.capability()
        if not capability.can_run:
            return capability.reason
        if capability.needs_download:
            return DOWNLOAD_ELSEWHERE
        return ""

    def _endpoint(self) -> str:
        """The model endpoint to use, started if it is ours to start.

        A researcher who pointed Apex at their own server keeps it: starting a
        second one on top would be presumptuous and would fight for the port.
        """
        return os.environ.get("GRANITE_BASE_URL") or self._server.start()

    def _model_name(self) -> str:
        return os.environ.get("GRANITE_MODEL") or gm.MODEL_REPO.replace("-GGUF", "")

    def _start(self, *, narrate: bool) -> None:
        session = self._session
        if session is None or not session.laps or self._task is not None:
            return
        task = _DebriefTask(
            session.laps,
            resolve_endpoint=self._endpoint if narrate else None,
            model=self._model_name(),
        )
        self._task = task
        task.signals.measured.connect(
            lambda report, current=task: self._measured(current, report)
        )
        task.signals.narrated.connect(
            lambda report, current=task: self._updated(current, report)
        )
        task.signals.finished.connect(
            lambda report, current=task: self._finished(current, report)
        )
        task.signals.failed.connect(
            lambda message, current=task: self._failed(current, message)
        )
        self._state.setText("Reading your laps…")
        self._refresh_buttons()
        self._pool.start(task)

    def _is_current(self, task: _DebriefTask) -> bool:
        return task is self._task

    def _measured(self, task: _DebriefTask, report: SessionReport) -> None:
        """The numbers, before the model has said anything about them."""
        if not self._is_current(task):
            return
        self._render(report)
        if not task.narrating:
            return
        self._state.setText(_waiting_line(sum(1 for item in report.laps if item.points)))

    def _updated(self, task: _DebriefTask, report: SessionReport) -> None:
        if not self._is_current(task):
            return
        self._render(report)
        self._state.setText(
            _waiting_line(
                sum(1 for item in report.laps if item.points and item.narrated is None)
            )
        )

    def _finished(self, task: _DebriefTask, report: SessionReport) -> None:
        if not self._is_current(task):
            return
        self._task = None
        self._render(report)
        blocked = "" if task.narrating else self._coach_blocked_reason()
        if report.narration_error:
            # Say why the prose is missing rather than letting it look as though
            # there was nothing to say.
            self._state.setText(
                "The measurements below are complete. Written coaching was "
                f"unavailable: {report.narration_error}"
            )
        elif blocked:
            self._state.setText(f"The measurements below are complete. {blocked}")
        else:
            self._state.setText("")
        self._refresh_buttons()
        self.status.emit(f"Debrief ready — {report.findings} stretch(es) to look at")

    def _failed(self, task: _DebriefTask, message: str) -> None:
        if not self._is_current(task):
            return
        self._task = None
        self._state.setText(f"This session could not be debriefed: {message}")
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        """Say what each button would do, and why it would not."""
        running = self._task is not None
        self._save_button.setEnabled(self._report is not None and not running)
        if running:
            self._coach_button.setEnabled(False)
            self._coach_button.setText("Working…")
            self._coach_button.setToolTip("")
            return
        self._coach_button.setText("Write the coaching")
        blocked = self._coach_blocked_reason()
        spoken = self._report is not None and any(
            item.narrated is not None for item in self._report.laps
        )
        self._coach_button.setEnabled(
            self._report is not None and not blocked and not spoken
        )
        self._coach_button.setToolTip(
            blocked
            or ("This debrief already has its written coaching." if spoken else "")
        )

    # -- rendering -----------------------------------------------------------

    def _clear_cards(self) -> None:
        for i in reversed(range(self._cards.count())):
            widget = self._cards.itemAt(i).widget()
            if widget is not None:
                self._cards.takeAt(i)
                widget.hide()  # stop painting now — deleteLater waits for the loop
                widget.deleteLater()

    def _render(self, report: SessionReport) -> None:
        self._report = report
        self._clear_cards()
        name = self._session.name if self._session is not None else ""
        identity = identity_line(report)
        self._subject.setText(f"{name}  ·  {identity}" if identity else name)
        if not report.laps:
            self._cards.addWidget(QLabel("No readable laps in this session."))
        else:
            for lap_report in report.laps:
                self._cards.addWidget(_LapCard(lap_report))
        self._cards.addStretch(1)
        self._refresh_buttons()

    # -- saving --------------------------------------------------------------

    def markdown(self) -> str:
        """This debrief as the markdown ``racecoach debrief`` writes."""
        return "" if self._report is None else render_markdown(self._report)

    def save_debrief(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.write_text(self.markdown(), encoding="utf-8")
        return destination

    def _save(self) -> None:
        if self._report is None:
            return
        name = self._session.name if self._session is not None else "session"
        suggested = str(Path.home() / f"apex-debrief-{name}.md")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save debrief", suggested, "Markdown (*.md);;Text (*.txt)"
        )
        if not path:
            return
        try:
            written = self.save_debrief(path)
        except OSError as exc:
            QMessageBox.critical(self, "Can't save debrief", str(exc))
            return
        self.status.emit(f"Debrief written to {written}")


def _waiting_line(remaining: int) -> str:
    """How many laps the model still owes words for. Empty when it owes none."""
    if remaining <= 0:
        return ""
    laps = "lap" if remaining == 1 else "laps"
    return (
        "The measurements below are complete. Written coaching is being "
        f"prepared for {remaining} more {laps}."
    )


class _LapCard(QFrame):
    """One lap: how it stood against the best, and the stretches that cost it."""

    def __init__(self, report: LapReport, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("lapCard")
        self.setStyleSheet(CARD_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        title = QLabel(f"{lap_title(report.lap)} — {report.lap.lap_time:.2f} s")
        # Purple is the session best everywhere else in Apex; the lap every
        # other lap here is measured against is the same thing.
        colour = f" color: {theme.PURPLE};" if report.is_reference else ""
        title.setStyleSheet(f"font-weight: 600;{colour}")
        layout.addWidget(title)

        summary = QLabel(report.summary)
        summary.setWordWrap(True)
        layout.addWidget(summary)

        for index, point in enumerate(report.points):
            layout.addLayout(_point_rows(report, index, point))


def _point_rows(report: LapReport, index: int, point) -> QVBoxLayout:
    """One measured stretch, with whatever the model was allowed to say about it."""
    rows = QVBoxLayout()
    rows.setContentsMargins(12, 6, 0, 0)
    rows.setSpacing(2)
    headline = QLabel(point.headline)
    headline.setStyleSheet(f"font-weight: 600; color: {theme.BLUE};")
    rows.addWidget(headline)
    if point.difference:
        difference = QLabel(f"{point.difference} ({point.detail})")
        difference.setWordWrap(True)
        rows.addWidget(difference)
    narrated = report.narrated.points[index] if report.narrated is not None else None
    if narrated is not None and narrated.narration:
        observation = QLabel(narrated.narration)
        observation.setWordWrap(True)
        observation.setStyleSheet(DIM)
        rows.addWidget(observation)
    if narrated is not None and narrated.advice:
        advice = QLabel(f"→ {narrated.advice}")
        advice.setWordWrap(True)
        advice.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        rows.addWidget(advice)
    return rows
