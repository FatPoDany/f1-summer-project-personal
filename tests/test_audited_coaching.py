"""One trusted coaching attempt, shared by interactive and queued callers."""

import json

import pytest

from f1coach_core import load_sample_session, run_audited_coaching


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


def test_provider_failure_is_returned_and_audited_with_partial_response():
    lap = load_sample_session().laps[1]

    class BrokenProvider:
        name = "broken"

        def generate(self, _summary, on_progress=None):
            on_progress('{"findings": [')
            raise RuntimeError("model stopped")

    attempt = run_audited_coaching(
        lap,
        None,
        provider_name="broken",
        provider_factory=BrokenProvider,
    )

    assert attempt.report is None
    assert attempt.error == "model stopped"
    assert attempt.audit_path is not None and attempt.audit_path.is_file()
    record = json.loads(attempt.audit_path.read_text("utf-8"))
    assert record["ok"] is False
    assert record["error"] == "model stopped"
    assert record["raw_response"] == '{"findings": ['
    assert record["evidence_summary"]["lap"]["name"] == lap.source.stem
    assert record["prompt"]
