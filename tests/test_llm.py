"""Shared LLM plumbing: compact prompt, strict schema, extraction and parsing."""

import json
from copy import deepcopy

import pytest

from f1coach_core import CoachingSchemaError, build_evidence_summary, load_sample_session
from f1coach_core.coach import evidence_catalog, opportunity_catalog
from f1coach_core.llm import (
    PROMPT_VERSION,
    build_coach_prompt,
    build_coach_response_format,
    extract_json_object,
    report_from_llm_text,
)

WRAPPED_JSON = json.dumps({"findings": []})
EVIDENCE_KEYS = ("metric", "corner", "value", "ref", "unit", "span_m")


@pytest.fixture(scope="module")
def summary():
    session = load_sample_session()
    return build_evidence_summary(session.laps[2], session.best_lap)


def available_citation(summary: dict, focus: str = "braking") -> dict:
    citation = next(
        item for item in opportunity_catalog(summary).values() if item["focus"] == focus
    )
    return {key: citation[key] for key in EVIDENCE_KEYS}


def grounded_findings_json(summary: dict, focus: str = "braking") -> str:
    citation = available_citation(summary, focus)
    return json.dumps(
        {
            "findings": [
                {
                    "focus": focus,
                    "issue": f"The main opportunity is {focus}",
                    "cause": "The cited telemetry differs from the reference.",
                    "action": "Use a smoother, more repeatable technique through this zone.",
                    "confidence": 0.8,
                    "evidence": [citation],
                }
            ]
        }
    )


def test_prompt_carries_only_compact_coachable_evidence_and_the_contract(summary):
    packet = deepcopy(summary)
    packet["raw_samples"] = ["must-not-enter-the-model"]
    template = deepcopy(summary["corners"][0])
    packet["corners"] = []
    for index in range(8):
        corner = deepcopy(template)
        corner["corner"] = f"X{index + 1}"
        corner["time_lost_s"] = float(index + 1)
        packet["corners"].append(corner)

    prompt = build_coach_prompt(packet)

    assert "lap_03" in prompt and "lap_02" in prompt
    assert '"findings"' in prompt and "span_m" in prompt and '"focus"' in prompt
    assert "Never invent" in prompt
    assert "at most once across the entire response" in prompt
    assert "must-not-enter-the-model" not in prompt  # unrelated bulk is removed
    assert '"X8"' in prompt and '"X3"' in prompt  # six highest-loss corners survive
    assert '"X2"' not in prompt and '"X1"' not in prompt


def test_response_format_is_strict_and_allows_only_available_citations(summary):
    response_format = build_coach_response_format(summary)
    assert response_format["type"] == "json_schema"
    envelope = response_format["json_schema"]
    assert envelope["name"] == "apex_lap_coaching" and envelope["strict"] is True

    schema = envelope["schema"]
    assert schema["additionalProperties"] is False
    findings = schema["properties"]["findings"]
    assert findings["minItems"] == 1
    assert findings["maxItems"] == 3
    finding = findings["items"]
    assert finding["additionalProperties"] is False
    assert finding["properties"]["focus"]["enum"] == [
        "braking",
        "cornering",
        "throttle",
    ]
    for field in ("issue", "cause", "action"):
        assert finding["properties"][field]["type"] == "string"
        assert "pattern" not in finding["properties"][field]

    choices = finding["properties"]["evidence"]["items"]["oneOf"]
    assert len(choices) == len(opportunity_catalog(summary))
    expected_item = next(iter(opportunity_catalog(summary).values()))
    expected = {key: expected_item[key] for key in EVIDENCE_KEYS}
    assert any(
        all(choice["properties"][key]["const"] == expected[key] for key in EVIDENCE_KEYS)
        for choice in choices
    )
    assert all(choice["additionalProperties"] is False for choice in choices)


def test_single_lap_prompt_and_schema_use_review_guides_not_a_reference():
    session = load_sample_session()
    solo = build_evidence_summary(session.laps[2])

    prompt = build_coach_prompt(solo)
    response_format = build_coach_response_format(solo)

    assert "single lap" in prompt.lower()
    assert "review threshold" in prompt.lower()
    assert '"reference": null' in prompt
    assert opportunity_catalog(solo)
    findings = response_format["json_schema"]["schema"]["properties"]["findings"]
    assert findings["minItems"] == 1


def test_empty_evidence_schema_forces_an_empty_findings_list():
    response_format = build_coach_response_format({"corners": []})
    findings = response_format["json_schema"]["schema"]["properties"]["findings"]
    assert findings["minItems"] == 0
    assert findings["maxItems"] == 0


@pytest.mark.parametrize(
    "wrapper",
    [
        "{payload}",
        "```json\n{payload}\n```",
        "Here is my analysis:\n{payload}\nHope this helps!",
    ],
)
def test_extract_json_survives_wrapping(wrapper):
    text = wrapper.format(payload=WRAPPED_JSON)
    assert extract_json_object(text) == json.loads(WRAPPED_JSON)


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


def test_report_from_llm_text_stamps_model_prompt_and_checks_evidence(summary):
    text = grounded_findings_json(summary)
    report = report_from_llm_text(
        text,
        model="ibm-granite/granite-4.1-3b",
        evidence_summary=summary,
    )
    expected = available_citation(summary)
    assert report.model == "ibm-granite/granite-4.1-3b"
    assert report.prompt_version == PROMPT_VERSION
    assert report.findings[0].focus == "braking"
    assert report.findings[0].evidence[0].span == tuple(expected["span_m"])


