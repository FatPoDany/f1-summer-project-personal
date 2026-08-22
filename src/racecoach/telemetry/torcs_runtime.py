"""Locate a patched TORCS runtime on the platform Apex is running on.

The two supported builds lay their files out differently, and both capture
paths need the same answers, so the layout rules live here rather than being
duplicated per recorder.

* The autotools build installs ``bin/torcs`` and its data under
  ``share/games/torcs/`` beneath one prefix.
* The Visual Studio build puts ``wtorcs.exe`` and its data in a single
  directory, because ``src/windows/main.cpp`` derives both ``DataDir`` and the
  fallback ``LocalDir`` from the executable's own folder.
"""

import os
import sys
from collections.abc import Mapping
from pathlib import Path

WINDOWS = os.name == "nt"
TORCS_EXECUTABLE_NAME = "wtorcs.exe" if WINDOWS else "torcs"
PACKAGED_RUNTIME_DIR = "torcs-runtime"


def graphical_session_issue(environ: Mapping[str, str] | None = None) -> str | None:
    """Explain why participant driving cannot start in this desktop session.

    The legacy TORCS renderer needs a local Windows OpenGL/input session.  RDP
    is unsuitable even when it happens to expose a software renderer: its input
    latency invalidates a participant measurement, and some hosts cannot create
    the required context at all.  Windows publishes the session kind through
    ``SESSIONNAME`` (normally ``RDP-Tcp#...`` or ``rdp-sxs...``).

    Return a participant-facing sentence rather than a boolean so every caller
    gives the same actionable recovery instruction.
    """
    if not WINDOWS:
        return None
    environment = os.environ if environ is None else environ
    session_name = (environment.get("SESSIONNAME") or "").strip().casefold()
    if not session_name.startswith("rdp"):
        return None
    return (
        "TORCS driving cannot run reliably through Remote Desktop. Sign in at "
        "this Windows PC locally, then reopen Apex and collect the session there."
    )


def torcs_executable(runtime_root: str | Path) -> Path:
    """The simulator executable inside an installed runtime root."""
    root = Path(runtime_root)
    return root / TORCS_EXECUTABLE_NAME if WINDOWS else root / "bin" / TORCS_EXECUTABLE_NAME


def torcs_runtime_root(torcs_binary: str | Path) -> Path:
    """The runtime root that owns ``torcs_binary``."""
    binary = Path(torcs_binary).resolve()
    return binary.parent if WINDOWS else binary.parent.parent


def torcs_data_root(torcs_binary: str | Path) -> Path:
    """The directory TORCS treats as ``DataDir`` for this executable."""
    root = torcs_runtime_root(torcs_binary)
    return root if WINDOWS else root / "share" / "games" / "torcs"


def torcs_raceman_dir(torcs_binary: str | Path) -> Path:
    """Where installed race-manager presets live for this executable."""
    return torcs_data_root(torcs_binary) / "config" / "raceman"


def torcs_launch_cwd(torcs_binary: str | Path) -> Path:
    """The working directory TORCS must be started from.

    The Visual Studio build resolves some paths against the current directory
    rather than ``DataDir``: ``prepareLocalDir`` in ``src/windows/main.cpp`` lists
    the race managers to seed a profile with as the relative path
    ``config/raceman``, and the same file's usage message says outright to run
    ``wtorcs.exe`` "from the directory which contains wtorcs.exe". Launched from
    anywhere else, a fresh profile directory silently receives no race managers.

    The autotools build does not care -- its launcher script exports absolute
    directories -- but the data root is a correct working directory there too, so
    both platforms get one rule.
    """
    return torcs_data_root(torcs_binary)


def default_torcs_binary() -> Path:
    """Find a packaged simulator first, then the developer runtime."""
    configured_prefix = os.environ.get("TORCS_PREFIX")
    if configured_prefix:
        return torcs_executable(configured_prefix)

    packaged_roots = []
    pyinstaller_root = getattr(sys, "_MEIPASS", None)
    if pyinstaller_root:
        packaged_roots.append(Path(pyinstaller_root))
    packaged_roots.append(Path(sys.executable).resolve().parent)
    for root in packaged_roots:
        candidate = torcs_executable(root / PACKAGED_RUNTIME_DIR)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate

    if WINDOWS:
        # There is no user-writable /tmp build convention on Windows: the study
        # installer always ships the runtime beside the Apex executable, so
        # report that path rather than inventing one under the user profile.
        return torcs_executable(packaged_roots[-1] / PACKAGED_RUNTIME_DIR)

    uid = os.getuid() if hasattr(os, "getuid") else 0
    return torcs_executable(Path(f"/tmp/apex-torcs-{uid}/{PACKAGED_RUNTIME_DIR}"))
