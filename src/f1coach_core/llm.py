"""Evidence-grounded prompt, JSON Schema, extraction, and response validation."""

import json
from collections.abc import Sequence

from f1coach_core.coach import (
    EVIDENCE_METRICS,
    FOCUS_AREAS,
    MAX_FINDINGS,
    CoachingReport,
    CoachingSchemaError,
    coachable_corners,
    coaching_report_from_dict,
    opportunity_catalog,
)
from f1coach_core.features import CornerScatter
from f1coach_core.guidance import guidance_for

PROMPT_VERSION = "coach-v5"

_METRIC_HELP = {
    "brake_point": "first rising crossing of 20% brake on the approach",
    "peak_brake": "maximum brake pressure in the corner zone",
    "brake_release": "first falling crossing below 10% after braking begins",
    "entry_speed": "speed at the beginning of the corner zone",
    "min_speed": "minimum speed in the corner zone",
    "min_speed_point": "distance of minimum speed",
    "exit_speed": "speed 200 m after the detected apex, clipped to the lap",
    "throttle_reapply": "first rising crossing of 10% throttle after the apex",
    "throttle_point": "first rising crossing of 50% throttle after the apex",
    "full_throttle": "first rising crossing of 95% throttle after the apex",
    "exit_throttle": "throttle at the exit-speed measurement point",
    "coast_distance": "distance between brake release and throttle reapplication",
    "brake_applications": "number of distinct rising brake applications before the apex",
    "throttle_applications": "number of distinct rising throttle applications after the apex",
    "pedal_overlap": "percentage of the corner zone with brake and throttle both applied",
}

_COMPARISON_CONTEXT = """\
Compare the driver's lap with the selected reference lap. The packet contains
at most the six corner zones with meaningful positive time loss. The `ref`
field is the exact value measured on that reference lap."""

_COMPOSITE_CONTEXT = """Compare the driver's lap with a per-corner reference. Each corner's `ref`
values were measured on whichever lap of this session went through that corner
quickest, so different corners may come from different laps. Every `ref` is a
real measurement. The reference is not a lap and has no lap time; do not refer
to one."""

_SINGLE_LAP_CONTEXT = """\
Analyze this single lap without a reference lap. The packet contains only
corner zones that crossed a deterministic technique review threshold. In this
mode, the `ref` field is that code-stamped review threshold, not another lap
and not an optimal target. Do not claim a lap-time loss or improvement."""

_ALREADY_SAID = """
You have already told this driver the following, earlier in this same run:
{advice}

Do not repeat advice they have had. If the corner you would name is one of
those, either say what is different about it this time or choose another.
"""

_INSTRUCTIONS = """\
You are IBM Granite 4.1 acting as a racing driver coach. Use ONLY the
deterministic telemetry evidence below.

{context}

A missing value means the event was not detected; never infer it.

For each finding, choose exactly one focus: braking, cornering, or throttle.
The issue, cause, and action must be concise and actionable. Do not put numeric
values in those prose fields; all numbers belong only in evidence citations.
Every citation must copy metric, corner, value, ref, unit, and span_m exactly;
never invent values. Never invent corners, measurements, causes, vehicle
behaviour, or a racing line.

Metric definitions:
{metric_help}

Evidence packet:
{summary}
{already_said}
Return ONLY a JSON object, without markdown, with this shape:

{{"findings": [
  {{"focus": "<braking|cornering|throttle>",
    "issue": "<corner and the main opportunity, without measurements>",
    "cause": "<difference directly supported by cited metrics, without measurements>",
    "action": "<one concrete technique to try, without measurements>",
    "evidence": [
      {{"metric": "<available metric>", "corner": "<available corner>",
        "value": <exact driver value>, "ref": <exact reference value>,
        "unit": "<exact unit>", "span_m": [<exact start>, <exact end>]}}
    ]}}
]}}

Rules:
- Return at most three findings, in the order supplied by the evidence packet.
- Each finding needs one to four citations and at least one citation matching its focus.
- Use each (corner, metric) citation at most once across the entire response.
- Group citations for the same metric and technique into one finding; never repeat a finding.
- Recommend only differences supported by the cited measurements.
- If the evidence packet has no corners, return {{"findings": []}}.
"""


