"""The bundled sample lap — demos must never depend on the network or credentials.

First run of the app opens this lap; it is also the fixture for tests and the
mock-provider development loop. Regenerate with scripts/make_sample_lap.py.
"""

from importlib.resources import as_file, files

from f1coach_core.lap import Lap
from f1coach_core.loader import load_telemetry_csv


def load_sample_lap() -> Lap:
    resource = files("f1coach_core") / "data" / "sample_lap.csv"
    with as_file(resource) as path:
        return load_telemetry_csv(path)
