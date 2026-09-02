"""Getting a participant's data off their laptop and onto the analyst's, intact.

Participants install on their own machines, so every session starts life on a
computer nobody in the study team controls and has to travel by whatever route
the participant has -- email, a shared drive, a memory stick. Two things have to
survive that: the laps themselves, and the knowledge of whose they are.

So a handover is one file, and it carries a digest of every member. A folder that
arrived with a file truncated by a mail gateway is not something anybody would
notice by looking, and a study that silently analyses eleven of a participant's
twelve laps has a hole in it that no later check can find.
"""

import hashlib
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from f1coach_core.exposure import adopt_views, ensure_log
from f1coach_core.lap import NO_IDENTITY, StudyIdentity
from f1coach_core.loader import TelemetrySchemaError
from f1coach_core.participant import (
    Background,
    background_path,
    load_background,
    save_background,
)
from f1coach_core.workspace import (
    ARRIVED_NAME,
    import_telemetry,
    sessions_root,
    workspace_root,
)

MANIFEST_NAME = "handover.json"
BACKGROUND_NAME = "participant.json"
EXPOSURE_NAME = "exposure.jsonl"
SCHEMA_VERSION = "apex-handover-v1"

# Names the package fills from the participant's workspace rather than from the
# capture folder. A folder that already holds one -- a handover somebody
# unpacked and packaged again -- would otherwise contribute a second member
# under the same name, and since `unpack` checks digests by name one of the two
# silently wins. A damaged file that a digest is meant to catch is exactly what
# that hides.
RESERVED_NAMES = frozenset({MANIFEST_NAME, BACKGROUND_NAME, EXPOSURE_NAME})

# How text that arrived from somebody else's machine is read: utf-8, plus "drop
# a byte-order mark if one is there". Everything Apex writes is plain utf-8, but
# these files travel, and on Windows a BOM is the commonest thing to survive a
# round trip through anything that rewrites them. Read as plain utf-8, a manifest
# with three extra bytes in front reports no participant at all, and the laps
# import as nobody's.
ARRIVING_ENCODING = "utf-8-sig"


def handovers_root() -> Path:
    """Where handovers from other people's machines are unpacked.

    Beside locally captured runs but never among them: a lap that arrived in a
    package was driven on a computer nobody here controls, and keeping that
    visible is part of what the package is for.
    """
    return workspace_root() / "captures" / "handovers"


class HandoverError(RuntimeError):
    """A package could not be built, or arrived damaged."""


@dataclass(frozen=True)
class Handover:
    path: Path
    participant_id: str
    files: int
    laps: int


@dataclass(frozen=True)
class Registered:
    """What arriving data became once this workspace could read it."""

    session: str
    laps: int
    background: bool
    views: int = 0  # coaching views adopted from this participant's log
    skipped: tuple[str, ...] = ()


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            sha.update(chunk)
    return sha.hexdigest()


def package(
    source: str | Path,
    destination: str | Path,
    *,
    participant_id: str | None = None,
) -> Handover:
    """Bundle one capture folder into a single verifiable file to hand over.

    The participant's background travels with it when there is one, because a
    comparability check months later cannot go back and ask them. So does the
    record of what coaching they looked at, for the same reason: how much of it
    they read is not recoverable from the telemetry, and the only machine that
    ever knew is the one being packaged.

    Both live in the participant's workspace rather than in the capture folder,
    so both are added by hand here. Neither is a lap, and a capture folder is a
    record of one run; these are records of a person.
    """
    source = Path(source)
    if not source.is_dir():
        raise HandoverError(f"Not a capture folder: {source}")

    members: list[tuple[Path, str]] = []
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.name not in RESERVED_NAMES:
            members.append((path, str(path.relative_to(source))))
    if not members:
        raise HandoverError(f"{source} holds no files to hand over.")

    if participant_id is None:
        participant_id = _participant_from(source)
    background = load_background(participant_id) if participant_id else None
    if background is not None:
        members.append((background_path(participant_id), BACKGROUND_NAME))
    if participant_id:
        # Included even when empty. A build that records exposure can say "they
        # looked at nothing" and mean it; a package with no exposure member came
        # from a build that never measured, which is a different answer and has
        # to stay one. See exposure.ensure_log.
        members.append((ensure_log(participant_id), EXPOSURE_NAME))

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "participant_id": participant_id or "",
        "packaged_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": source.name,
        "files": [
            {"name": name, "bytes": path.stat().st_size, "sha256": _digest(path)}
            for path, name in members
        ],
    }

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, name in members:
            archive.write(path, name)
        archive.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))

    return Handover(
        path=destination,
        participant_id=participant_id or "",
        files=len(members),
        laps=sum(1 for _p, name in members if name.endswith(".csv")),
    )


