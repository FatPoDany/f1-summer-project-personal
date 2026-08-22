"""The on-disk workspace: ~/Apex/sessions/<name>/*.csv.

Set APEX_WORKSPACE to relocate it (tests do; so can users).
"""

import os
import shutil
from importlib.resources import as_file, files
from pathlib import Path

from f1coach_core.lap import NO_IDENTITY, StudyIdentity

SAMPLE_SESSION_NAME = "sample-session"


def workspace_root() -> Path:
    return Path(os.environ.get("APEX_WORKSPACE", str(Path.home() / "Apex")))


def sessions_root() -> Path:
    return workspace_root() / "sessions"


def list_sessions() -> list[Path]:
    root = sessions_root()
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink())


def _validate_session_name(name: str) -> str:
    name = name.strip()
    if not name or not name.strip(".") or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError(
            f"Session name {name!r} won't work as a folder name — use letters, "
            "numbers, spaces, dashes or underscores."
        )
    return name


def create_session(name: str) -> Path:
    name = _validate_session_name(name)
    path = sessions_root() / name
    if path.is_symlink():
        raise ValueError(f"Session {name!r} is a symbolic link and cannot be managed safely.")
    path.mkdir(parents=True, exist_ok=True)
    return path


def delete_session(name: str) -> Path:
    """Delete one managed session directory, never an imported source file."""
    name = _validate_session_name(name)
    root = sessions_root().resolve(strict=False)
    target = sessions_root() / name
    if target.is_symlink():
        raise ValueError(f"Session {name!r} is a symbolic link and cannot be deleted safely.")
    try:
        resolved = target.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"No session named {name!r} exists in {root}.") from exc
    if resolved.parent != root or not resolved.is_dir():
        raise ValueError(f"Session {name!r} is not a managed session directory.")
    shutil.rmtree(resolved)
    return resolved


def import_lap(src: str | Path, session_name: str) -> Path:
    """Copy a telemetry CSV into a session, never overwriting an existing lap."""
    src = Path(src)
    dest = _unique_dest(create_session(session_name), src.name)
    shutil.copy2(src, dest)
    return dest


def import_telemetry(
    src: str | Path, session_name: str, identity: StudyIdentity = NO_IDENTITY
) -> str:
    """Import any supported telemetry file; returns a human-readable summary.

    Canonical lap CSVs are copied as-is. TORCS run exports (Lin's exporter)
    are split at start-line crossings; each complete lap lands as its own
    canonical CSV, incomplete fragments (grid start, cut-off final lap) are
    skipped.

    ``identity`` is stamped into every lap written here, so a study lap keeps
    saying who drove it wherever the file ends up.
    """
    from f1coach_core.torcs import canonical_lap_text, is_torcs_export, split_torcs_run

    src = Path(src)
    if not is_torcs_export(src):
        import_lap(src, session_name)
        return f"Imported {src.name}"

    _carry_recording_pointer(src, session_name)
    laps = split_torcs_run(src)
    complete = [lap for lap in laps if lap.complete]
    dest_dir = create_session(session_name)
    existing_contents = {
        existing.read_bytes() for existing in dest_dir.glob("*.csv") if existing.is_file()
    }
    imported = 0
    already_present = 0
    for lap in complete:
        content = canonical_lap_text(lap, src.name, identity)
        encoded = content.encode("utf-8")
        if encoded in existing_contents:
            already_present += 1
            continue
        dest = _unique_dest(dest_dir, f"{src.stem}-lap{lap.lap_label:02d}.csv")
        dest.write_text(content, encoding="utf-8")
        existing_contents.add(encoded)
        imported += 1
    skipped = len(laps) - len(complete)
    noun = "new laps" if already_present else "laps"
    summary = f"Imported {imported} {noun} from TORCS run {src.name}"
    details = []
    if already_present:
        details.append(f"{already_present} already present")
    if skipped:
        details.append(
            f"{skipped} incomplete fragment{'s' if skipped != 1 else ''} skipped"
        )
    if details:
        summary += f" ({', '.join(details)})"
    return summary


RECORDING_POINTER = "recording.json"


def _carry_recording_pointer(src: Path, session_name: str) -> None:
    """Note where this session's footage lives, if the capture recorded any.

    A pointer rather than a copy: the recording is hundreds of megabytes and
    already sits in the capture folder the participant hands over. Duplicating it
    into the session would double that for no gain, and the clips cut from it are
    small enough to travel on their own.
    """
    import json

    manifest = src.parent / "manifest.json"
    try:
        recording = json.loads(manifest.read_text("utf-8")).get("recording")
    except (OSError, ValueError, AttributeError):
        return
    if not isinstance(recording, dict) or not recording.get("path"):
        return
    try:
        (sessions_root() / session_name / RECORDING_POINTER).write_text(
            json.dumps(recording, indent=2), encoding="utf-8"
        )
    except OSError:
        return


def session_recording(session_dir: str | Path):
    """The recording behind a session, or None when there is not a usable one."""
    import json

    from racecoach.telemetry.screen_capture import Recording

    try:
        data = json.loads((Path(session_dir) / RECORDING_POINTER).read_text("utf-8"))
        recording = Recording.from_dict(data)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    # A pointer outlives the file it names: a participant may have moved or
    # deleted the capture folder, and a dangling path would fail deep inside
    # ffmpeg rather than here.
    return recording if recording.path.is_file() else None


def _unique_dest(dest_dir: Path, name: str) -> Path:
    dest = dest_dir / name
    counter = 2
    while dest.exists():
        dest = dest_dir / f"{dest_dir.joinpath(name).stem}-{counter}{Path(name).suffix}"
        counter += 1
    return dest


def ensure_sample_session() -> Path:
    """First run: materialise the bundled sample session into the workspace."""
    target = sessions_root() / SAMPLE_SESSION_NAME
    if not target.is_dir():
        target.mkdir(parents=True)
        bundled = files("f1coach_core") / "data" / "sample_session"
        for entry in bundled.iterdir():
            if entry.name.endswith(".csv"):
                with as_file(entry) as real:
                    shutil.copy2(real, target / entry.name)
    return target
