"""Coaching v2 contract: every provider response is shape- and fact-checked."""

from copy import deepcopy

import pytest

from f1coach_core import (
    CoachingSchemaError,
    build_evidence_summary,
    coaching_report_from_dict,
    get_provider,
    load_sample_session,
)
from f1coach_core.coach import evidence_catalog
from sample_laps import slow_and_best

EVIDENCE_KEYS = ("metric", "corner", "value", "ref", "unit", "span_m")


@pytest.fixture(scope="module")
def summary():
    return build_evidence_summary(*slow_and_best())


def valid_payload(summary: dict, focus: str = "braking") -> dict:
    available = next(item for item in evidence_catalog(summary).values() if item["focus"] == focus)
    evidence = {key: deepcopy(available[key]) for key in EVIDENCE_KEYS}
    return {
        "findings": [
            {
                "focus": focus,
                "issue": f"The main opportunity is {focus}",
                "cause": "The cited telemetry differs from the reference.",
                "action": "Use a smoother and more repeatable technique in this zone.",
                "evidence": [evidence],
            }
        ],
        "model": "test-model",
        "prompt_version": "coach-v2",
    }


def test_valid_grounded_payload_round_trips(summary):
    payload = valid_payload(summary)
    report = coaching_report_from_dict(payload, summary)
    assert report.findings[0].focus == "braking"
    assert report.findings[0].evidence[0].span == tuple(
        payload["findings"][0]["evidence"][0]["span_m"]
    )
    assert report.to_dict() == payload


def test_a_stored_confidence_still_loads_but_is_not_written_out_again(summary):
    """Every audit recorded before it was removed carries one.

    Those files are the only account of what a participant was actually shown,
    so they have to keep loading -- refusing them would not remove the number,
    it would hide that it was ever there. What must not happen is the value
    coming back out of the other side as though something still measured it.
    """
    payload = valid_payload(summary)
    payload["findings"][0]["confidence"] = 0.85

    report = coaching_report_from_dict(payload, summary)

    assert report.findings[0].confidence == 0.85
    assert "confidence" not in report.to_dict()["findings"][0]


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda data: data.pop("findings"), "missing 'findings'"),
        (lambda data: data["findings"][0].pop("issue"), "keys must be"),
        (lambda data: data["findings"][0].update(focus="strategy"), "focus"),
        (lambda data: data["findings"][0].update(confidence=1.5), "confidence"),
        (lambda data: data["findings"][0].update(evidence=[]), "evidence"),
        (
            lambda data: data["findings"][0]["evidence"][0].update(span_m=[900.0, 510.0]),
            "start < end",
        ),
        (
            lambda data: data["findings"][0]["evidence"][0].update(value="fast"),
            "value",
        ),
    ],
)
def test_validator_rejects_bad_shape_with_readable_errors(summary, mutate, fragment):
    payload = valid_payload(summary)
    mutate(payload)
    with pytest.raises(CoachingSchemaError) as error:
        coaching_report_from_dict(payload, summary)
    assert fragment in str(error.value)


@pytest.mark.parametrize(
    ("mutation", "fragment"),
    [
        ({"corner": "T99"}, "not available"),
        ({"metric": "imaginary_grip"}, "not available"),
        ({"value_delta": 1.0}, "value does not match"),
        ({"ref_delta": 1.0}, "ref does not match"),
        ({"unit": "mph"}, "unit does not match"),
        ({"span_delta": 1.0}, "span_m does not match"),
    ],
)
def test_grounding_rejects_fabricated_citation_facts(summary, mutation, fragment):
    payload = valid_payload(summary)
    evidence = payload["findings"][0]["evidence"][0]
    if "value_delta" in mutation:
        evidence["value"] += mutation["value_delta"]
    elif "ref_delta" in mutation:
        evidence["ref"] += mutation["ref_delta"]
    elif "span_delta" in mutation:
        evidence["span_m"][0] += mutation["span_delta"]
    else:
        evidence.update(mutation)

    with pytest.raises(CoachingSchemaError, match=fragment):
        coaching_report_from_dict(payload, summary)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("value", float("nan")),
        ("ref", float("inf")),
    ],
)
def test_validator_rejects_non_finite_evidence(summary, field, value):
    payload = valid_payload(summary)
    payload["findings"][0]["evidence"][0][field] = value
    with pytest.raises(CoachingSchemaError, match="finite"):
        coaching_report_from_dict(payload, summary)