def _participant_from(source: Path) -> str | None:
    """Read the id out of the capture's own manifest rather than the folder name."""
    try:
        data = json.loads((source / "manifest.json").read_text(ARRIVING_ENCODING))
    except (OSError, ValueError):
        return None
    value = data.get("participant_id")
    return str(value) if value else None


def unpack(archive_path: str | Path, into: str | Path) -> Handover:
    """Verify a handover and lay it out under `into/<participant>-<source>/`.

    Every member is checked against the digest recorded when it was packaged.
    A mismatch stops the whole unpack: half a participant's session is worse
    than none, because it looks complete.
    """
    archive_path = Path(archive_path)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read(MANIFEST_NAME))
            expected = {entry["name"]: entry for entry in manifest.get("files", [])}
            if not expected:
                raise HandoverError(f"{archive_path.name} lists no files.")

            participant = manifest.get("participant_id") or "unknown"
            target = Path(into) / f"{participant}-{manifest.get('source', archive_path.stem)}"
            target.mkdir(parents=True, exist_ok=True)

            for name, entry in expected.items():
                data = archive.read(name)
                if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != (
                    entry["sha256"]
                ):
                    raise HandoverError(
                        f"{archive_path.name} is damaged: {name} does not match the "
                        "digest recorded when it was packaged. Ask for it again."
                    )
                out = target / name
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise HandoverError(f"{archive_path} is not a readable handover: {exc}") from exc

    return Handover(
        path=target,
        participant_id=manifest.get("participant_id", ""),
        files=len(expected),
        laps=sum(1 for name in expected if name.endswith(".csv")),
    )


def collect(archives: list[str | Path], into: str | Path) -> list[Handover]:
    """Unpack many handovers into one place, refusing to merge two of the same.

    A participant who sent their folder twice, or a facilitator who ran the same
    session through twice, would otherwise double that person's weight in the
    comparison without anything looking wrong.
    """
    into = Path(into)
    results: list[Handover] = []
    seen: set[str] = set()
    for archive in archives:
        handover = unpack(archive, into)
        key = f"{handover.participant_id}/{handover.path.name}"
        if key in seen:
            raise HandoverError(
                f"{Path(archive).name} is a second copy of {key}. Remove the "
                "duplicate before pooling, or it counts twice."
            )
        seen.add(key)
        results.append(handover)
    return results


def handover_identity(folder: str | Path) -> tuple[StudyIdentity, dict | None]:
    """Who drove this handover, and where its screen recording now lives.

    Read from the capture's own manifest rather than asked for at import time:
    a package opened months later, by somebody who was not there, still says
    what it came with.
    """
    folder = Path(folder)
    try:
        manifest = json.loads(
            (folder / "manifest.json").read_text(encoding=ARRIVING_ENCODING)
        )
    except (OSError, ValueError):
        return NO_IDENTITY, None
    if not isinstance(manifest, dict):
        return NO_IDENTITY, None
    preset = manifest.get("study_preset")
    identity = StudyIdentity(
        driver=manifest.get("participant_id") or None,
        phase=manifest.get("phase") or None,
        setup=preset.get("preset_id") if isinstance(preset, dict) else None,
    )
    recording = manifest.get("recording")
    if not isinstance(recording, dict):
        return identity, None
    # The path recorded on the participant's machine means nothing here, but
    # the file itself travelled inside the package.
    local = folder / Path(str(recording.get("path", ""))).name
    return identity, {**recording, "path": str(local)} if local.is_file() else None


def adopt_background(folder: str | Path) -> str | None:
    """Keep the questionnaire that travelled with the laps; return whose it is.

    Nothing in a telemetry file records prior experience, and a comparability
    check months from now cannot go back and ask. Dropping it on import is the
    one loss a handover cannot recover from.

    It has to be adopted into this workspace, not merely left in the unpacked
    folder, because the export reads a participant's background by id from the
    workspace. A questionnaire that arrived and stayed where it landed is a
    background column that exports blank.
    """
    folder = Path(folder)
    try:
        data = json.loads((folder / BACKGROUND_NAME).read_text(encoding=ARRIVING_ENCODING))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        background = Background.from_dict(data)
    except TypeError:
        return None
    if not background.participant_id:
        return None
    save_background(background)
    return background.participant_id


