"""Coaching audit trail — one JSON file per coaching run, success or failure.

Every run of every provider (mock included) leaves a self-contained record:
what the model was asked (evidence summary + exact prompt), what it answered
(raw streamed text), and what Apex did with it (the validated report, or the
readable error it refused with). Records land next to the lap's session
(…/<session>/coaching/) so the audit travels with the data; laps opened from
outside the workspace fall back to <workspace>/coaching/.

Each record also carries the lap's study identity, because the trail doubles as
the record of what advice a participant was given: a comparison of what the
coached arm was told against how they then drove has to start from whose run it
was, and a lap file name does not say.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from f1coach_core.coach import (
    CoachingReport,
    CoachingSchemaError,
    CoachProvider,
    coaching_report_from_dict,
)
from f1coach_core.input_device import InputDevice, detect_input_device
from f1coach_core.lap import NO_IDENTITY, Lap, StudyIdentity
from f1coach_core.llm import build_coach_prompt
from f1coach_core.reference import CompositeReference, evidence_for, reference_name
from f1coach_core.workspace import _unique_dest, sessions_root, workspace_root

AUDIT_DIR_NAME = "coaching"


@dataclass(frozen=True)
class SavedCoachingReport:
    """A previously published report and the audit record that proves it."""

    report: CoachingReport
    path: Path


@dataclass(frozen=True)
class AuditedCoachingAttempt:
    """The publishable outcome and audit outcome of one requested analysis."""

    report: CoachingReport | None
    error: str | None
    audit_path: Path | None
    audit_error: str | None = None


def run_audited_coaching(
    lap: Lap,
    reference: Lap | CompositeReference | None,
    *,
    provider_name: str,
    provider_factory: Callable[[], CoachProvider],
    on_progress: Callable[[str], None] | None = None,
) -> AuditedCoachingAttempt:
    """Build, validate, and audit one report through a shared trusted path.

    Provider construction is inside the attempt so a model-server start failure
    is recorded against the lap just like a request or validation failure.
    """
    raw_text = ""
    device = detect_input_device(lap)

    def progress(text: str) -> None:
        nonlocal raw_text
        raw_text = text
        if on_progress is not None:
            on_progress(text)

    summary: dict | None = None
    prompt: str | None = None
    report: CoachingReport | None = None
    error: str | None = None
    try:
        summary = evidence_for(lap, reference)
        prompt = build_coach_prompt(summary)
        provider = provider_factory()
        # Set here rather than asked of the factory: the factory belongs to the
        # caller and knows about servers and endpoints, while the device is a
        # property of this lap, which is held here.
        provider.device = device.kind
        report = provider.generate(summary, on_progress=progress)
    except Exception as exc:  # provider and imported telemetry are trust boundaries
        error = str(exc)

    audit_path: Path | None = None
    audit_error: str | None = None
    try:
        audit_path = write_coaching_audit(
            lap_source=lap.source,
            provider=provider_name,
            lap_name=lap.source.stem,
            identity=lap.identity,
            reference_name=reference_name(reference),
            evidence_summary=summary,
            prompt=prompt,
            raw_response=raw_text,
            report=report,
            error=error,
            input_device=device,
        )
    except OSError as exc:
        audit_error = str(exc)
    return AuditedCoachingAttempt(
        report=report,
        error=error,
        audit_path=audit_path,
        audit_error=audit_error,
    )


def coaching_audit_dir(lap_source: str | Path | None) -> Path:
    """Where a run's audit record belongs, given the lap it coached."""
    if lap_source is not None:
        parent = Path(lap_source).parent
        if parent.is_dir() and parent.is_relative_to(sessions_root()):
            return parent / AUDIT_DIR_NAME
    return workspace_root() / AUDIT_DIR_NAME