@pytest.mark.parametrize("field", ["issue", "cause", "action"])
def test_validator_refuses_a_measurement_the_finding_does_not_cite(summary, field):
    payload = valid_payload(summary)
    payload["findings"][0][field] = "The measurement differs by 424242 metres."
    with pytest.raises(CoachingSchemaError, match="not a measurement this finding cites"):
        coaching_report_from_dict(payload, summary)


@pytest.mark.parametrize("field", ["issue", "cause", "action"])
def test_validator_lets_a_finding_quote_the_value_it_cites(summary, field):
    payload = valid_payload(summary)
    cited = payload["findings"][0]["evidence"][0]
    spoken = f"You reached {round(cited['value'], 1)} here."
    payload["findings"][0][field] = spoken
    report = coaching_report_from_dict(payload, summary)
    assert getattr(report.findings[0], field) == spoken


@pytest.mark.parametrize("field", ["issue", "cause", "action"])
def test_validator_lets_a_finding_quote_the_gap_it_is_about(summary, field):
    """The difference is derived from two checked numbers, not a third one."""
    payload = valid_payload(summary)
    cited = payload["findings"][0]["evidence"][0]
    gap = round(abs(cited["value"] - cited["ref"]))
    assert gap, "this fixture needs a corner where the two numbers differ"
    spoken = f"That is {gap} away from the reference."
    payload["findings"][0][field] = spoken
    report = coaching_report_from_dict(payload, summary)
    assert getattr(report.findings[0], field) == spoken


@pytest.mark.parametrize("level", ["top", "finding", "evidence"])
def test_validator_rejects_extra_fields_at_every_contract_level(summary, level):
    payload = valid_payload(summary)
    target = payload
    if level == "finding":
        target = payload["findings"][0]
    elif level == "evidence":
        target = payload["findings"][0]["evidence"][0]
    target["invented"] = "not allowed"

    with pytest.raises(CoachingSchemaError, match="keys must be"):
        coaching_report_from_dict(payload, summary)


def test_validator_rejects_more_than_three_findings(summary):
    payload = valid_payload(summary)
    payload["findings"] = [deepcopy(payload["findings"][0]) for _ in range(4)]
    with pytest.raises(CoachingSchemaError, match="at most 3"):
        coaching_report_from_dict(payload, summary)


def test_finding_focus_must_match_at_least_one_cited_metric(summary):
    payload = valid_payload(summary, focus="braking")
    payload["findings"][0]["focus"] = "throttle"
    with pytest.raises(CoachingSchemaError, match="cite at least one throttle metric"):
        coaching_report_from_dict(payload, summary)


def test_mock_coach_grounds_findings_in_the_evidence(summary):
    report = get_provider("mock").generate(summary)

    assert report.model == "mock" and report.prompt_version == "mock-3"
    assert 1 <= len(report.findings) <= 3
    worst = max(summary["corners"], key=lambda corner: corner["time_lost_s"])
    assert worst["corner"] == report.findings[0].evidence[0].corner
    catalog = evidence_catalog(summary)
    track_end = max(corner["span_m"][1] for corner in summary["corners"])
    for finding in report.findings:
        assert finding.focus in {"braking", "cornering", "throttle"}
        assert finding.confidence is None  # nothing measures one, so none is written
        assert finding.evidence
        assert any(
            catalog[(evidence.corner, evidence.metric)]["focus"] == finding.focus
            for evidence in finding.evidence
        )
        for evidence in finding.evidence:
            assert 0.0 <= evidence.span[0] < evidence.span[1] <= track_end


def test_mock_coach_stays_quiet_on_a_matching_lap():
    session = load_sample_session()
    best = session.best_lap
    report = get_provider("mock").generate(build_evidence_summary(best, best))
    assert report.findings == ()


def test_unknown_provider_is_a_readable_error():
    with pytest.raises(ValueError, match="granite, mock, ollama, watsonx"):
        get_provider("granite-cloud")


def test_nothing_a_participant_reads_claims_a_reliability_it_cannot_have(summary):
    """D: the chip said "high" on every card of the whole study.

    Eighty-nine findings were served to the study's laps and every one of them
    carried a number between 0.80 and 0.98, rendered green. It was the model's
    invention on the Granite path and a linear function of time lost on the
    mock, and a value that never varies cannot distinguish anything -- it only
    lends the advice an authority nothing measured.

    Nothing takes its place. A finding is published only when its difference
    cleared a bar set above the driver's own scatter through that corner
    (``features.notable_bar``), so the reliability a confidence claimed to
    report is the condition of the finding existing at all.
    """
    served = get_provider("mock").generate(summary).to_dict()

    assert served["findings"]
    for finding in served["findings"]:
        assert "confidence" not in finding
