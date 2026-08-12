"""IBM Granite 4.1 integration for live TORCS coaching.

The model is deliberately a sidecar: TORCS and the deterministic SCR driver
keep owning the 50 Hz control loop while Granite receives compact telemetry
snapshots asynchronously.
"""

from racecoach.granite.client import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    GraniteAdvice,
    GraniteClient,
    GraniteError,
    GraniteResult,
    MockGraniteClient,
    TelemetrySnapshot,
)
from racecoach.granite.live import LiveGraniteAdvisor

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "GraniteAdvice",
    "GraniteClient",
    "GraniteError",
    "GraniteResult",
    "LiveGraniteAdvisor",
    "MockGraniteClient",
    "TelemetrySnapshot",
]
