"""Application entry point: ``apex [telemetry.csv]`` or ``python -m apex``.

With no argument the Garage opens on the bundled sample session (materialised
into the workspace on first run). A canonical lap CSV argument opens straight
in Lap Analysis; a TORCS run export is split into a session.
"""

import os
import sys
import traceback

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from apex import theme
from apex.main_window import MainWindow
from f1coach_core import ensure_sample_session


def _dark_palette() -> QPalette:
    """Explicit Carbon-gray palette: platforms without a dark theme (offscreen,
    bare Linux) would otherwise ignore the colour-scheme hint."""
    text = QColor("#f4f4f4")
    surface = QColor("#262626")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(theme.BG))
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, QColor("#1f1f1f"))
    palette.setColor(QPalette.ColorRole.AlternateBase, surface)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, surface)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.ToolTipBase, surface)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#0f62fe"))  # Carbon blue-60
    palette.setColor(QPalette.ColorRole.HighlightedText, text)
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(theme.TEXT_DIM))
    disabled = QColor("#6f6f6f")
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return palette


def create_app(argv: list[str] | None = None) -> QApplication:
    """QApplication with the Apex look: Fusion + dark-first (pit-wall convention)."""
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("Apex")
    app.setOrganizationName("BristolIBMF1")
    app.setStyle("Fusion")  # consistent cross-platform base for the dark scheme
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt >= 6.8
        hints.setColorScheme(Qt.ColorScheme.Dark)
    app.setPalette(_dark_palette())
    return app


def show_crash_dialog(exc_type, exc, tb) -> None:
    """Last-resort excepthook. PySide6 routes unhandled slot exceptions here and
    keeps the event loop running — but a packaged .app has no visible stderr, so
    without a dialog a stray bug ends the demo with nothing on screen."""
    sys.__excepthook__(exc_type, exc, tb)  # keep the full trace in any terminal/log
    if QApplication.instance() is None:
        return
    try:
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("Apex — unexpected error")
        box.setText("Apex hit an unexpected error and will try to carry on.")
        box.setInformativeText(str(exc))
        box.setDetailedText("".join(traceback.format_exception(exc_type, exc, tb)))
        box.exec()
    except Exception:  # the hook itself must never raise
        pass


def main() -> int:
    app = create_app()
    sys.excepthook = show_crash_dialog
    sample_problem: str | None = None
    try:
        ensure_sample_session()
    except OSError as exc:  # the app works without the sample; say so and carry on
        sample_problem = f"Sample session unavailable — {exc}"

    window = MainWindow()
    window.show()
    if sample_problem:
        window.statusBar().showMessage(sample_problem)
    args = app.arguments()[1:]  # Qt's own flags already stripped
    if args:
        window.open_path(args[0])
    if os.environ.get("APEX_SMOKE_TEST"):  # packaged-build check: paint, then exit 0
        QTimer.singleShot(1500, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
