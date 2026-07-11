"""Coaching audit trail — one JSON file per coaching run, success or failure.

Every run of every provider (mock included) leaves a self-contained record:
what the model was asked (evidence summary + exact prompt), what it answered
(raw streamed text), and what Apex did with it (the validated report, or the
readable error it refused with). Records land next to the lap's session
(…/<session>/coaching/) so the audit travels with the data; laps opened from
outside the workspace fall back to <workspace>/coaching/.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from f1coach_core.coach import CoachingReport
from f1coach_core.workspace import _unique_dest, sessions_root, workspace_root

AUDIT_DIR_NAME = "coaching"


def coaching_audit_dir(lap_source: str | Path | None) -> Path:
    """Where a run's audit record belongs, given the lap it coached."""
    if lap_source is not None:
        parent = Path(lap_source).parent
        if parent.is_relative_to(sessions_root()):
            return parent / AUDIT_DIR_NAME
    return workspace_root() / AUDIT_DIR_NAME


def write_coaching_audit(
    *,
    lap_source: str | Path | None,
    provider: str,
    lap_name: str,
    reference_name: str,
    evidence_summary: dict | None,
    prompt: str | None,
    raw_response: str,
    report: CoachingReport | None,
    error: str | None = None,
) -> Path:
    """Write one audit record; returns its path. Raises OSError on write
    failure — the caller decides whether auditing may break the run it audits."""
    now = datetime.now(UTC)
    record = {
        "written_at": now.isoformat(timespec="seconds"),
        "provider": provider,
        "ok": error is None,
        "error": error,
        "lap": lap_name,
        "reference": reference_name,
        "model": report.model if report else None,
        "prompt_version": report.prompt_version if report else None,
        "evidence_summary": evidence_summary,
        "prompt": prompt,
        "raw_response": raw_response,
        "report": report.to_dict() if report else None,
    }
    directory = coaching_audit_dir(lap_source)
    directory.mkdir(parents=True, exist_ok=True)
    dest = _unique_dest(directory, f"{now:%Y%m%d-%H%M%S}-{provider}.json")
    dest.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return dest
