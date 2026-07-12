"""racecoach — post-race driving feedback pipeline for TORCS telemetry.

The CLI face of the project ("CLI, notebooks, and app are three faces of one
package"): import exporter runs into a run store, compute rule-based metrics
and events, and (downstream) turn them into LLM coaching with evidence.

Pure Python, no Qt. Reuses f1coach_core for the canonical lap schema,
coaching contract, LLM providers, and audit trail.
"""

from racecoach.analysis.events import Event, detect_events
from racecoach.analysis.metrics import build_run_metrics
from racecoach.analysis.sections import Section, detect_sections
from racecoach.telemetry.run_store import (
    LoadedRun,
    RunImportError,
    RunMeta,
    import_run,
    list_runs,
    load_run,
    runs_root,
)

__all__ = [
    "Event",
    "LoadedRun",
    "RunImportError",
    "RunMeta",
    "Section",
    "build_run_metrics",
    "detect_events",
    "detect_sections",
    "import_run",
    "list_runs",
    "load_run",
    "runs_root",
]
