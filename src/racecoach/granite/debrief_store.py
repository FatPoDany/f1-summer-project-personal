"""Keeping the debrief that was produced, beside the session it was about.

A session debrief is the study's intervention. The coached arm drives, reads
this, and drives again -- so what it said is the record of what a participant
was told, and until now nothing kept it. It was built in memory when somebody
opened the screen, thrown away when they left it, and rebuilt from scratch the
next time. A participant's laptop could produce a debrief, show it, and leave
no trace that it ever existed; the folder they handed over said only that they
had driven.

The measured half was always recoverable -- ``adherence`` recomputes it from
the telemetry -- but the prose was not. The model is sampled at temperature 0,
which makes it repeatable and does not make it recoverable: a rerun months
later is a different build of Apex, possibly a different model, and in any case
an answer produced after the fact rather than the one that was on screen. So
this writes the report down when it is produced, on the machine that produced
it.

Records land in ``<session>/debrief/`` next to ``<session>/coaching/``, so a
debrief travels with the handover exactly as the per-lap audit trail does.

``arrived`` is the field that keeps the trail honest. A debrief generated on a
researcher's machine, over a folder a participant sent in, is an analysis
artefact and not the intervention -- nobody read it, and it may well differ
from what they did read. ``is_arrived`` already separates the two everywhere
else (``exposure`` refuses to record a dose against an imported session), and
it separates them here too, so the file says which kind of thing it is instead
of leaving both to look alike.
"""

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from f1coach_core.build import app_build
from f1coach_core.debrief import DebriefPoint
from f1coach_core.workspace import _unique_dest, is_arrived
from racecoach.granite.narrate import NarratedDebrief, NarratedPoint
from racecoach.granite.report import LapReport, SessionReport, render_markdown

DEBRIEF_DIR_NAME = "debrief"
SCHEMA_VERSION = "apex-debrief-v1"


@dataclass(frozen=True)
class StoredLapNarration:
    """What the model said about one lap, and what it was said about.

    ``keys`` is the point it was said about, not a checksum of it. Prose is
    restored onto a freshly measured report, and measurement is what moves: a
    lap re-measured under a later Apex against a different session best can
    produce a different set of stretches in a different order. Attaching by
    position alone would then put last month's sentence about Turn 3 under this
    month's finding about Turn 7, which reads perfectly and is a fabrication.
    """

    summary: str
    keys: tuple[tuple[str, str, str], ...]  # (corner, metric, difference) per point
    prose: tuple[tuple[str, str], ...]  # (narration, advice) per point
    model: str = ""

    def matches(self, points: tuple[DebriefPoint, ...]) -> bool:
        return self.keys == tuple(_point_key(point) for point in points)


@dataclass(frozen=True)
class StoredDebrief:
    """A debrief that was produced once, and the file that proves it."""

    path: Path
    written_at: str
    narrated: bool
    findings: int
    markdown: str
    build: str = ""
    arrived: bool = False
    model: str = ""
    narration_error: str = ""
    narration: dict[str, StoredLapNarration] = field(default_factory=dict)


def _point_key(point: DebriefPoint) -> tuple[str, str, str]:
    return (point.corner, point.metric, point.difference)


def debrief_dir(session_dir: str | Path) -> Path:
    return Path(session_dir) / DEBRIEF_DIR_NAME


def is_narrated(report: SessionReport) -> bool:
    """Whether the model spoke for any lap of this report.

    Any rather than all: a session where only some laps had something to say is
    a narrated session, and one where the model died half way through carries
    its reason in ``narration_error`` rather than being demoted to measured.
    """
    return any(item.narrated is not None for item in report.laps)