def write_coaching_audit(
    *,
    lap_source: str | Path | None,
    provider: str,
    lap_name: str,
    reference_name: str | None,
    identity: StudyIdentity = NO_IDENTITY,
    evidence_summary: dict | None,
    prompt: str | None,
    raw_response: str,
    report: CoachingReport | None,
    error: str | None = None,
    input_device: InputDevice | None = None,
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
        # Who this run coached, not just which file. The audit trail is also the
        # only record of what advice a participant was actually given, and a
        # file name is not a participant: laps are renamed on import, pooled
        # across machines, and read months later by somebody who was not there.
        "driver": identity.driver,
        "phase": identity.phase,
        "setup": identity.setup,
        # What they were driving with, measured off their own steering rather
        # than assumed. Nothing else records it -- the capture manifest fixes
        # the track, the car and the lap count and says nothing about the
        # controls -- and it decides whether the advice above was executable on
        # the hardware in front of them. Kept outside `evidence_summary` on
        # purpose: that dict is this file's match key, so a new field in it
        # would orphan every report already generated.
        "input_device": None if input_device is None else input_device.kind,
        "input_device_evidence": (
            None
            if input_device is None
            else {
                "steady_rate_share": input_device.steady_rate_share,
                "moving_samples": input_device.moving_samples,
            }
        ),
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


def latest_coaching_report(
    lap: Lap,
    reference: Lap | CompositeReference | None,
    *,
    provider: str,
) -> SavedCoachingReport | None:
    """Load the newest successful report for exactly this analysis context.

    Audit files are untrusted input. A stored report is publishable only when
    its provider, lap, optional reference, trusted metadata, and frozen
    evidence packet all still match. Its citations then pass the current
    evidence validator again before the caller may render it.
    """
    directory = coaching_audit_dir(lap.source)
    if not directory.is_dir():
        return None
    summary = evidence_for(lap, reference)
    against = reference_name(reference)
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        report_data = record.get("report")
        if not (
            record.get("ok") is True
            and record.get("error") is None
            and record.get("provider") == provider
            and record.get("lap") == lap.source.stem
            and record.get("reference") == against
            and record.get("evidence_summary") == summary
            and isinstance(report_data, dict)
            and record.get("model") == report_data.get("model")
            and record.get("prompt_version") == report_data.get("prompt_version")
        ):
            continue
        try:
            report = coaching_report_from_dict(
                report_data,
                summary,
                opportunities_only=provider != "mock",
            )
        except CoachingSchemaError:
            continue
        if not _report_has_unique_claims_and_citations(report):
            continue
        return SavedCoachingReport(report=report, path=path)
    return None


def _report_has_unique_claims_and_citations(report: CoachingReport) -> bool:
    """Keep obsolete audits from reintroducing cards current publication rejects."""
    claims: set[tuple[str, str, str, str]] = set()
    citations: set[tuple[str, str]] = set()
    for finding in report.findings:
        claim = (finding.focus, finding.issue, finding.cause, finding.action)
        finding_citations = {(item.corner, item.metric) for item in finding.evidence}
        if claim in claims or citations.intersection(finding_citations):
            return False
        claims.add(claim)
        citations.update(finding_citations)
    return True


def latest_coaching_outcomes(session_path: str | Path) -> dict[tuple[str, str | None], int]:
    """(lap, reference) -> findings count, from that pair's newest successful audit.

    Drives the Garage's "ANALYSED · n findings" status. The count belongs to the
    pair rather than to the lap: the same lap read against two references is two
    different questions with two different answers, and keying on the lap alone
    published whichever comparison happened to run last — a number the
    participant could not reconcile with the one Lap Analysis showed them.

    A single-lap analysis is stored under a ``None`` reference. Timestamped
    filenames make lexical order chronological, so later records win; unreadable
    or failed records are skipped rather than surfaced — this is a status hint,
    not the audit trail itself."""
    directory = Path(session_path) / AUDIT_DIR_NAME
    if not directory.is_dir():
        return {}
    outcomes: dict[tuple[str, str | None], int] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        report = record.get("report")
        if record.get("ok") and isinstance(report, dict) and record.get("lap"):
            reference = record.get("reference")
            key = (str(record["lap"]), str(reference) if reference else None)
            outcomes[key] = len(report.get("findings") or [])
    return outcomes
