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
import re
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
from f1coach_core.features import CornerScatter
from f1coach_core.input_device import InputDevice, detect_input_device
from f1coach_core.lap import NO_IDENTITY, Lap, StudyIdentity
from f1coach_core.llm import build_coach_prompt
from f1coach_core.reference import CompositeReference, evidence_for, reference_name
from f1coach_core.workspace import _unique_dest, sessions_root, workspace_root

AUDIT_DIR_NAME = "coaching"

# <date>-<time>[-<microseconds>]-<provider>[-<collision counter>]
_AUDIT_NAME = re.compile(
    r"^(?P<stamp>\d{8}-\d{6}(?:-\d{6})?)-(?P<provider>.+?)(?:-(?P<counter>\d+))?$"
)


def audit_order(path: str | Path) -> tuple[str, int, str]:
    """Chronological sort key for a record filename, collisions included.

    Timestamped names were meant to make lexical order chronological, and for
    a single record per second they do. But `_unique_dest` resolves a collision
    by appending "-2", and "-2.json" sorts BEFORE ".json" -- "-" is 0x2D and "."
    is 0x2E. So two records written in the same second come back in the wrong
    order, and every "newest wins" scan built on a plain sort returns the older
    one. That is the single thing those scans exist to get right, and the
    workspace has real pairs in it: `20260828-105513-mock.json` sits beside
    `20260828-105513-mock-2.json`.

    Records written from now on carry microseconds and will essentially never
    collide, but the counter still has to be read, because the ones already on
    disk do not have microseconds and some of them do collide.

    A name that is not one of ours sorts oldest rather than raising: these
    directories are read to decide what to show a participant, and a stray file
    should cost nothing.
    """
    stem = Path(path).stem
    match = _AUDIT_NAME.match(stem)
    if match is None:
        return ("", 0, stem)
    return (match["stamp"], int(match["counter"] or 1), stem)


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
    scatter: CornerScatter | None = None,
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
        told = prior_advice(lap)
        prompt = build_coach_prompt(summary, scatter, told)
        provider = provider_factory()
        # Set here rather than asked of the factory: the factory belongs to the
        # caller and knows about servers and endpoints, while the device is a
        # property of this lap, which is held here.
        provider.device = device.kind
        # Same reason as the device, and the same route: the spread through a
        # corner is a property of this session, which is held here and nowhere
        # below. Without it the cards would go on using a bar this driver's own
        # lap-to-lap movement clears without meaning anything.
        provider.scatter = scatter
        provider.prior_advice = told
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


PRIOR_ADVICE_LIMIT = 6


def prior_advice(lap: Lap, *, limit: int = PRIOR_ADVICE_LIMIT) -> tuple[str, ...]:
    """What this driver has already been told, on the other laps of this run.

    A session is coached lap by lap, and a participant reads the laps in a row.
    Without this the same instruction arrives on every screen, because the same
    corner really is the biggest difference every time -- the advice is correct
    and reading it five times still teaches nothing the first one did not.

    Only the action is taken: what the driver was asked to do is the thing that
    must not repeat, while an issue and a cause may legitimately be restated
    about the same corner. Newest first and capped, because this rides in the
    prompt of a small local model whose context is the scarce resource here.
    """
    directory = coaching_audit_dir(lap.source)
    if not directory.is_dir():
        return ()
    seen: dict[str, None] = {}
    for path in sorted(directory.glob("*.json"), reverse=True):
        if len(seen) >= limit:
            break
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict) or record.get("ok") is not True:
            continue
        if record.get("lap") == lap.source.stem:  # this lap, not another
            continue
        report = record.get("report")
        findings = report.get("findings") if isinstance(report, dict) else None
        if not isinstance(findings, list):
            continue
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            action = finding.get("action")
            evidence = finding.get("evidence")
            corner = None
            if isinstance(evidence, list) and evidence and isinstance(evidence[0], dict):
                corner = evidence[0].get("corner")
            if isinstance(action, str) and action.strip() and len(seen) < limit:
                where = f"{corner}: " if isinstance(corner, str) and corner else ""
                seen.setdefault(f"{where}{action.strip()}")
    return tuple(seen)


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
    # Microseconds, so `audit_order` almost never has to fall back on the
    # collision counter and a plain listing reads chronologically too.
    dest = _unique_dest(directory, f"{now:%Y%m%d-%H%M%S-%f}-{provider}.json")
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
    for path in sorted(directory.glob("*.json"), key=audit_order, reverse=True):
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
    for path in sorted(directory.glob("*.json"), key=audit_order):
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
