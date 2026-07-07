"""Coaching contract: the validator is the gate every provider goes through."""

import pytest

from f1coach_core import (
    CoachingSchemaError,
    build_evidence_summary,
    coaching_report_from_dict,
    get_provider,
    load_sample_session,
)


def valid_payload():
    return {
        "findings": [
            {
                "issue": "T1: losing 0.4 s",
                "cause": "Braking early.",
                "action": "Brake later.",
                "confidence": 0.8,
                "evidence": [
                    {
                        "metric": "brake_point",
                        "corner": "T1",
                        "value": 540.0,
                        "ref": 585.0,
                        "unit": "m",
                        "span_m": [510.0, 960.0],
                    }
                ],
            }
        ],
        "model": "mock",
        "prompt_version": "mock-1",
    }


def test_valid_payload_round_trips():
    report = coaching_report_from_dict(valid_payload())
    assert report.findings[0].evidence[0].span == (510.0, 960.0)
    assert report.to_dict() == valid_payload()


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda d: d.pop("findings"), "missing 'findings'"),
        (lambda d: d["findings"][0].pop("issue"), "findings[0].issue"),
        (lambda d: d["findings"][0].update(confidence=1.5), "confidence"),
        (lambda d: d["findings"][0].update(evidence=[]), "evidence"),
        (
            lambda d: d["findings"][0]["evidence"][0].update(span_m=[900.0, 510.0]),
            "start < end",
        ),
        (lambda d: d["findings"][0]["evidence"][0].update(value="fast"), "value"),
    ],
)
def test_validator_rejects_with_readable_errors(mutate, fragment):
    payload = valid_payload()
    mutate(payload)
    with pytest.raises(CoachingSchemaError) as err:
        coaching_report_from_dict(payload)
    assert fragment in str(err.value)


def test_mock_coach_grounds_findings_in_the_evidence():
    session = load_sample_session()
    summary = build_evidence_summary(session.laps[2], session.best_lap)  # ragged vs best
    report = get_provider("mock").generate(summary)

    assert report.model == "mock" and report.prompt_version == "mock-1"
    assert 1 <= len(report.findings) <= 3
    worst = max(summary["corners"], key=lambda c: c["time_lost_s"])
    assert worst["corner"] in report.findings[0].issue  # worst corner comes first
    track_end = max(c["span_m"][1] for c in summary["corners"])
    for finding in report.findings:
        assert 0.0 <= finding.confidence <= 1.0
        assert finding.evidence  # every claim cites evidence
        for evidence in finding.evidence:
            assert 0.0 <= evidence.span[0] < evidence.span[1] <= track_end


def test_mock_coach_stays_quiet_on_a_matching_lap():
    session = load_sample_session()
    best = session.best_lap
    report = get_provider("mock").generate(build_evidence_summary(best, best))
    assert report.findings == ()


def test_unknown_provider_is_a_readable_error():
    with pytest.raises(ValueError, match="watsonx and ollama arrive"):
        get_provider("watsonx")
