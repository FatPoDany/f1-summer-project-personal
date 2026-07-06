"""Render the main window (bundled sample lap) to a PNG — headless-safe.

Feeds the weekly blog post and IBM status forms with real pixels; CI uploads
one per OS as a build artifact.

Usage: [QT_QPA_PLATFORM=offscreen] python scripts/screenshot.py [out.png]
"""

import sys
from pathlib import Path

from apex.app import create_app
from apex.main_window import MainWindow
from f1coach_core import load_sample_lap


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("screenshot.png")
    app = create_app([sys.argv[0]])
    window = MainWindow(load_sample_lap())
    window.resize(1280, 720)
    window.show()
    for _ in range(5):  # let layout + first paint settle
        app.processEvents()
    ok = window.grab().save(str(out))
    print(f"{'wrote' if ok else 'FAILED to write'} {out} ({window.width()}x{window.height()})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
