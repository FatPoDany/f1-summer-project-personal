"""f1coach-core — pure-Python telemetry loading, features, analysis, coaching (lanes M2-M5).

No Qt in here, ever: the desktop app, CLI, and notebooks all import this package
and render what it returns.
"""

from f1coach_core.lap import Lap
from f1coach_core.loader import TelemetrySchemaError, load_telemetry_csv
from f1coach_core.sample import load_sample_lap

__all__ = ["Lap", "TelemetrySchemaError", "load_sample_lap", "load_telemetry_csv"]
