"""Everything that has to happen after a race, in one place.

The other half of this project closes a loop: the race ends, the client uploads
what it recorded, the browser opens on the review, and the driver goes again.
Apex has had every part of that for a while and none of the joins, so finishing
a session meant a participant reading an instruction, choosing a folder in a
save dialog, and a researcher later running three commands by hand. Each of
those is a place the loop stops.

So this is the join, and it is one function rather than one in the GUI and
another in the CLI, because two of them would drift and only one of them would
be tested.

Two rules shape it.

**Nothing here may cost a participant their data.** By the time this runs, the
race is on disk and the laps are already in the Garage. Every step is therefore
isolated: a failed upload must not stop the file being written that lets the
data leave the machine at all, and a failed anything must not raise into the
window the participant is looking at. What each step did, or did not do, comes
back in the result rather than as an exception.

**Sending is not something to do by accident.** Uploading is a network action
that puts human-subject data on a third-party host, so it happens only where an
upload key has deliberately been put -- an environment variable, or the
configuration file their installer ships, dropped beside the executable by
somebody who meant to. No key is not an error here; it is a machine that was
never set up to send, which is the correct default for a machine that was not.
"""

from __future__ import annotations

import json
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from f1coach_core.ibmf1 import COACHED_PHASE, player_key_for, study_arm_for
from f1coach_core.ibmf1 import package as export_ibmf1
from racecoach.telemetry.handover import package as package_handover
from racecoach.telemetry.handover import register_capture
from racecoach.telemetry.ibmf1_upload import IbmF1UploadError, deliver, resolve_endpoint

ARCHIVE_SUFFIX = ".zip"
BUNDLE_SUFFIX = "-ibmf1.zip"


@dataclass(frozen=True)
class Step:
    """One thing that was tried, and what came of it."""

    name: str
    done: bool
    detail: str

    def __str__(self) -> str:
        return f"{'ok' if self.done else '--'}  {self.name}: {self.detail}"


@dataclass(frozen=True)
class Handoff:
    """What became of a finished session."""

    capture_dir: Path
    session: str | None = None
    laps: int = 0
    archive: Path | None = None
    bundle: Path | None = None
    review_url: str | None = None
    delivered: bool = False
    opened: bool = False
    steps: tuple[Step, ...] = field(default_factory=tuple)

    @property
    def lines(self) -> tuple[str, ...]:
        return tuple(str(step) for step in self.steps)


def default_archive_dir() -> Path:
    """Where the file a participant has to send should land.

    The Desktop, not the workspace. Apex keeps its data somewhere a participant
    has no reason to browse, and a file they cannot find is one they will not
    send.
    """
    desktop = Path.home() / "Desktop"
    return desktop if desktop.is_dir() else Path.home()


