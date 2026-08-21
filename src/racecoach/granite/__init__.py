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
from racecoach.granite.model import ModelError, available, ensure_model, model_path
from racecoach.granite.narrate import (
    NarratedDebrief,
    NarrationError,
    narrate_debrief,
)
from racecoach.granite.report import SessionReport, build_report, render_markdown, session_laps
from racecoach.granite.server import GraniteServer, ServerError

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "GraniteAdvice",
    "GraniteClient",
    "GraniteError",
    "GraniteResult",
    "GraniteServer",
    "ModelError",
    "NarratedDebrief",
    "NarrationError",
    "SessionReport",
    "build_report",
    "narrate_debrief",
    "render_markdown",
    "session_laps",
    "ServerError",
    "available",
    "ensure_model",
    "model_path",
    "LiveGraniteAdvisor",
    "MockGraniteClient",
    "TelemetrySnapshot",
]
