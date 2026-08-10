"""The on-disk workspace: ~/Apex/sessions/<name>/*.csv.

Set APEX_WORKSPACE to relocate it (tests do; so can users).
"""

import os
import shutil
from importlib.resources import as_file, files
from pathlib import Path

SAMPLE_SESSION_NAME = "sample-session"


def workspace_root() -> Path:
    return Path(os.environ.get("APEX_WORKSPACE", str(Path.home() / "Apex")))


def sessions_root() -> Path:
    return workspace_root() / "sessions"


def list_sessions() -> list[Path]:
    root = sessions_root()
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())


def create_session(name: str) -> Path:
    name = name.strip()
    if not name or not name.strip(".") or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError(
            f"Session name {name!r} won't work as a folder name — use letters, "
            "numbers, spaces, dashes or underscores."
        )
    path = sessions_root() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def import_lap(src: str | Path, session_name: str) -> Path:
    """Copy a telemetry CSV into a session, never overwriting an existing lap."""
    src = Path(src)
    dest = _unique_dest(create_session(session_name), src.name)
    shutil.copy2(src, dest)
    return dest


def import_telemetry(src: str | Path, session_name: str) -> str:
    """Import any supported telemetry file; returns a human-readable summary.

    Canonical lap CSVs are copied as-is. TORCS run exports (Lin's exporter)
    are split at start-line crossings; each complete lap lands as its own
    canonical CSV, incomplete fragments (grid start, cut-off final lap) are
    skipped.
    """
    from f1coach_core.torcs import is_torcs_export, split_torcs_run, write_canonical_lap

    src = Path(src)
    if not is_torcs_export(src):
        import_lap(src, session_name)
        return f"Imported {src.name}"

    laps = split_torcs_run(src)
    complete = [lap for lap in laps if lap.complete]
    dest_dir = create_session(session_name)
    for lap in complete:
        dest = _unique_dest(dest_dir, f"{src.stem}-lap{lap.lap_label:02d}.csv")
        write_canonical_lap(lap, dest, src.name)
    skipped = len(laps) - len(complete)
    summary = f"Imported {len(complete)} laps from TORCS run {src.name}"
    if skipped:
        summary += f" ({skipped} incomplete fragment{'s' if skipped != 1 else ''} skipped)"
    return summary


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
