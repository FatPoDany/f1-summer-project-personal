# PyInstaller build recipe.
#
#   pip install -e ".[package]"
#   pyinstaller apex.spec
#
# macOS   -> dist/Apex.app (unsigned; right-click > Open on first launch)
# Linux   -> dist/Apex/    (wrap with appimagetool for the AppImage)
# Windows -> dist/Apex/Apex.exe
#
# The study installer is built from the Windows output by
# integrations/torcs-1.3.9/build-windows.ps1, which copies the patched simulator
# to dist/Apex/torcs-runtime/ — the packaged location default_torcs_binary()
# resolves from sys.executable's own directory.
#
# The watsonx SDK is excluded to keep the bundle lean — packaged builds are
# the offline demo device (mock + local Ollama). Run from source for watsonx,
# or delete the exclude below and rebuild.

import subprocess
import sys
from pathlib import Path


def _build_id() -> Path:
    """Stamp the commit being packaged into the bundle, and return the file.

    A frozen build has no repository under it, so it cannot work out later what
    it was built from. The exposure log records this against every view a
    participant reads, because the coaching *is* the study's intervention and it
    changed while collection was running -- without a stamp, participants who
    read two different versions of it pool into one condition that never
    existed.

    Written into build/ rather than into the source tree: a packaging step
    should not leave the working copy dirty, least of all with a file whose
    whole job is to say whether the working copy was dirty.
    """
    def git(*args: str) -> str:
        try:
            done = subprocess.run(
                ["git", *args], capture_output=True, text=True, timeout=10, check=False
            )
            return done.stdout.strip() if done.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    commit = git("rev-parse", "--short=7", "HEAD")
    if commit and git("status", "--porcelain"):
        commit += "+"  # built from a tree with uncommitted changes in it
    out = Path("build") / "build_id.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(commit, encoding="utf-8")
    print(f"apex.spec: build id {commit or '(unknown)'}")
    return out


_COMMON = dict(
    pathex=["src"],
    datas=[
        ("src/f1coach_core/data", "f1coach_core/data"),
        (str(_build_id()), "f1coach_core/data"),
    ],
    hiddenimports=[],
    excludes=["ibm_watsonx_ai", "IPython", "matplotlib", "tkinter"],
)

a = Analysis(["scripts/pyinstaller_entry.py"], **_COMMON)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="Apex",
    console=False,
)

# The CLI ships beside the app. Every documented racecoach workflow -- and in
# particular recovering a capture that a crashed simulator left unregistered --
# is otherwise unreachable on a machine that only ran the installer. console=True
# because it is a terminal program and needs somewhere to print.
cli = Analysis(["scripts/pyinstaller_racecoach.py"], **_COMMON)
cli_pyz = PYZ(cli.pure)
cli_exe = EXE(
    cli_pyz,
    cli.scripts,
    exclude_binaries=True,
    name="racecoach",
    console=True,
)

coll = COLLECT(exe, a.binaries, a.datas, cli_exe, cli.binaries, cli.datas, name="Apex")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Apex.app",
        icon=None,
        bundle_identifier="uk.ac.bristol.ibm.apex",
    )