@pytest.mark.parametrize("field", ["issue", "cause", "action"])
def test_report_from_llm_text_replaces_model_prose_with_grounded_guidance(summary, field):
    payload = json.loads(grounded_findings_json(summary))
    payload["findings"][0][field] = "Brake release differs by 42 metres."

    report = report_from_llm_text(
        json.dumps(payload),
        model="granite-test",
        evidence_summary=summary,
    )

    value = getattr(report.findings[0], field)
    assert value and not any(character.isdigit() for character in value)
    assert "42" not in value


def test_single_lap_repeated_claims_merge_into_one_finding():
    session = load_sample_session()
    solo = build_evidence_summary(session.laps[1])
    coast = [
        {key: item[key] for key in EVIDENCE_KEYS}
        for item in opportunity_catalog(solo).values()
        if item["metric"] == "coast_distance"
    ]
    assert len(coast) >= 4

    findings = []
    for confidence, evidence in (
        (0.95, coast[:2]),
        (0.90, coast[:2]),
        (0.85, coast[2:4]),
    ):
        findings.append(
            {
                "focus": "throttle",
                "issue": "Review the throttle transition.",
                "cause": "The cited coast distance crossed the review guide.",
                "action": "Use a smoother pedal transition.",
                "confidence": confidence,
                "evidence": evidence,
            }
        )

    report = report_from_llm_text(
        json.dumps({"findings": findings}),
        model="granite-test",
        evidence_summary=solo,
    )

    assert len(report.findings) == 1
    assert report.findings[0].confidence == pytest.approx(0.95)
    assert len(report.findings[0].evidence) == 4
    assert len(
        {(item.corner, item.metric) for item in report.findings[0].evidence}
    ) == 4


def test_duplicate_mislabelled_comparison_finding_is_grounded_and_merged():
    session = load_sample_session()
    comparison = build_evidence_summary(session.laps[0], session.laps[1])
    brake_points = {
        corner: {key: item[key] for key in EVIDENCE_KEYS}
        for (corner, metric), item in opportunity_catalog(comparison).items()
        if metric == "brake_point"
    }
    assert {"T1", "T3", "T5"} <= brake_points.keys()

    def finding(focus, evidence, confidence):
        return {
            "focus": focus,
            "issue": "Review this corner.",
            "cause": "The cited telemetry supports the review.",
            "action": "Use a smooth and repeatable technique.",
            "confidence": confidence,
            "evidence": evidence,
        }

    raw = {
        "findings": [
            finding("braking", [brake_points["T5"]], 0.9),
            finding("throttle", [brake_points["T1"], brake_points["T1"]], 0.8),
            finding("braking", [brake_points["T3"]], 0.7),
        ]
    }

    report = report_from_llm_text(
        json.dumps(raw),
        model="granite-test",
        evidence_summary=comparison,
    )

    assert len(report.findings) == 1
    assert report.findings[0].focus == "braking"
    assert [item.corner for item in report.findings[0].evidence] == ["T5", "T1", "T3"]


def test_fabricated_sibling_does_not_suppress_grounded_findings():
    session = load_sample_session()
    comparison = build_evidence_summary(session.laps[0], session.laps[1])
    brake_points = [
        {key: item[key] for key in EVIDENCE_KEYS}
        for (_corner, metric), item in opportunity_catalog(comparison).items()
        if metric == "brake_point"
    ]
    fabricated = deepcopy(brake_points[1])
    fabricated["value"] += 1.0

    def finding(evidence):
        return {
            "focus": "braking",
            "issue": "Review this corner.",
            "cause": "The cited telemetry supports the review.",
            "action": "Use a smooth and repeatable technique.",
            "confidence": 0.8,
            "evidence": [evidence],
        }

    report = report_from_llm_text(
        json.dumps(
            {
                "findings": [
                    finding(brake_points[0]),
                    finding(fabricated),
                    finding(brake_points[2]),
                ]
            }
        ),
        model="granite-test",
        evidence_summary=comparison,
    )

    published = [item for result in report.findings for item in result.evidence]
    assert [(item.corner, item.value) for item in published] == [
        (brake_points[0]["corner"], brake_points[0]["value"]),
        (brake_points[2]["corner"], brake_points[2]["value"]),
    ]


def test_opportunity_catalog_keeps_only_directionally_actionable_metrics(summary):
    catalog = opportunity_catalog(summary)

    assert catalog
    ambiguous = {"peak_brake", "brake_release", "min_speed_point"}
    assert all(metric not in ambiguous for _, metric in catalog)
    assert any(metric == "min_speed" for _, metric in catalog)
    assert all(
        item["value"] >= item["ref"] + 15.0
        for (_, metric), item in catalog.items()
        if metric == "throttle_reapply"
    )


def test_llm_parser_rejects_real_but_non_actionable_citation(summary):
    actionable = opportunity_catalog(summary)
    non_actionable = next(
        item for citation, item in evidence_catalog(summary).items() if citation not in actionable
    )
    citation = {key: non_actionable[key] for key in EVIDENCE_KEYS}
    payload = {
        "findings": [
            {
                "focus": non_actionable["focus"],
                "issue": "Telemetry shows an opportunity.",
                "cause": "The cited telemetry differs from the reference.",
                "action": "Use a smooth and repeatable technique.",
                "confidence": 0.8,
                "evidence": [citation],
            }
        ]
    }

    with pytest.raises(CoachingSchemaError, match="not available"):
        report_from_llm_text(
            json.dumps(payload),
            model="granite-test",
            evidence_summary=summary,
        )


def test_report_from_llm_text_requires_findings_key(summary):
    with pytest.raises(CoachingSchemaError, match="'findings'"):
        report_from_llm_text(
            '{"analysis": "great lap"}',
            model="m",
            evidence_summary=summary,
        )
