"""Evidence-grounded prompt, JSON Schema, extraction, and response validation."""

import json

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

PROMPT_VERSION = "coach-v4"

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

Return ONLY a JSON object, without markdown, with this shape:

{{"findings": [
  {{"focus": "<braking|cornering|throttle>",
    "issue": "<corner and the main opportunity, without measurements>",
    "cause": "<difference directly supported by cited metrics, without measurements>",
    "action": "<one concrete technique to try, without measurements>",
    "confidence": <0.0-1.0>,
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


def build_coach_prompt(evidence_summary: dict) -> str:
    metrics = {name: _METRIC_HELP[name] for _, name in opportunity_catalog(evidence_summary)}
    context = {
        "single_lap": _SINGLE_LAP_CONTEXT,
        "composite": _COMPOSITE_CONTEXT,
    }.get(evidence_summary.get("analysis_mode"), _COMPARISON_CONTEXT)
    return _INSTRUCTIONS.format(
        context=context,
        metric_help=json.dumps(metrics, indent=2),
        summary=json.dumps(_prompt_summary(evidence_summary), indent=2),
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


def build_coach_response_format(evidence_summary: dict) -> dict:
    """Strict OpenAI-compatible JSON Schema with only real citations allowed."""
    citations = list(opportunity_catalog(evidence_summary).values())
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
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "evidence": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": citation_items,
            },
        },
        "required": ["focus", "issue", "cause", "action", "confidence", "evidence"],
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
) -> CoachingReport:
    """Parse model output, stamp provenance, and publish only grounded findings.

    Small local models can repeat a schema-valid claim or duplicate one citation.
    Redundant citations are removed before validation, repeated grounded claims
    are merged, and an invalid finding is isolated so it cannot suppress valid
    siblings. Every finding that survives still passes the complete strict
    contract and exact evidence check.
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
        )

    accepted: list[dict] = []
    claim_indexes: dict[tuple[str, str, str, str], int] = {}
    used_citations: set[tuple[str, str]] = set()
    first_rejection: CoachingSchemaError | None = None
    for raw in findings:
        candidate = _without_repeated_citations(raw, used_citations)
        _ground_model_prose([candidate], evidence_summary)
        try:
            coaching_report_from_dict(
                {
                    "findings": [candidate],
                    "model": model,
                    "prompt_version": PROMPT_VERSION,
                },
                evidence_summary,
                opportunities_only=True,
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


def _ground_model_prose(findings, evidence_summary: dict | None = None) -> None:
    """Render metric-specific guidance before the strict trust-boundary check.

    Some OpenAI-compatible local servers cannot reliably enforce JSON Schema
    string patterns, and a small model may infer vehicle behaviour that is not
    measured. Granite chooses the focus and citation; these templates turn that
    choice into advice that cannot outrun the selected telemetry fact.
    """
    if not isinstance(findings, list):
        return
    guidance = {
        "brake_point": (
            "Initial braking begins earlier than the reference.",
            "The cited brake-onset position is displaced toward the approach.",
            "Move initial brake application progressively toward the cited reference marker.",
        ),
        "peak_brake": (
            "Peak brake pressure differs from the reference.",
            "The cited pressure traces reach different peaks.",
            "Build brake pressure smoothly and compare the peak with the cited reference.",
        ),
        "brake_release": (
            "Brake release timing differs from the reference.",
            "The cited release positions do not align.",
            "Release brake pressure progressively toward the cited reference point.",
        ),
        "entry_speed": (
            "Corner entry speed is lower than the reference.",
            "The cited comparison shows less speed carried into the zone.",
            "Keep the approach repeatable and preserve speed into corner entry.",
        ),
        "min_speed": (
            "Minimum corner speed is lower than the reference.",
            "The cited comparison shows more speed lost through the slowest section.",
            "Release the brake smoothly and preserve momentum through the slowest section.",
        ),
        "min_speed_point": (
            "The slowest point differs from the reference.",
            "The cited minimum-speed positions do not align.",
            "Use a repeatable brake release and aim to align the slowest point with the reference.",
        ),
        "exit_speed": (
            "Corner exit speed is lower than the reference.",
            "The cited comparison shows less speed carried onto the exit.",
            "Prioritise a clean exit and build acceleration progressively.",
        ),
        "throttle_reapply": (
            "Throttle reapplication comes later than the reference.",
            "The cited pickup point is displaced farther along the exit.",
            "Begin squeezing the throttle progressively toward the cited reference point.",
        ),
        "throttle_point": (
            "The half-throttle point comes later than the reference.",
            "The cited comparison shows a delayed throttle build on exit.",
            "Build throttle progressively earlier as the car settles on exit.",
        ),
        "full_throttle": (
            "Full throttle comes later than the reference.",
            "The cited comparison shows a delayed completion of throttle application.",
            "Use a smooth pedal build toward the cited full-throttle point.",
        ),
        "exit_throttle": (
            "Exit throttle is lower than the reference.",
            "The cited comparison shows less throttle carried at the exit sample.",
            "Build pedal input smoothly and prioritise a stable corner exit.",
        ),
        "coast_distance": (
            "The coasting phase is longer than the reference.",
            "The cited comparison shows a larger gap between brake release and throttle pickup.",
            "Reduce the pause with a smooth transition from brake release to throttle pickup.",
        ),
        "brake_applications": (
            "Brake application needs review.",
            "The braking input is split into repeated applications.",
            "Use one progressive brake application and one controlled release.",
        ),
        "throttle_applications": (
            "Throttle application needs review.",
            "The exit input is interrupted by repeated throttle applications.",
            "Build throttle progressively as steering unwinds.",
        ),
        "pedal_overlap": (
            "Pedal transition needs review.",
            "Brake and throttle are applied together through part of the zone.",
            "Separate brake release from throttle pickup with a controlled transition.",
        ),
    }
    if evidence_summary and evidence_summary.get("analysis_mode") == "single_lap":
        guidance["coast_distance"] = (
            "Throttle transition needs review.",
            "The telemetry shows an extended neutral-pedal phase.",
            "Make the brake-release to throttle-pickup transition smooth and deliberate.",
        )
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        evidence = finding.get("evidence")
        if not isinstance(evidence, list):
            continue
        selected = next(
            (
                item
                for item in evidence
                if isinstance(item, dict) and item.get("metric") in guidance
            ),
            None,
        )
        if selected is None:
            continue
        finding["focus"] = EVIDENCE_METRICS[selected["metric"]][3]
        finding["issue"], finding["cause"], finding["action"] = guidance[selected["metric"]]
