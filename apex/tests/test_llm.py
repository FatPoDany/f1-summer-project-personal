"""Shared LLM plumbing: prompt content, JSON extraction, response parsing."""

import json

import pytest

from f1coach_core import CoachingSchemaError, build_evidence_summary, load_sample_session
from f1coach_core.llm import (
    PROMPT_VERSION,
    build_coach_prompt,
    extract_json_object,
    report_from_llm_text,
)

FINDINGS_JSON = json.dumps(
    {
        "findings": [
            {
                "issue": "T1: losing 0.40 s",
                "cause": "Braking 15 m earlier.",
                "action": "Brake at 585 m.",
                "confidence": 0.8,
                "evidence": [
                    {
                        "metric": "brake_point",
                        "corner": "T1",
                        "value": 570.0,
                        "ref": 585.0,
                        "unit": "m",
                        "span_m": [510.0, 960.0],
                    }
                ],
            }
        ]
    }
)


def test_prompt_carries_the_evidence_and_the_contract():
    session = load_sample_session()
    summary = build_evidence_summary(session.laps[2], session.best_lap)
    prompt = build_coach_prompt(summary)
    assert "lap_03" in prompt and "lap_02" in prompt
    assert str(summary["corners"][0]["span_m"][0]) in prompt
    assert '"findings"' in prompt and "span_m" in prompt
    assert "never invent values" in prompt


@pytest.mark.parametrize(
    "wrapper",
    [
        "{payload}",
        "```json\n{payload}\n```",
        "Here is my analysis:\n{payload}\nHope this helps!",
    ],
)
def test_extract_json_survives_wrapping(wrapper):
    text = wrapper.format(payload=FINDINGS_JSON)
    assert extract_json_object(text) == json.loads(FINDINGS_JSON)


def test_extract_json_handles_braces_inside_strings():
    tricky = '{"findings": [], "note": "a {brace} in a string"}'
    assert extract_json_object(tricky)["note"] == "a {brace} in a string"


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("the driver should brake later", "no JSON object"),
        ('{"findings": [', "never closes"),
        ('{"findings": [}]}', "does not parse"),
    ],
)
def test_extract_json_failures_are_readable(text, fragment):
    with pytest.raises(CoachingSchemaError, match=fragment):
        extract_json_object(text)


def test_report_from_llm_text_stamps_model_and_prompt_version():
    report = report_from_llm_text(FINDINGS_JSON, model="ibm/granite-3-3-8b-instruct")
    assert report.model == "ibm/granite-3-3-8b-instruct"
    assert report.prompt_version == PROMPT_VERSION
    assert report.findings[0].evidence[0].span == (510.0, 960.0)


def test_report_from_llm_text_requires_findings_key():
    with pytest.raises(CoachingSchemaError, match="'findings'"):
        report_from_llm_text('{"analysis": "great lap"}', model="m")
