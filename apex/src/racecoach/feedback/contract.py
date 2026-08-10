"""Run feedback contract v1 — the five sections the spec fixes:

    1. overall            one-paragraph assessment
    2. highlights         what went well (only claims the metrics support)
    3. issues             worst first, each with citable evidence
    4. code_recommendations   concrete controller-level changes
    5. next_experiment    one thing to try in the next run

Validation is two-layered: shape (types, non-empty) and *traceability* —
every evidence `ref` must name an event_id, a section label, or a lap that
actually exists in the metrics document the model was shown. `model` and
`prompt_version` are stamped by the engine, never trusted from the LLM
(same rule as the lap-coach contract in f1coach_core).
"""

from dataclasses import asdict, dataclass, field

MAX_ISSUES = 3


class FeedbackSchemaError(ValueError):
    """Feedback racecoach refuses to publish. Messages are written to be
    shown to a person (and land in the run's audit record)."""


@dataclass(frozen=True)
class IssueEvidence:
    ref: str  # event_id ("off_track-1"), section label ("T1"), or "lap 2"
    detail: str  # the numbers, verbatim from the metrics
    numbers: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Issue:
    issue: str
    cause: str
    action: str
    evidence: tuple[IssueEvidence, ...]


@dataclass(frozen=True)
class RunFeedback:
    overall: str
    highlights: tuple[str, ...]
    issues: tuple[Issue, ...]  # worst first
    code_recommendations: tuple[str, ...]
    next_experiment: str
    model: str
    prompt_version: str

    def to_dict(self) -> dict:
        return asdict(self)


def valid_refs(metrics: dict) -> set[str]:
    refs = {event["event_id"] for event in metrics.get("events", [])}
    refs |= {section["label"] for section in metrics.get("sections", [])}
    refs |= {f"lap {lap['lap']}" for lap in metrics.get("laps", [])}
    return refs


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FeedbackSchemaError(message)


def _text(data: dict, key: str, where: str) -> str:
    value = data.get(key)
    _require(
        isinstance(value, str) and value.strip(),
        f"{where}.{key} must be a non-empty string",
    )
    return value.strip()


def _string_list(data: dict, key: str, where: str) -> tuple[str, ...]:
    value = data.get(key)
    _require(isinstance(value, list), f"{where}.{key} must be a list of strings")
    for i, item in enumerate(value):
        _require(
            isinstance(item, str) and item.strip(),
            f"{where}.{key}[{i}] must be a non-empty string",
        )
    return tuple(item.strip() for item in value)


def feedback_from_dict(
    data: dict, metrics: dict, *, model: str, prompt_version: str
) -> RunFeedback:
    """Validate raw feedback against the contract AND the metrics it must cite."""
    _require(
        isinstance(data, dict),
        f"feedback must be a JSON object (got {type(data).__name__})",
    )
    overall = _text(data, "overall", "feedback")
    highlights = _string_list(data, "highlights", "feedback")
    next_experiment = _text(data, "next_experiment", "feedback")
    code_recommendations = _string_list(data, "code_recommendations", "feedback")

    raw_issues = data.get("issues")
    _require(isinstance(raw_issues, list), "feedback.issues must be a list")
    _require(
        len(raw_issues) <= MAX_ISSUES,
        f"feedback.issues must hold at most {MAX_ISSUES} issues (got {len(raw_issues)})",
    )
    known = valid_refs(metrics)
    issues = []
    for i, raw in enumerate(raw_issues):
        where = f"issues[{i}]"
        _require(isinstance(raw, dict), f"{where} must be an object")
        raw_evidence = raw.get("evidence")
        _require(
            isinstance(raw_evidence, list) and raw_evidence,
            f"{where}.evidence must be a non-empty list — every issue cites its data",
        )
        evidence = []
        for j, item in enumerate(raw_evidence):
            ev_where = f"{where}.evidence[{j}]"
            _require(isinstance(item, dict), f"{ev_where} must be an object")
            ref = _text(item, "ref", ev_where)
            _require(
                ref in known,
                f"{ev_where}.ref '{ref}' does not exist in the run metrics "
                f"(valid refs are event ids, section labels, or 'lap N')",
            )
            numbers = item.get("numbers", {})
            _require(isinstance(numbers, dict), f"{ev_where}.numbers must be an object")
            evidence.append(
                IssueEvidence(ref=ref, detail=_text(item, "detail", ev_where), numbers=numbers)
            )
        issues.append(
            Issue(
                issue=_text(raw, "issue", where),
                cause=_text(raw, "cause", where),
                action=_text(raw, "action", where),
                evidence=tuple(evidence),
            )
        )

    return RunFeedback(
        overall=overall,
        highlights=highlights,
        issues=tuple(issues),
        code_recommendations=code_recommendations,
        next_experiment=next_experiment,
        model=model,
        prompt_version=prompt_version,
    )
