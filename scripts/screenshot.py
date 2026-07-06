"""Render the Garage and Lap Analysis screens to PNGs — headless-safe.

Feeds the weekly blog post and IBM status forms with real pixels; CI uploads
them per OS as build artifacts.

Usage: [QT_QPA_PLATFORM=offscreen] python scripts/screenshot.py [out_dir]
"""

import sys
from pathlib import Path

from apex.app import create_app
from apex.main_window import MainWindow
from f1coach_core import ensure_sample_session, load_sample_session


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    out_dir.mkdir(parents=True, exist_ok=True)
    app = create_app([sys.argv[0]])
    ensure_sample_session()

    window = MainWindow()
    window.resize(1280, 720)
    window.show()

    def settle() -> None:
        for _ in range(6):
            app.processEvents()

    ok = True
    settle()
    ok &= window.grab().save(str(out_dir / "apex-garage.png"))

    session = load_sample_session()
    best = session.best_lap
    assert best is not None
    window.show_analysis(best, session)
    settle()
    ok &= window.grab().save(str(out_dir / "apex-analysis.png"))

    print(f"{'wrote' if ok else 'FAILED to write'} apex-garage.png, apex-analysis.png in {out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
