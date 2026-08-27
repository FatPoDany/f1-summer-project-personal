"""How much coaching a participant actually took in, measured rather than assumed.

A study that hands somebody advice and then measures their driving has tested
the advice only on the assumption that they read it. That assumption does real
work here. With a handful of people per arm the between-arm comparison is
underpowered almost by construction, and the one piece of causal evidence that
does not depend on the two arms being alike is dose and response: whether the
people who looked at more of it improved more. That comparison happens inside
the coached arm, so two groups differing to begin with cannot explain it away.

Nothing in here infers engagement from lap times, or from the fact that a report
was successfully generated. A view is recorded when a participant had something
in front of them and closed when they no longer did. Views are appended one JSON
object per line, per participant, so a session that ends badly still leaves
everything up to the moment it did.

What the numbers are not: this measures what was on screen, not what was read.
Nobody can measure the second, and calling the first "attention" would be the
same overreach as calling a missing channel zero.
"""

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from f1coach_core.workspace import SAMPLE_SESSION_NAME, workspace_root

SCHEMA_VERSION = "apex-exposure-v1"
EXPOSURE_DIR_NAME = "exposure"

# The two things a participant can be looking at. A corner is the review window
# open on one stretch; a report is the coach's findings on the analysis screen.
# Kept apart because they are different doses: somebody who read the panel and
# never opened a corner is a real case, and pooling the two would hide it.
CORNER_VIEW = "corner"
REPORT_VIEW = "report"

# A view shorter than this is somebody arrowing down the corner list, not
# somebody reading it. Counting keystrokes as attention would inflate exactly
# the number the dose-response argument rests on. The same reasoning as
# MIN_EXCURSION_SAMPLES: the shortest thing worth calling an event at all.
MIN_VIEW_SECONDS = 1.0

# The demonstration that ships with Apex, under the name it has inside the
# package and the name it is copied to in a workspace. Its five laps are real
# and still carry a real participant's identity -- the same trap
# list_study_sessions exists for -- so views of them would otherwise record a
# researcher exploring the demo as that participant reading their own coaching.
SAMPLE_LAP_FOLDERS = frozenset({SAMPLE_SESSION_NAME, "sample_session"})


def is_recordable(lap_source: str | Path | None) -> bool:
    """Whether looking at this lap is somebody's dose or nobody's."""
    if lap_source is None:
        return False
    return Path(lap_source).parent.name not in SAMPLE_LAP_FOLDERS


@dataclass(frozen=True)
class ReviewView:
    """One thing a participant had in front of them, and for how long."""

    driver: str
    phase: str  # the phase of the lap looked at, not the one being driven next
    kind: str  # CORNER_VIEW or REPORT_VIEW
    seconds: float
    lap: str = ""  # the lap file it was about
    corner: str = ""  # empty for a report view
    advice: bool = False  # a validated AI instruction was on screen with it
    findings: int | None = None  # report views: how many were on screen
    at: str = ""  # UTC, when the view ended
    id: str = ""  # stable, so a log that travels twice cannot count twice

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "at": self.at,
            "driver": self.driver,
            "phase": self.phase,
            "kind": self.kind,
            "lap": self.lap,
            "corner": self.corner,
            "seconds": round(self.seconds, 2),
            "advice": self.advice,
            "findings": self.findings,
        }


# One view as a row. Ordered so a spreadsheet reads left to right as "who, in
# what condition, looked at what, for how long".
VIEW_COLUMNS = (
    "driver",
    "phase",
    "kind",
    "corner",
    "lap",
    "seconds",
    "advice",
    "findings",
    "at",
)


def _view_from(data: object) -> ReviewView | None:
    """Rebuild one view from a log line, or reject it.

    Exposure logs arrive from participants' machines the same way telemetry
    does, so they are untrusted input: a line that is not a view of a known kind
    for a known participant is skipped rather than repaired into one.
    """
    if not isinstance(data, dict):
        return None
    driver = str(data.get("driver") or "")
    phase = str(data.get("phase") or "")
    kind = str(data.get("kind") or "")
    if not driver or not phase or kind not in (CORNER_VIEW, REPORT_VIEW):
        return None
    try:
        seconds = float(data.get("seconds"))
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    findings = data.get("findings")
    return ReviewView(
        driver=driver,
        phase=phase,
        kind=kind,
        seconds=seconds,
        lap=str(data.get("lap") or ""),
        corner=str(data.get("corner") or ""),
        advice=bool(data.get("advice")),
        findings=int(findings) if isinstance(findings, int) else None,
        at=str(data.get("at") or ""),
        id=str(data.get("id") or ""),
    )


