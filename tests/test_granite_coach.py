"""Local Granite 4.1 provider for evidence-grounded post-lap coaching."""

import io
import json
import urllib.error

import pytest

from f1coach_core import build_evidence_summary, load_sample_session
from f1coach_core.coach import opportunity_catalog
from f1coach_core.granite_coach import GraniteCoach, GraniteCoachError
from f1coach_core.llm import build_coach_response_format


@pytest.fixture(scope="module")
def summary():
    session = load_sample_session()
    return build_evidence_summary(session.laps[2], session.best_lap)


def empty_report() -> str:
    return json.dumps({"findings": []})


def test_posts_to_openai_endpoint_with_strict_dynamic_schema_and_stamps_model(summary):
    captured = {}
    raw = empty_report()

    def transport(url, payload, headers, timeout):
        captured.update(url=url, payload=payload, headers=headers, timeout=timeout)
        return {
            "model": "served/granite-4.1-3b",
            "choices": [{"message": {"content": raw}}],
        }

    progress = []
    report = GraniteCoach(
        transport=transport,
        api_key="test-only",
        timeout_s=3,
    ).generate(summary, on_progress=progress.append)

    assert captured["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    assert captured["timeout"] == 3
    assert captured["headers"]["Authorization"] == "Bearer test-only"
    assert captured["payload"]["model"] == "ibm-granite/granite-4.1-3b"
    assert captured["payload"]["temperature"] == 0.0
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["response_format"] == build_coach_response_format(summary)
    assert captured["payload"]["response_format"]["type"] == "json_schema"
    assert captured["payload"]["response_format"]["json_schema"]["strict"] is True
    assert "never invent values" in captured["payload"]["messages"][0]["content"]
    assert report.model == "served/granite-4.1-3b"
    assert progress == [raw]


def test_granite_validates_single_lap_guidance_without_a_reference():
    session = load_sample_session()
    solo = build_evidence_summary(session.laps[2])
    available = next(iter(opportunity_catalog(solo).values()))
    citation = {
        key: available[key]
        for key in ("metric", "corner", "value", "ref", "unit", "span_m")
    }
    raw = json.dumps(
        {
            "findings": [
                {
                    "focus": available["focus"],
                    "issue": "Review this corner.",
                    "cause": "The evidence supports a technique review.",
                    "action": "Use a smooth and deliberate pedal transition.",
                    "confidence": 0.7,
                    "evidence": [citation],
                }
            ]
        }
    )

    def transport(url, payload, headers, timeout):
        return {"choices": [{"message": {"content": raw}}]}

    report = GraniteCoach(transport=transport).generate(solo)

    assert report.findings
    assert report.findings[0].evidence[0].ref == available["ref"]
    assert "reference" not in report.findings[0].cause.lower()


def test_environment_configures_endpoint_model_key_and_timeout(summary, monkeypatch):
    monkeypatch.setenv("GRANITE_BASE_URL", "https://granite.example/v1/")
    monkeypatch.setenv("GRANITE_MODEL", "team/granite")
    monkeypatch.setenv("GRANITE_API_KEY", "secret")
    monkeypatch.setenv("GRANITE_TIMEOUT_S", "12.5")
    captured = {}

    def transport(url, payload, headers, timeout):
        captured.update(url=url, payload=payload, headers=headers, timeout=timeout)
        return {"choices": [{"message": {"content": empty_report()}}]}

    report = GraniteCoach(transport=transport).generate(summary)

    assert captured["url"] == "https://granite.example/v1/chat/completions"
    assert captured["payload"]["model"] == "team/granite"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["timeout"] == 12.5
    assert report.model == "team/granite"  # requested model is the fallback stamp


def test_prebuilt_chat_completions_endpoint_is_not_duplicated(summary):
    captured = {}

    def transport(url, payload, headers, timeout):
        captured["url"] = url
        return {"choices": [{"message": {"content": empty_report()}}]}

    endpoint = "https://granite.example/v1/chat/completions"
    GraniteCoach(base_url=endpoint, transport=transport).generate(summary)
    assert captured["url"] == endpoint


@pytest.mark.parametrize("value", ["bad", 0, -1, float("nan"), float("inf")])
def test_rejects_invalid_timeout(value):
    with pytest.raises(GraniteCoachError, match="positive finite"):
        GraniteCoach(timeout_s=value)


def test_connection_failure_names_the_endpoint_it_could_not_reach(summary):
    """No longer tells anybody to start a server: Apex starts its own."""

    def transport(url, payload, headers, timeout):
        raise urllib.error.URLError("connection refused")

    with pytest.raises(GraniteCoachError, match="not reachable") as caught:
        GraniteCoach(transport=transport).generate(summary)
    message = str(caught.value)
    assert "connection refused" in message
    assert "Start the local Granite server" not in message


def test_a_slow_model_is_not_reported_as_an_absent_one(summary):
    """A 3B model on a laptop CPU is slow, not missing.

    Both used to land in one branch, so a first answer that overran the timeout
    said "the coach stopped responding" and sent everybody hunting a connection
    problem that was not there.
    """

    def transport(url, payload, headers, timeout):
        raise TimeoutError("timed out")

    with pytest.raises(GraniteCoachError, match="did not finish within") as caught:
        GraniteCoach(transport=transport).generate(summary)
    message = str(caught.value)
    assert "not reachable" not in message
    assert "GRANITE_TIMEOUT_S" in message  # what to change if it needs longer


def test_a_timeout_wrapped_in_a_url_error_is_still_a_timeout(summary):
    """urllib reports socket timeouts this way, which is how it slipped through."""

    def transport(url, payload, headers, timeout):
        raise urllib.error.URLError(TimeoutError("timed out"))

    with pytest.raises(GraniteCoachError, match="did not finish within"):
        GraniteCoach(transport=transport).generate(summary)


def test_http_failure_preserves_status_and_server_detail(summary):
    body = io.BytesIO(b'{"error":"model is unavailable"}')

    def transport(url, payload, headers, timeout):
        raise urllib.error.HTTPError(url, 404, "Not Found", None, body)

    with pytest.raises(GraniteCoachError, match="HTTP 404") as caught:
        GraniteCoach(transport=transport).generate(summary)
    assert "model is unavailable" in str(caught.value)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({}, r"choices\[0\]\.message\.content"),
        ({"choices": [{"message": {"content": ["not text"]}}]}, "non-text"),
        (["not", "an", "object"], "expected a JSON object"),
    ],
)
def test_malformed_server_response_is_readable(summary, response, message):
    def transport(url, payload, headers, timeout):
        return response

    with pytest.raises(GraniteCoachError, match=message):
        GraniteCoach(transport=transport).generate(summary)
