"""Serial, evidence-audited coaching work requested by the Garage."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from f1coach_core import (
    CoachProvider,
    GraniteCoach,
    Lap,
    Session,
    get_provider,
    latest_coaching_report,
    run_audited_coaching,
)
from f1coach_core.reference import CompositeReference, composite_reference
from racecoach.granite import host as gh
from racecoach.granite.server import GraniteServer

# The observed packaged CPU run exceeded the previous 420-second limit before
# its first answer. A visible queued/generating state makes the longer bound
# honest, while still ensuring a failed request eventually reaches a terminal
# state and an audit record.
MANAGED_TIMEOUT_S = 900.0


class CoachingStage(StrEnum):
    QUEUED = "queued"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"
    SETUP_NEEDED = "setup_needed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CoachingProgress:
    """One lap's current automatic-coaching state."""

    lap_source: Path
    stage: CoachingStage
    findings: int | None = None
    message: str = ""
    audit_path: Path | None = None


class _JobSignals(QObject):
    progress = Signal(object)  # CoachingProgress
    done = Signal(object)  # Path


class _CoachingJob(QRunnable):
    def __init__(
        self,
        lap: Lap,
        reference: Lap | CompositeReference | None,
        *,
        provider_name: str,
        provider_factory: Callable[[], CoachProvider],
    ) -> None:
        super().__init__()
        self.signals = _JobSignals()
        self._lap = lap
        self._reference = reference
        self._provider_name = provider_name
        self._provider_factory = provider_factory

    def run(self) -> None:
        try:
            self._run()
        finally:
            self.signals.done.emit(self._lap.source)

    def _run(self) -> None:
        try:
            saved = latest_coaching_report(
                self._lap,
                self._reference,
                provider=self._provider_name,
            )
        except (OSError, ValueError):
            saved = None
        if saved is not None:
            self.signals.progress.emit(
                CoachingProgress(
                    self._lap.source,
                    CoachingStage.READY,
                    findings=len(saved.report.findings),
                    audit_path=saved.path,
                )
            )
            return

        self.signals.progress.emit(
            CoachingProgress(self._lap.source, CoachingStage.GENERATING)
        )
        attempt = run_audited_coaching(
            self._lap,
            self._reference,
            provider_name=self._provider_name,
            provider_factory=self._provider_factory,
        )
        if attempt.error is not None:
            message = attempt.error
            if attempt.audit_error:
                message += f" Audit record could not be written: {attempt.audit_error}"
            self.signals.progress.emit(
                CoachingProgress(
                    self._lap.source,
                    CoachingStage.FAILED,
                    message=message,
                    audit_path=attempt.audit_path,
                )
            )
            return

        report = attempt.report
        self.signals.progress.emit(
            CoachingProgress(
                self._lap.source,
                CoachingStage.READY,
                findings=len(report.findings) if report is not None else 0,
                message=(
                    f"Audit record could not be written: {attempt.audit_error}"
                    if attempt.audit_error
                    else ""
                ),
                audit_path=attempt.audit_path,
            )
        )


Availability = Callable[[], tuple[CoachingStage, str] | None]


class GarageCoachingQueue(QObject):
    """Queue each session lap once, using one CPU model request at a time."""

    progress = Signal(object)  # CoachingProgress

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        pool: QThreadPool | None = None,
        server: GraniteServer | None = None,
        provider_name: str = "granite",
        provider_factory: Callable[[], CoachProvider] | None = None,
        availability: Availability | None = None,
    ) -> None:
        super().__init__(parent)
        self._pool = pool or QThreadPool(self)
        if pool is None:
            self._pool.setMaxThreadCount(1)
        self._server = server or GraniteServer()
        self._provider_name = provider_name
        self._provider_factory = provider_factory or self._managed_provider
        self._availability = availability or (
            (lambda: None) if provider_factory is not None else self._default_availability
        )
        self._stages: dict[Path, CoachingStage] = {}
        self._tasks: dict[Path, _CoachingJob] = {}
        self._shutting_down = False

    def queue_session(self, session: Session) -> None:
        """Queue the automatic context for every lap in one loaded session."""
        if self._shutting_down or not session.laps:
            return
        blocked = self._availability()
        best = session.best_lap
        # The quickest lap has no quicker lap to be read against, so it used to
        # be queued as a single-lap technique review: four fixed guides, no
        # corner speeds, nothing about where its time actually went. It is read
        # against the best each of its own corners was driven instead. Every
        # other lap keeps the session best, which is what the Analysis screen
        # opens them against -- a different reference here would be a second
        # context, and a second multi-minute run on the participant's CPU.
        composite = self._composite(session)
        for lap in session.laps:
            source = lap.source.resolve(strict=False)
            # Ready work is complete and active work is already represented in
            # the pool. A terminal failure is deliberately retryable when the
            # participant reloads the session after fixing the model/server.
            if source in self._tasks or self._stages.get(source) is CoachingStage.READY:
                continue
            if blocked is not None:
                stage, message = blocked
                self.progress.emit(CoachingProgress(lap.source, stage, message=message))
                continue
            reference = (composite if lap is best else best)
            task = _CoachingJob(
                lap,
                reference,
                provider_name=self._provider_name,
                provider_factory=self._provider_factory,
            )
            task.signals.progress.connect(self._forward_progress)
            task.signals.done.connect(self._job_done)
            self._stages[source] = CoachingStage.QUEUED
            self._tasks[source] = task
            self.progress.emit(CoachingProgress(lap.source, CoachingStage.QUEUED))
            # Interactive Analysis work uses the same one-thread pool at normal
            # priority, so it may go next without racing this background batch.
            self._pool.start(task, -1)

    @staticmethod
    def _composite(session: Session) -> CompositeReference | None:
        """Per-corner bests for this session, or None if they cannot be built.

        Building one reads every lap, on the thread the Garage is drawn from.
        A session whose laps cannot be put on a common grid must still queue --
        losing the coach entirely because the reference could not be improved
        would be a worse failure than the reference it replaced.
        """
        try:
            return composite_reference(list(session.laps), anchor=session.best_lap)
        except (ValueError, IndexError, KeyError):
            return None

    def _forward_progress(self, update: CoachingProgress) -> None:
        self._stages[update.lap_source.resolve(strict=False)] = update.stage
        self.progress.emit(update)

    def _job_done(self, lap_source: Path) -> None:
        self._tasks.pop(Path(lap_source).resolve(strict=False), None)

    def _default_availability(self) -> tuple[CoachingStage, str] | None:
        if os.environ.get("GRANITE_BASE_URL"):
            return None
        capability = gh.capability()
        if not capability.can_run:
            return CoachingStage.UNAVAILABLE, capability.reason
        if capability.needs_download:
            return CoachingStage.SETUP_NEEDED, capability.reason
        return None

    def shutdown(self) -> None:
        """Discard pending work and prevent a closing app from restarting Granite."""
        self._shutting_down = True
        self._pool.clear()

    def _managed_provider(self) -> CoachProvider:
        if self._shutting_down:
            raise RuntimeError("Apex is closing; automatic coaching was cancelled.")
        if os.environ.get("GRANITE_BASE_URL"):
            return get_provider("granite")
        base_url = self._server.start()
        return GraniteCoach(base_url=base_url, timeout_s=MANAGED_TIMEOUT_S)