def _prompt_summary(evidence_summary: dict) -> dict:
    """Compact the full analysis so Granite's small local context stays useful."""
    return {
        "analysis_mode": evidence_summary.get("analysis_mode", "comparison"),
        "lap": evidence_summary.get("lap"),
        "reference": evidence_summary.get("reference"),
        "total_delta_s": evidence_summary.get("total_delta_s"),
        # Only present in a composite comparison, where there is no lap-time
        # delta to give. Dropped rather than sent as null in the other modes.
        **(
            {"corner_delta_s": evidence_summary["corner_delta_s"]}
            if "corner_delta_s" in evidence_summary
            else {}
        ),
        "corners": coachable_corners(evidence_summary),
    }


def build_coach_prompt(
    evidence_summary: dict,
    scatter: CornerScatter | None = None,
    prior_advice: Sequence[str] = (),
) -> str:
    metrics = {
        name: _METRIC_HELP[name]
        for _, name in opportunity_catalog(evidence_summary, scatter)
    }
    context = {
        "single_lap": _SINGLE_LAP_CONTEXT,
        "composite": _COMPOSITE_CONTEXT,
    }.get(evidence_summary.get("analysis_mode"), _COMPARISON_CONTEXT)
    return _INSTRUCTIONS.format(
        context=context,
        metric_help=json.dumps(metrics, indent=2),
        summary=json.dumps(_prompt_summary(evidence_summary), indent=2),
        already_said=(
            _ALREADY_SAID.format(
                advice="\n".join(f"- {line}" for line in prior_advice)
            )
            if prior_advice
            else ""
        ),
    )


def _citation_schema(citation: dict) -> dict:
    properties = {
        key: {"const": citation[key]}
        for key in ("metric", "corner", "value", "ref", "unit", "span_m")
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


def build_coach_response_format(
    evidence_summary: dict, scatter: CornerScatter | None = None
) -> dict:
    """Strict OpenAI-compatible JSON Schema with only real citations allowed."""
    citations = list(opportunity_catalog(evidence_summary, scatter).values())
    citation_items = (
        {"oneOf": [_citation_schema(citation) for citation in citations]}
        if citations
        else {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        }
    )
    prose = {"type": "string", "minLength": 1, "maxLength": 300}
    finding = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "focus": {"type": "string", "enum": list(FOCUS_AREAS)},
            "issue": {**prose, "maxLength": 180},
            "cause": prose,
            "action": prose,
            "evidence": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": citation_items,
            },
        },
        "required": ["focus", "issue", "cause", "action", "evidence"],
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "findings": {
                "type": "array",
                "minItems": 1 if citations else 0,
                "maxItems": MAX_FINDINGS if citations else 0,
                "items": finding,
            }
        },
        "required": ["findings"],
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "apex_lap_coaching",
            "strict": True,
            "schema": schema,
        },
    }


def extract_json_object(text: str) -> dict:
    """Pull the first balanced JSON object out of model output (fences and all)."""
    start = text.find("{")
    if start == -1:
        raise CoachingSchemaError(
            f"the model returned no JSON object; response started: {text[:120]!r}"
        )
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError as exc:
                    raise CoachingSchemaError(
                        f"the model's JSON does not parse ({exc}); "
                        f"it started: {text[start : start + 120]!r}"
                    ) from exc
    raise CoachingSchemaError(
        f"the model's JSON object never closes; response started: {text[start : start + 120]!r}"
    )


