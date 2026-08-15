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

PROMPT_VERSION = "coach-v2"

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
}

_INSTRUCTIONS = """\
You are IBM Granite 4.1 acting as a racing driver coach. Compare the driver's
lap with the reference using ONLY the deterministic telemetry evidence below.
The packet contains at most the six corner zones with meaningful positive time
loss. A missing value means the event was not detected; never infer it.

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
- Return at most three findings, ordered by largest time_lost_s first.
- Each finding needs one to four citations and at least one citation matching its focus.
- Recommend only differences supported by the cited measurements.
- If the evidence packet has no corners, return {{"findings": []}}.
"""


def _prompt_summary(evidence_summary: dict) -> dict:
    """Compact the full analysis so Granite's small local context stays useful."""
    return {
        "lap": evidence_summary.get("lap"),
        "reference": evidence_summary.get("reference"),
        "total_delta_s": evidence_summary.get("total_delta_s"),
        "corners": coachable_corners(evidence_summary),
    }


def build_coach_prompt(evidence_summary: dict) -> str:
    metrics = {name: _METRIC_HELP[name] for _, name in opportunity_catalog(evidence_summary)}
    return _INSTRUCTIONS.format(
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
    """Parse model output, stamp provenance, and exact-match cited telemetry."""
    data = extract_json_object(text)
    findings = data.get("findings")
    if findings is None:
        raise CoachingSchemaError("the model's JSON has no 'findings' key")
    _ground_model_prose(findings)
    return coaching_report_from_dict(
        {"findings": findings, "model": model, "prompt_version": PROMPT_VERSION},
        evidence_summary,
        opportunities_only=True,
    )


def _ground_model_prose(findings) -> None:
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
    }
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
