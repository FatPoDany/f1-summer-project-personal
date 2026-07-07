# PyInstaller build recipe.
#
#   pip install -e ".[package]"
#   pyinstaller apex.spec
#
# macOS -> dist/Apex.app (unsigned; right-click > Open on first launch)
# Linux -> dist/Apex/    (wrap with appimagetool for the AppImage)
#
# The watsonx SDK is excluded to keep the bundle lean — packaged builds are
# the offline demo device (mock + local Ollama). Run from source for watsonx,
# or delete the exclude below and rebuild.

import sys

a = Analysis(
    ["scripts/pyinstaller_entry.py"],
    pathex=["src"],
    datas=[("src/f1coach_core/data", "f1coach_core/data")],
    hiddenimports=[],
    excludes=["ibm_watsonx_ai", "IPython", "matplotlib", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="Apex",
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Apex")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Apex.app",
        icon=None,
        bundle_identifier="uk.ac.bristol.ibm.apex",
    )
