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


def test_the_audit_says_whose_run_it_coached_not_just_which_file():
    """The trail is also the only record of what a participant was told.

    Lap files are renamed on import, pooled across machines, and read months
    later by somebody who was not there; a name cannot carry the participant.
    """
    from dataclasses import replace

    from f1coach_core import MockCoach
    from f1coach_core.lap import StudyIdentity

    lap = replace(
        load_sample_session().laps[1],
        identity=StudyIdentity(driver="P007", phase="baseline", setup="apex-study-v1"),
    )

    attempt = run_audited_coaching(
        lap, None, provider_name="mock", provider_factory=MockCoach
    )

    record = json.loads(attempt.audit_path.read_text("utf-8"))
    assert (record["driver"], record["phase"]) == ("P007", "baseline")
    assert record["setup"] == "apex-study-v1"


def test_a_lap_from_outside_the_study_is_audited_without_inventing_a_participant():
    from dataclasses import replace

    from f1coach_core import MockCoach
    from f1coach_core.lap import NO_IDENTITY

    lap = replace(load_sample_session().laps[1], identity=NO_IDENTITY)
    attempt = run_audited_coaching(
        lap, None, provider_name="mock", provider_factory=MockCoach
    )

    record = json.loads(attempt.audit_path.read_text("utf-8"))
    assert record["driver"] is None and record["phase"] is None
