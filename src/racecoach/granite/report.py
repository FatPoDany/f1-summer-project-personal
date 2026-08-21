"""Coached debriefs for a whole session, for whoever ends up generating them.

Participants install on their own laptops and half of those laptops will not run
a 3B model comfortably. So the same debrief has to be reachable two ways: on the
participant's machine right after driving, and on a researcher's machine later,
from the folder the participant handed over. Both call this.

The measured debrief is produced either way. The model only ever adds prose on
top, so a session that could not reach a model still yields a complete report --
one without narration, not one without findings.
"""

from dataclasses import dataclass
from pathlib import Path

from f1coach_core.debrief import DebriefPoint, debrief_summary, lap_debrief
from f1coach_core.lap import Lap
from f1coach_core.loader import TelemetrySchemaError, load_telemetry_csv
from racecoach.granite.narrate import NarratedDebrief, NarrationError, narrate_debrief


@dataclass(frozen=True)
class LapReport:
    """One lap measured against the session's best, narrated where possible."""

    lap: Lap
    reference: Lap
    points: tuple[DebriefPoint, ...]
    summary: str
    narrated: NarratedDebrief | None = None

    @property
    def is_reference(self) -> bool:
        return self.lap is self.reference

    @property
    def spoken_count(self) -> int:
        return 0 if self.narrated is None else self.narrated.spoken_count


@dataclass(frozen=True)
class SessionReport:
    laps: tuple[LapReport, ...]
    reference: Lap | None
    narration_error: str = ""

    @property
    def findings(self) -> int:
        return sum(len(report.points) for report in self.laps)


def session_laps(directory: str | Path) -> list[Lap]:
    """Every canonical lap in a session folder, in lap order.

    Files that are not readable laps are skipped rather than fatal: a session
    that gained a stray CSV should still produce a report for the laps that are
    fine, and the participant is not in a position to fix the odd one out.
    """
    laps: list[Lap] = []
    for path in sorted(Path(directory).glob("*.csv")):
        try:
            laps.append(load_telemetry_csv(path))
        except (TelemetrySchemaError, OSError, ValueError):
            continue
    return sorted(laps, key=lambda lap: (lap.lap_number is None, lap.lap_number or 0))


def fastest(laps: list[Lap]) -> Lap | None:
    return min(laps, key=lambda lap: lap.lap_time) if laps else None


def build_report(
    laps: list[Lap],
    *,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    transport=None,
    limit: int = 3,
) -> SessionReport:
    """Measure every lap against the session best, narrating if a model is offered.

    ``base_url`` of None means measurement only. A model that fails mid-way stops
    narration for the rest of the session but never discards what was measured;
    the reason travels back on the report so a caller can say why it is quiet.
    """
    reference = fastest(laps)
    if reference is None:
        return SessionReport(laps=(), reference=None)

    reports: list[LapReport] = []
    narration_error = ""

    for lap in laps:
        if lap is reference:
            reports.append(
                LapReport(
                    lap=lap,
                    reference=reference,
                    points=(),
                    summary="This was your quickest lap of the session.",
                )
            )
            continue
        try:
            points = lap_debrief(lap, reference, limit=limit)
        except ValueError:  # laps too short to share a distance grid
            points = []
        summary = debrief_summary(lap, reference, points)

        narrated = None
        if base_url and model and points and not narration_error:
            try:
                narrated = narrate_debrief(
                    summary,
                    points,
                    base_url=base_url,
                    model=model,
                    api_key=api_key,
                    transport=transport,
                )
                summary = narrated.summary
            except NarrationError as exc:
                narration_error = str(exc)

        reports.append(
            LapReport(
                lap=lap,
                reference=reference,
                points=tuple(points),
                summary=summary,
                narrated=narrated,
            )
        )

    return SessionReport(
        laps=tuple(reports), reference=reference, narration_error=narration_error
    )


def render_markdown(report: SessionReport) -> str:
    """A report a participant can read and a researcher can file."""
    if not report.laps:
        return "# Driving debrief\n\nNo readable laps in this session.\n"

    identity = report.laps[0].lap.identity
    lines = ["# Driving debrief", ""]
    if identity.driver:
        lines.append(f"Driver: {identity.driver}")
    if identity.phase:
        lines.append(f"Phase: {identity.phase}")
    if identity.setup:
        lines.append(f"Setup: {identity.setup}")
    if identity.driver or identity.phase or identity.setup:
        lines.append("")

    for lap_report in report.laps:
        label = lap_report.lap.label
        lines.append(f"## {label} — {lap_report.lap.lap_time:.2f} s")
        lines.append("")
        lines.append(lap_report.summary)
        lines.append("")
        for index, point in enumerate(lap_report.points):
            lines.append(f"- **{point.headline}**")
            if point.difference:
                lines.append(f"  - {point.difference} ({point.detail})")
            spoken = ""
            if lap_report.narrated is not None:
                spoken = lap_report.narrated.points[index].narration
            if spoken:
                lines.append(f"  - {spoken}")
        lines.append("")

    if report.narration_error:
        # Say why the prose is missing rather than letting it look like there
        # was nothing to say.
        lines.append(
            f"_Written coaching was unavailable for part of this session: "
            f"{report.narration_error}_"
        )
        lines.append("")
    return "\n".join(lines)