def capture_phase(capture_dir: Path) -> str | None:
    """The study phase this capture recorded, or None if it did not record one."""
    try:
        manifest = json.loads((capture_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    phase = manifest.get("phase")
    return phase if isinstance(phase, str) and phase else None


def finish_session(
    capture_dir: str | Path,
    *,
    archive_dir: Path | None = None,
    session_number: int | None = None,
    upload: bool | None = None,
    open_review: bool | None = None,
    include_video: bool = True,
    progress: Callable[[str], None] | None = None,
    register_fn: Callable[..., object] = register_capture,
    package_fn: Callable[..., object] = package_handover,
    export_fn: Callable[..., object] = export_ibmf1,
    deliver_fn: Callable[..., object] = deliver,
    endpoint_fn: Callable[[], object] = resolve_endpoint,
    open_url: Callable[[str], bool] = webbrowser.open,
) -> Handoff:
    """Register, package, send, and open the review -- as far as each gets.

    `upload=None` means "send if this machine was set up to send". `open_review`
    defaults to opening only a coached race, which is what the other half's own
    control build does: their no-coach installer collects the race and does not
    open the review at all. Opening it for a control participant would hand them
    the page the study is keeping from them.
    """
    capture_dir = Path(capture_dir)
    steps: list[Step] = []
    say = progress or (lambda _line: None)

    def record(name: str, done: bool, detail: str) -> None:
        steps.append(Step(name, done, detail))
        say(str(steps[-1]))

    session: str | None = None
    laps = 0
    try:
        registered = register_fn(capture_dir)
        session, laps = registered.session, registered.laps
        record("laps read into the study", True, f"{laps} lap(s) in session {session}")
        for note in registered.skipped:
            record("skipped a run", False, note)
    except Exception as exc:  # noqa: BLE001 - a failure here must not stop the rest
        record("laps read into the study", False, str(exc))

    archive: Path | None = None
    destination = (archive_dir or default_archive_dir()) / f"{capture_dir.name}{ARCHIVE_SUFFIX}"
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = package_fn(capture_dir, destination)
        archive = Path(result.path)
        record("file to send saved", True, f"{result.files} files -> {archive}")
    except Exception as exc:  # noqa: BLE001
        record("file to send saved", False, str(exc))

    arm = study_arm_for(capture_phase(capture_dir))
    if upload is None:
        upload = _can_send(endpoint_fn, record)
    if not upload:
        return _finish(capture_dir, session, laps, archive, None, None, False, False, steps)
    if laps < 1:
        record(
            "sent to the Coach server",
            False,
            "no complete lap in this capture, so there is nothing to review",
        )
        return _finish(capture_dir, session, laps, archive, None, None, False, False, steps)

    bundle: Path | None = None
    try:
        exported = export_fn(
            capture_dir,
            capture_dir.with_name(f"{capture_dir.name}{BUNDLE_SUFFIX}"),
            session_number=session_number,
            include_background=True,
            include_video=include_video,
        )
        bundle = Path(exported.path)
        detail = f"{exported.rows} rows, {exported.bytes / (1024 * 1024):.1f} MB"
        if getattr(exported, "paused_s", 0):
            detail += f", {exported.paused_s:.0f} s of pause cut out"
        record("translated for their pipeline", True, detail)
    except Exception as exc:  # noqa: BLE001
        record("translated for their pipeline", False, str(exc))
        return _finish(capture_dir, session, laps, archive, None, None, False, False, steps)

    review_url: str | None = None
    delivered = False
    try:
        outcome = deliver_fn(bundle)
        review_url, delivered = outcome.review_url, True
        record("sent to the Coach server", True, outcome.message)
    except IbmF1UploadError as exc:
        record("sent to the Coach server", False, str(exc))
    except Exception as exc:  # noqa: BLE001
        record("sent to the Coach server", False, str(exc))

    if delivered and arm == COACHED_PHASE:
        # Said every time, because it is the difference between a participant
        # opening the site and finding a coach and opening it and finding
        # nothing. Their server ignores the arm a bundle declares; only a
        # researcher's assignment on their dashboard turns coaching on.
        record(
            "still to do",
            False,
            f"assign {player_key_for(_participant(capture_dir))} to coached on their "
            "Operations Dashboard, or this race offers no AI coaching",
        )

    opened = False
    if open_review is None:
        open_review = arm == COACHED_PHASE
    if open_review and delivered and review_url:
        try:
            opened = bool(open_url(review_url))
        except Exception as exc:  # noqa: BLE001
            record("review opened", False, str(exc))
        else:
            record("review opened", opened, review_url)
    elif delivered and review_url:
        record(
            "review not opened",
            True,
            f"{arm} arm: the site withholds coaching, which is what this arm asks for",
        )

    return _finish(
        capture_dir, session, laps, archive, bundle, review_url, delivered, opened, steps
    )


def _can_send(endpoint_fn: Callable[[], object], record: Callable[..., None]) -> bool:
    try:
        endpoint_fn()
    except IbmF1UploadError as exc:
        # Not an error. A machine with no key was never set up to send, and
        # saying so once is more use than failing a step that was never asked
        # for.
        record("not sent", True, str(exc))
        return False
    return True


def _participant(capture_dir: Path) -> str:
    try:
        manifest = json.loads((capture_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    value = manifest.get("participant_id")
    return value if isinstance(value, str) else ""


def _finish(
    capture_dir: Path,
    session: str | None,
    laps: int,
    archive: Path | None,
    bundle: Path | None,
    review_url: str | None,
    delivered: bool,
    opened: bool,
    steps: list[Step],
) -> Handoff:
    return Handoff(
        capture_dir=capture_dir,
        session=session,
        laps=laps,
        archive=archive,
        bundle=bundle,
        review_url=review_url,
        delivered=delivered,
        opened=opened,
        steps=tuple(steps),
    )
