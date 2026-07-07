"""Shared LLM plumbing: the coaching prompt, JSON extraction, and the parse
path every real provider funnels through.

The prompt is versioned — reports carry `prompt_version` so the reliability
audit can tie a bad finding back to the exact wording that produced it.
"""

import json

from f1coach_core.coach import CoachingReport, CoachingSchemaError, coaching_report_from_dict

PROMPT_VERSION = "coach-v1"

_INSTRUCTIONS = """\
You are an F1 driver coach analysing telemetry. Compare the driver's lap with
the reference lap using ONLY the evidence summary below — every number you
mention must come from it; never invent values.

Evidence summary (distances in metres from the start line, speeds km/h,
times seconds; positive time_lost_s means the driver is slower than the
reference in that corner zone):

{summary}

Reply with ONLY a JSON object, no prose, no markdown fences, in exactly this
shape:

{{"findings": [
  {{"issue": "<one line: corner + time lost>",
    "cause": "<what the driver is doing differently, with numbers>",
    "action": "<one concrete instruction>",
    "confidence": <0.0-1.0, higher when the evidence is stronger>,
    "evidence": [
      {{"metric": "<min_speed|brake_point|throttle_point>",
        "corner": "<corner label from the summary>",
        "value": <driver's number>, "ref": <reference number>,
        "unit": "<km/h|m>",
        "span_m": [<start>, <end>]}}
    ]}}
]}}

Rules:
- At most 3 findings, worst corner (largest time_lost_s) first.
- Every finding needs at least one evidence item; copy span_m exactly from
  that corner's span_m in the summary.
- Skip corners with time_lost_s below 0.05.
- If nothing significant is lost anywhere, return {{"findings": []}}.
"""


def build_coach_prompt(evidence_summary: dict) -> str:
    return _INSTRUCTIONS.format(summary=json.dumps(evidence_summary, indent=2))


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


def report_from_llm_text(text: str, model: str) -> CoachingReport:
    """Model output -> validated CoachingReport. The provider stamps `model`
    and `prompt_version` itself — the LLM is never trusted to echo them."""
    data = extract_json_object(text)
    findings = data.get("findings")
    if findings is None:
        raise CoachingSchemaError("the model's JSON has no 'findings' key")
    return coaching_report_from_dict(
        {"findings": findings, "model": model, "prompt_version": PROMPT_VERSION}
    )