def adopt_exposure(folder: str | Path, driver: str | None) -> int:
    """Keep the record of what this participant looked at; return how many are new.

    The same argument as the questionnaire, one step further. Whether coaching
    changed how somebody drove is only answerable if we know they read it, and
    nothing in a telemetry file records that. It also has to be adopted into
    this workspace rather than left where it landed, because the export reads a
    participant's viewing by id -- a log that arrived and stayed put is a dose
    column that exports blank, which the analysis would correctly read as
    "nobody knows" and quietly drop from the one test this study can carry.

    Merging is by view id, so a participant whose second package repeats every
    view the first one carried is not counted twice.
    """
    source = Path(folder) / EXPOSURE_NAME
    if not driver or not source.is_file():
        return 0
    return adopt_views(source, driver)


def session_name(handover: Handover) -> str:
    """What the laps in this handover should be called in the workspace.

    ``unpack`` prefixes the folder with the participant id, and a capture folder
    is already named after them, so the folder name on its own reads
    "P007-P007-baseline-...". Use the capture's own name.
    """
    name = handover.path.name
    doubled = f"{handover.participant_id}-" * 2
    if handover.participant_id and name.startswith(doubled):
        name = name[len(handover.participant_id) + 1 :]
    return name


def mark_arrived(session_name: str, handover: Handover) -> Path | None:
    """Say, in the session itself, that these laps were driven somewhere else.

    Both doors into the study -- `register` here and the Garage's import --
    write it, because both create sessions on a machine that did not drive
    them. The alternative was for the exposure measure to guess from the
    workspace's shape, and a guess that is wrong once has already recorded a
    researcher's reading as a participant's dose.
    """
    from racecoach.telemetry.human_capture import human_captures_root

    directory = sessions_root() / session_name
    if not directory.is_dir():
        return None
    if (human_captures_root() / session_name).is_dir():
        # This machine drove it. Collecting a package of one's own capture is a
        # reasonable thing to do -- it is how a facilitator checks a handover
        # opens -- and it must not relabel the session as somebody else's, which
        # would move that participant's coaching views out of their own dose.
        return None
    marker = directory / ARRIVED_NAME
    marker.write_text(
        json.dumps(
            {
                "participant_id": handover.participant_id,
                "collected_from": handover.path.name,
                "collected_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return marker


def _register(folder: Path, name: str, *, adopt: bool = True) -> Registered:
    """Split every run in `folder` into canonical laps under session `name`."""
    identity, recording = handover_identity(folder)
    background = adopt_background(folder) if adopt else None
    views = adopt_exposure(folder, identity.driver or background) if adopt else 0
    skipped: list[str] = []
    for run in sorted(folder.glob("*.csv")):
        try:
            import_telemetry(run, name, identity=identity, recording=recording)
        except (TelemetrySchemaError, OSError) as exc:
            # One unreadable file must not cost four participants their data. A
            # capture folder routinely holds an export for a race that was
            # started and abandoned, and the participant is in no position to
            # tidy it up; say which file and carry on with the rest.
            skipped.append(f"{run.name}: {exc}")
    session = sessions_root() / name
    laps = len(list(session.glob("*.csv"))) if session.is_dir() else 0
    return Registered(
        session=name,
        laps=laps,
        background=background is not None,
        views=views,
        skipped=tuple(skipped),
    )


def register_capture(capture_dir: str | Path) -> Registered:
    """Make laps driven on this machine readable by the study path, right away.

    The Garage shows a capture the moment it finishes, but everything on the
    study path -- the summary, the per-lap export, the Study Results screen --
    reads canonical single laps out of `sessions_root`, and the only thing that
    ever put them there was `collect`, which runs on the analyst's machine. So a
    session was visible to the person who drove it and invisible to the study on
    the same computer, and the gap closed only after the data had made a round
    trip through a zip and back.

    Nothing is adopted and no arrival is marked, which is the whole difference
    from `register`. The background answers and the exposure log are already
    this workspace's own rather than copies of somebody else's, and
    `arrived.json` means "driven elsewhere" -- it is what keeps a researcher
    reading a debrief from being counted as the participant's dose.

    Importing is content-addressed, so running this after a capture and then
    collecting a package of the same capture adds the laps once.
    """
    capture_dir = Path(capture_dir)
    return _register(capture_dir, capture_dir.name, adopt=False)


def register(handover: Handover) -> Registered:
    """Turn a verified handover into laps this workspace can actually read.

    Verifying that files arrived intact is not the same as being able to analyse
    them. What travels is the participant's raw TORCS run export, and every
    reader on the study path -- the summary, the per-lap export, the Study
    Results screen -- takes canonical single-lap CSVs. Until the run is split,
    a pooled folder full of correct data summarises to nothing at all, which is
    indistinguishable from having collected nothing.

    Importing is content-addressed, so collecting the same handover twice adds
    no laps the second time rather than counting that participant twice.
    """
    name = session_name(handover)
    registered = _register(handover.path, name)
    mark_arrived(name, handover)
    return registered