def exposure_root() -> Path:
    return workspace_root() / EXPOSURE_DIR_NAME


def exposure_path(driver: str) -> Path:
    """One log per participant, named the way their questionnaire is."""
    return exposure_root() / f"{driver}.jsonl"


def append_view(view: ReviewView) -> Path | None:
    """Add one finished view to its participant's log.

    A view with nobody to attribute it to is not written at all. Laps opened
    outside the study -- the sample session, a loose CSV somebody dropped in --
    carry no identity, and a dose belongs to a participant or to nothing.
    """
    if not view.driver or not view.phase:
        return None
    path = exposure_path(view.driver)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(view.to_dict()) + "\n")
    return path


def ensure_log(driver: str) -> Path:
    """This participant's log, created empty when they have none yet.

    Called when a handover is built, so somebody who never opened a review still
    hands over a log rather than nothing at all. "Recorded, and looked at
    nothing" is a measurement and belongs to everybody who took part under a
    build that measures; "no log" is the absence of one, and should only ever
    describe data that came from a build predating this.
    """
    path = exposure_path(driver)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    return path


def read_views(path: str | Path) -> list[ReviewView]:
    """Every readable view in one log, skipping lines that are not views.

    Read as utf-8-sig, which is utf-8 plus "drop a byte-order mark if one is
    there". Logs cross machines, and a BOM is the commonest way a file written
    on Windows differs -- anything that rewrites this one on the way (a text
    editor, a sync client) puts three bytes in front of the first line, and
    plain utf-8 then makes a perfectly good view disappear with no line to
    point at. Skipping malformed lines is deliberate; skipping a sound one
    because of an encoding marker is not.
    """
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return []
    views: list[ReviewView] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        view = _view_from(data)
        if view is not None:
            views.append(view)
    return views


def load_exposure(root: str | Path | None = None) -> dict[str, list[ReviewView]]:
    """Every participant's log in this workspace, keyed by participant.

    A participant with a log and no views maps to an empty list, and that is a
    different fact from having no log at all: the first says they were recorded
    and looked at nothing, the second says nobody recorded whether they did.
    Collapsing the two would hand the least-engaged reading to exactly the
    participants nothing is known about.
    """
    directory = Path(root) if root is not None else exposure_root()
    if not directory.is_dir():
        return {}
    return {path.stem: read_views(path) for path in sorted(directory.glob("*.jsonl"))}


def adopt_views(source: str | Path, driver: str) -> int:
    """Merge a log that travelled with a handover; return how many were new.

    Union by the id each view was written with, because a participant's second
    package repeats every view the first one carried: the log is a snapshot of
    everything up to the moment it was packaged, not of that phase alone.
    Appending it again would count a corner twice for exactly the participants
    who took part in the most phases.

    The destination is created even when nothing is added, so a participant who
    was recorded and looked at nothing reads as zero rather than as unknown.
    """
    destination = exposure_path(driver)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        destination.write_text("", encoding="utf-8")
    known = {view.id for view in read_views(destination) if view.id}
    fresh = [
        view
        for view in read_views(source)
        if view.driver == driver and (not view.id or view.id not in known)
    ]
    if not fresh:
        return 0
    with open(destination, "a", encoding="utf-8") as handle:
        for view in fresh:
            handle.write(json.dumps(view.to_dict()) + "\n")
    return len(fresh)