def report_from_llm_text(
    text: str,
    model: str,
    evidence_summary: dict | None = None,
    *,
    device: str = "",
    scatter: CornerScatter | None = None,
) -> CoachingReport:
    """Parse model output, stamp provenance, and publish only grounded findings.

    Small local models can repeat a schema-valid claim or duplicate one citation.
    Redundant citations are removed before validation, repeated grounded claims
    are merged, and an invalid finding is isolated so it cannot suppress valid
    siblings. Every finding that survives still passes the complete strict
    contract and exact evidence check.

    The templates run before the check and not as a fallback behind it, so no
    sentence a participant reads is the model's. Letting the model keep words
    that passed the contract was tried and measured against the real weights:
    across five laps its prose reached a card zero times out of eight, and the
    one finding that would have gone up verbatim had the sign backwards --
    1775 m of brake point against a 1795 m reference is braking earlier, it
    called that later, and it advised braking earlier still. A contract that
    checks whether a number is cited does not check whether the sentence around
    it is true. See docs/AI_FEEDBACK_IMPROVEMENTS.md 15.6.
    """
    data = extract_json_object(text)
    findings = data.get("findings")
    if findings is None:
        raise CoachingSchemaError("the model's JSON has no 'findings' key")
    if not isinstance(findings, list) or not findings:
        return coaching_report_from_dict(
            {"findings": findings, "model": model, "prompt_version": PROMPT_VERSION},
            evidence_summary,
            opportunities_only=True,
            scatter=scatter,
        )

    accepted: list[dict] = []
    claim_indexes: dict[tuple[str, str, str, str], int] = {}
    used_citations: set[tuple[str, str]] = set()
    first_rejection: CoachingSchemaError | None = None
    for raw in findings:
        candidate = _without_repeated_citations(raw, used_citations)
        _ground_model_prose([candidate], evidence_summary, device)
        try:
            coaching_report_from_dict(
                {
                    "findings": [candidate],
                    "model": model,
                    "prompt_version": PROMPT_VERSION,
                },
                evidence_summary,
                opportunities_only=True,
                scatter=scatter,
            )
        except CoachingSchemaError as exc:
            if first_rejection is None:
                first_rejection = exc
            continue

        claim = (
            candidate["focus"],
            candidate["issue"],
            candidate["cause"],
            candidate["action"],
        )
        if claim in claim_indexes:
            existing = accepted[claim_indexes[claim]]["evidence"]
            available_slots = 4 - len(existing)
            additions = candidate["evidence"][:available_slots]
            existing.extend(additions)
            used_citations.update(_citation_key(item) for item in additions)
            continue

        if len(accepted) >= MAX_FINDINGS:
            continue
        claim_indexes[claim] = len(accepted)
        accepted.append(candidate)
        used_citations.update(_citation_key(item) for item in candidate["evidence"])

    if not accepted and first_rejection is not None:
        raise first_rejection
    return coaching_report_from_dict(
        {"findings": accepted, "model": model, "prompt_version": PROMPT_VERSION},
        evidence_summary,
        opportunities_only=True,
        scatter=scatter,
    )


def _citation_key(item: dict) -> tuple[str, str]:
    return item["corner"], item["metric"]


def _without_repeated_citations(
    raw: object,
    used_citations: set[tuple[str, str]],
) -> object:
    """Copy one finding while removing only identifiable repeated citations."""
    if not isinstance(raw, dict) or not isinstance(raw.get("evidence"), list):
        return raw
    candidate = dict(raw)
    evidence = []
    local: set[tuple[str, str]] = set()
    for item in raw["evidence"]:
        if not isinstance(item, dict):
            evidence.append(item)
            continue
        corner, metric = item.get("corner"), item.get("metric")
        if not isinstance(corner, str) or not isinstance(metric, str):
            evidence.append(item)
            continue
        citation = (corner, metric)
        if citation in local or citation in used_citations:
            continue
        local.add(citation)
        evidence.append(item)
    candidate["evidence"] = evidence
    return candidate


def _ground_model_prose(
    findings, evidence_summary: dict | None = None, device: str = ""
) -> None:
    """Render metric-specific guidance before the strict trust-boundary check.

    Some OpenAI-compatible local servers cannot reliably enforce JSON Schema
    string patterns, and a small model may infer vehicle behaviour that is not
    measured. Granite chooses the focus and citation; these templates turn that
    choice into advice that cannot outrun the selected telemetry fact.

    The sentences themselves live in ``guidance``, shared with ``MockCoach`` so
    one metric cannot mean two different things depending on which provider
    answered. ``device`` picks the phrasing: advice about pedal pressure is not
    advice at all to somebody holding an arrow key.
    """
    if not isinstance(findings, list):
        return
    single_lap = bool(
        evidence_summary and evidence_summary.get("analysis_mode") == "single_lap"
    )
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        evidence = finding.get("evidence")
        if not isinstance(evidence, list):
            continue
        selected = None
        chosen = None
        for item in evidence:
            if not isinstance(item, dict):
                continue
            found = guidance_for(
                str(item.get("metric", "")), device=device, single_lap=single_lap
            )
            if found is not None:
                selected, chosen = item, found
                break
        if selected is None or chosen is None:
            continue
        finding["focus"] = EVIDENCE_METRICS[selected["metric"]][3]
        finding["issue"] = chosen.issue
        finding["cause"] = chosen.cause
        finding["action"] = chosen.action
        # A model asked how sure it is will answer, and the answer measures
        # nothing. Dropped here rather than ignored downstream, so it never
        # reaches an audit record and cannot be read back later as though
        # something had computed it.
        finding.pop("confidence", None)
