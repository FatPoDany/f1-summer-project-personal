"""f1coach-core — pure-Python telemetry loading, features, analysis, coaching.

No Qt in here, ever: the desktop app, CLI, and notebooks all import this package
and render what it returns.
"""

from f1coach_core.analysis import sector_spans, sector_times
from f1coach_core.audit import (
    SavedCoachingReport,
    coaching_audit_dir,
    latest_coaching_outcomes,
    latest_coaching_report,
    write_coaching_audit,
)
from f1coach_core.coach import (
    CoachingReport,
    CoachingSchemaError,
    CoachProvider,
    Evidence,
    Finding,
    MockCoach,
    available_providers,
    coaching_report_from_dict,
    get_provider,
)
from f1coach_core.debrief import DebriefPoint, debrief_summary, lap_debrief
from f1coach_core.features import (
    build_evidence_summary,
    corner_table,
    detect_corners,
    single_lap_corner_table,
    time_delta,
)
from f1coach_core.granite_coach import GraniteCoach, GraniteCoachError
from f1coach_core.lap import Lap, StudyIdentity
from f1coach_core.llm import build_coach_prompt
from f1coach_core.loader import TelemetrySchemaError, load_telemetry_csv
from f1coach_core.report import render_html_report
from f1coach_core.sample import load_sample_lap, load_sample_session
from f1coach_core.session import Session, load_session
from f1coach_core.torcs import is_torcs_export, split_torcs_run
from f1coach_core.workspace import (
    SAMPLE_SESSION_NAME,
    create_session,
    delete_session,
    ensure_sample_session,
    import_lap,
    import_telemetry,
    list_sessions,
    sessions_root,
    workspace_root,
)

__all__ = [
    "SAMPLE_SESSION_NAME",
    "SavedCoachingReport",
    "CoachProvider",
    "CoachingReport",
    "CoachingSchemaError",
    "Evidence",
    "Finding",
    "GraniteCoach",
    "GraniteCoachError",
    "DebriefPoint",
    "Lap",
    "StudyIdentity",
    "debrief_summary",
    "lap_debrief",
    "MockCoach",
    "Session",
    "TelemetrySchemaError",
    "available_providers",
    "build_coach_prompt",
    "build_evidence_summary",
    "coaching_audit_dir",
    "coaching_report_from_dict",
    "corner_table",
    "create_session",
    "detect_corners",
    "delete_session",
    "ensure_sample_session",
    "get_provider",
    "import_lap",
    "import_telemetry",
    "is_torcs_export",
    "latest_coaching_outcomes",
    "latest_coaching_report",
    "list_sessions",
    "load_sample_lap",
    "load_sample_session",
    "load_session",
    "load_telemetry_csv",
    "render_html_report",
    "sector_spans",
    "sector_times",
    "single_lap_corner_table",
    "sessions_root",
    "split_torcs_run",
    "time_delta",
    "workspace_root",
    "write_coaching_audit",
]