class ExposureLog:
    """One thing at a time, timed from when it appeared to when it went away.

    The front end says what a participant can now see and when they can no
    longer see it; the arithmetic lives here, because how long somebody looked
    at a corner is a study measurement and the house rule keeps those out of the
    widgets. It also means the clock can be tested without waiting.

    Paused rather than stopped when a window loses focus: a review window left
    behind the analysis screen is not being read, and the minute before it went
    behind is not thrown away either.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sink: Callable[[ReviewView], object] = append_view,
    ) -> None:
        # Monotonic, so a laptop whose wall clock steps mid-session cannot
        # produce a negative view.
        self._clock = clock
        self._sink = sink
        self._open: ReviewView | None = None
        self._started: float | None = None  # None while paused
        self._elapsed = 0.0

    @property
    def open_view(self) -> ReviewView | None:
        """What is being timed right now, its seconds not yet settled."""
        return self._open

    def opened(
        self,
        *,
        driver: str | None,
        phase: str | None,
        kind: str,
        lap_source: str | Path | None = None,
        corner: str = "",
        advice: bool = False,
        findings: int | None = None,
    ) -> None:
        """Start timing something now on screen, closing whatever was before.

        The lap is handed over as its path rather than its name, because whether
        a view counts at all is a question about where the lap came from and the
        front end should not be the one deciding it.
        """
        self.closed()
        if not driver or not phase:
            return  # not a study lap: there is nobody to attribute a dose to
        if lap_source is not None and not is_recordable(lap_source):
            return
        self._open = ReviewView(
            driver=driver,
            phase=phase,
            kind=kind,
            seconds=0.0,
            lap=Path(lap_source).name if lap_source is not None else "",
            corner=corner,
            advice=advice,
            findings=findings,
        )
        self._elapsed = 0.0
        self._started = self._clock()

    def advice_arrived(self, findings: int | None = None) -> None:
        """The instruction appeared while they were already looking at this.

        Coaching runs in the background, so a corner is routinely opened before
        there is anything to read in it. Whether advice was ever on screen is
        what a dose is made of, not whether it got there first.
        """
        if self._open is None:
            return
        self._open = replace(
            self._open,
            advice=True,
            findings=self._open.findings if findings is None else findings,
        )

    def paused(self) -> None:
        """Looking at something else; keep what they have already had."""
        if self._started is None:
            return
        self._elapsed += max(0.0, self._clock() - self._started)
        self._started = None

    def resumed(self) -> None:
        """Back in front of them, continuing the same view."""
        if self._open is None or self._started is not None:
            return
        self._started = self._clock()

    def closed(self) -> ReviewView | None:
        """Write the finished view, unless it was too brief to have been read."""
        self.paused()
        view, elapsed = self._open, self._elapsed
        self._open, self._elapsed = None, 0.0
        if view is None or elapsed < MIN_VIEW_SECONDS:
            return None
        finished = replace(
            view,
            seconds=round(elapsed, 2),
            at=datetime.now(UTC).isoformat(timespec="seconds"),
            id=uuid.uuid4().hex,
        )
        self._sink(finished)
        return finished


@dataclass(frozen=True)
class PhaseExposure:
    """What one participant looked at that belongs to one phase."""

    review_seconds: float
    review_corners: int
    review_corners_seen: int  # distinct corners, so breadth is not depth
    advice_seconds: float
    report_seconds: float


def phase_exposure(views: list[ReviewView], phase: str) -> PhaseExposure:
    """Add up one participant's views of one phase's laps.

    The phase is the one they were looking at, not the one they went on to
    drive. Reviewing baseline laps is the dose that the second run is the
    response to; reviewing the second run's laps afterwards is exposure that
    arrived too late to have caused anything, and keeping the two on separate
    rows is what lets an analyst tell them apart instead of summing them.
    """
    corners = [v for v in views if v.phase == phase and v.kind == CORNER_VIEW]
    reports = [v for v in views if v.phase == phase and v.kind == REPORT_VIEW]
    return PhaseExposure(
        review_seconds=round(sum(v.seconds for v in corners), 1),
        review_corners=len(corners),
        review_corners_seen=len({v.corner for v in corners if v.corner}),
        advice_seconds=round(sum(v.seconds for v in corners if v.advice), 1),
        report_seconds=round(sum(v.seconds for v in reports), 1),
    )


EXPOSURE_COLUMNS = (
    "review_seconds",
    "review_corners",
    "review_corners_seen",
    "advice_seconds",
    "report_seconds",
)


def exposure_columns(views: list[ReviewView] | None, *, phase: str) -> dict:
    """Flat columns for the study export; blank when nothing was recorded.

    None and an empty list are different answers, and the whole point of the
    distinction is which participants get which. Blank means no log reached this
    workspace -- an older build, a package cut before this measure existed, a
    control participant who never opened the app between runs -- and a
    statistical tool reading it as missing is correct. Zero is a measurement:
    they were recorded, and they looked at nothing.
    """
    if views is None:
        return dict.fromkeys(EXPOSURE_COLUMNS, "")
    found = phase_exposure(views, phase)
    return {
        "review_seconds": found.review_seconds,
        "review_corners": found.review_corners,
        "review_corners_seen": found.review_corners_seen,
        "advice_seconds": found.advice_seconds,
        "report_seconds": found.report_seconds,
    }