def write_debrief(
    session_dir: str | Path,
    report: SessionReport,
    *,
    model: str = "",
) -> Path:
    """Write one debrief record; returns its path. Raises OSError on failure.

    The caller decides whether keeping the record may break the thing it
    records -- the screen has already shown the participant their debrief by
    the time this runs, and failing to file it must not take that away.
    """
    session_dir = Path(session_dir)
    now = datetime.now(UTC)
    identity = report.laps[0].lap.identity if report.laps else None
    record = {
        "schema": SCHEMA_VERSION,
        "written_at": now.isoformat(timespec="seconds"),
        "session": session_dir.name,
        "driver": identity.driver if identity else "",
        "phase": identity.phase if identity else "",
        "setup": identity.setup if identity else "",
        # Which build wrote it. The debrief is the intervention and the
        # intervention changed while collection was running, so two conditions
        # would otherwise pool into one that never existed -- the same reason
        # `exposure` stamps every view.
        "build": app_build(),
        # Whether anybody was actually shown this. See the module docstring.
        "arrived": is_arrived(session_dir),
        "narrated": is_narrated(report),
        "model": model if is_narrated(report) else "",
        "narration_error": report.narration_error,
        "findings": report.findings,
        "reference_lap": (
            report.reference.source.stem if report.reference is not None else ""
        ),
        "laps": [_lap_record(item) for item in report.laps],
        # What it actually said, in the form `racecoach debrief` writes and the
        # Save button exports. Kept inline rather than as a sibling .md so that
        # one file is the whole record and cannot arrive half copied.
        "markdown": render_markdown(report),
    }
    directory = debrief_dir(session_dir)
    directory.mkdir(parents=True, exist_ok=True)
    # Microseconds, not seconds. `_unique_dest` resolves a collision by
    # appending "-2", and "-2.json" sorts BEFORE ".json" -- so two records
    # written in the same second would make `latest_debrief` return the older
    # one, which is the single thing it exists to get right.
    dest = _unique_dest(directory, f"{now:%Y%m%d-%H%M%S-%f}-debrief.json")
    dest.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return dest


def _lap_record(item: LapReport) -> dict:
    """One lap of the report, with the prose kept beside the point it is about."""
    record = {
        "lap": item.lap.source.stem,
        "lap_number": item.lap.lap_number,
        "lap_time_s": round(item.lap.lap_time, 3),
        "points": len(item.points),
        "spoken": item.spoken_count,
        "summary": item.summary,
        "keys": [list(_point_key(point)) for point in item.points],
    }
    if item.narrated is not None:
        record["prose"] = [
            [spoken.narration, spoken.advice] for spoken in item.narrated.points
        ]
        record["model"] = item.narrated.model
    return record


def _lap_narration(record: object) -> tuple[str, StoredLapNarration] | None:
    """One stored lap turned back into prose, or None if it carries none."""
    if not isinstance(record, dict) or not record.get("prose"):
        return None
    lap = str(record.get("lap") or "")
    keys = record.get("keys")
    prose = record.get("prose")
    if not lap or not isinstance(keys, list) or not isinstance(prose, list):
        return None
    if len(keys) != len(prose):
        return None
    try:
        parsed_keys = tuple((str(k[0]), str(k[1]), str(k[2])) for k in keys)
        parsed_prose = tuple((str(p[0]), str(p[1])) for p in prose)
    except (IndexError, TypeError):
        return None
    return lap, StoredLapNarration(
        summary=str(record.get("summary") or ""),
        keys=parsed_keys,
        prose=parsed_prose,
        model=str(record.get("model") or ""),
    )


def restore_narration(report: SessionReport, stored: StoredDebrief) -> SessionReport:
    """Put previously produced prose back on a freshly measured report.

    Lap by lap, and only where the stretches measured now are the stretches it
    was written about. A lap that no longer matches keeps its measurements and
    loses its prose, which is the safe direction: a participant sees the
    numbers with nothing said about them, rather than a sentence about a corner
    this run never found.
    """
    if not stored.narration:
        return report
    laps = []
    for item in report.laps:
        narration = stored.narration.get(item.lap.source.stem)
        if narration is None or not narration.matches(item.points):
            laps.append(item)
            continue
        spoken = NarratedDebrief(
            summary=narration.summary,
            points=tuple(
                NarratedPoint(point=point, narration=text, advice=advice)
                for point, (text, advice) in zip(item.points, narration.prose, strict=True)
            ),
            model=narration.model,
        )
        laps.append(replace(item, summary=narration.summary, narrated=spoken))
    return replace(report, laps=tuple(laps))


def latest_debrief(session_dir: str | Path) -> StoredDebrief | None:
    """The newest readable debrief record for this session, or None.

    Timestamped names make lexical order chronological. Unreadable records are
    skipped rather than fatal: these files are read back on a participant's
    machine to decide whether to spend minutes of their CPU, and a corrupt one
    should cost them a rerun, not the screen.
    """
    directory = debrief_dir(session_dir)
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict) or record.get("schema") != SCHEMA_VERSION:
            continue
        narration = dict(
            entry
            for entry in (_lap_narration(item) for item in record.get("laps") or [])
            if entry is not None
        )
        return StoredDebrief(
            path=path,
            written_at=str(record.get("written_at") or ""),
            narrated=bool(record.get("narrated")),
            findings=int(record.get("findings") or 0),
            markdown=str(record.get("markdown") or ""),
            build=str(record.get("build") or ""),
            arrived=bool(record.get("arrived")),
            model=str(record.get("model") or ""),
            narration_error=str(record.get("narration_error") or ""),
            narration=narration,
        )
    return None
