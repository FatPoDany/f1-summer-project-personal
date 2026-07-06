"""Application entry point: ``apex [lap.csv]`` or ``python -m apex [lap.csv]``."""

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from apex.main_window import MainWindow
from f1coach_core import TelemetrySchemaError, load_sample_lap, load_telemetry_csv


def create_app(argv: list[str] | None = None) -> QApplication:
    """QApplication with the Apex look: Fusion + dark-first (pit-wall convention)."""
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("Apex")
    app.setOrganizationName("BristolIBMF1")
    app.setStyle("Fusion")  # consistent cross-platform base for the dark scheme
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt >= 6.8
        hints.setColorScheme(Qt.ColorScheme.Dark)
    return app


def main() -> int:
    app = create_app()
    args = app.arguments()[1:]  # Qt's own flags already stripped
    try:
        lap = load_telemetry_csv(args[0]) if args else load_sample_lap()
    except TelemetrySchemaError as exc:
        print(f"apex: {exc}", file=sys.stderr)
        return 2

    window = MainWindow(lap)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
