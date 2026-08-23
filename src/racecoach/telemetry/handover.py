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

from f1coach_core.participant import background_path, load_background
from f1coach_core.workspace import workspace_root

MANIFEST_NAME = "handover.json"
SCHEMA_VERSION = "apex-handover-v1"


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
    comparability check months later cannot go back and ask them.
    """
    source = Path(source)
    if not source.is_dir():
        raise HandoverError(f"Not a capture folder: {source}")

    members: list[tuple[Path, str]] = []
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.name != MANIFEST_NAME:
            members.append((path, str(path.relative_to(source))))
    if not members:
        raise HandoverError(f"{source} holds no files to hand over.")

    if participant_id is None:
        participant_id = _participant_from(source)
    background = load_background(participant_id) if participant_id else None
    if background is not None:
        members.append((background_path(participant_id), "participant.json"))

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
        data = json.loads((source / "manifest.json").read_text("utf-8"))
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
