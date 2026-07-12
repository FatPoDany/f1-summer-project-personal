"""racecoach feedback: contract validation, grounded mock, engine + audit."""

import json

import pytest

from racecoach.analysis.metrics import build_run_metrics
from racecoach.feedback.contract import FeedbackSchemaError, feedback_from_dict, valid_refs
from racecoach.feedback.engine import FEEDBACK_NAME, coach_run, mock_feedback
from racecoach.telemetry.run_store import import_run, list_runs
from test_racecoach_analysis import loaded_run, make_run_frame


@pytest.fixture(scope="module")
def metrics():
    return build_run_metrics(loaded_run(make_run_frame()))


def payload_for(metrics):
    event = metrics["events"][0]
    return {
        "overall": "Two laps, one clear problem area.",
        "highlights": ["Best lap 30.000 s on lap 1"],
        "issues": [
            {
                "issue": "Ran wide in T1",
                "cause": "carried too much entry speed",
                "action": "brake 10 m earlier",
                "evidence": [
                    {"ref": event["event_id"], "detail": event["summary"], "numbers": {}}
                ],
            }
        ],
        "code_recommendations": ["rate-limit steer_cmd"],
        "next_experiment": "same track, earlier braking into T1",
    }


# -- contract -----------------------------------------------------------------


def test_valid_feedback_passes_and_is_stamped(metrics):
    feedback = feedback_from_dict(
        payload_for(metrics), metrics, model="test-model", prompt_version="feedback-v1"
    )
    assert feedback.model == "test-model" and feedback.prompt_version == "feedback-v1"
    assert feedback.issues[0].evidence[0].ref in valid_refs(metrics)


def test_untraceable_evidence_is_a_schema_error(metrics):
    bad = payload_for(metrics)
    bad["issues"][0]["evidence"][0]["ref"] = "corner-of-imagination"
    with pytest.raises(FeedbackSchemaError, match="corner-of-imagination"):
        feedback_from_dict(bad, metrics, model="m", prompt_version="v")


def test_issue_without_evidence_is_refused(metrics):
    bad = payload_for(metrics)
    bad["issues"][0]["evidence"] = []
    with pytest.raises(FeedbackSchemaError, match="cites its data"):
        feedback_from_dict(bad, metrics, model="m", prompt_version="v")


def test_section_and_lap_refs_are_valid_too(metrics):
    payload = payload_for(metrics)
    payload["issues"][0]["evidence"].append(
        {"ref": "T1", "detail": "min speed 90 km/h", "numbers": {}}
    )
    payload["issues"][0]["evidence"].append(
        {"ref": "lap 2", "detail": "29.9 s sampled", "numbers": {}}
    )
    feedback = feedback_from_dict(payload, metrics, model="m", prompt_version="v")
    assert [item.ref for item in feedback.issues[0].evidence][1:] == ["T1", "lap 2"]


def test_more_than_three_issues_is_refused(metrics):
    bad = payload_for(metrics)
    bad["issues"] = bad["issues"] * 4
    with pytest.raises(FeedbackSchemaError, match="at most 3"):
        feedback_from_dict(bad, metrics, model="m", prompt_version="v")


# -- mock engine --------------------------------------------------------------


def test_mock_feedback_is_grounded_in_the_metrics(metrics):
    feedback = mock_feedback(metrics)
    assert feedback.model == "mock"
    assert len(feedback.issues) == 3  # 5 events, capped at the 3 worst
    event_ids = {event["event_id"] for event in metrics["events"]}
    for issue in feedback.issues:
        assert issue.evidence[0].ref in event_ids
    severities = {event["event_id"]: event["severity"] for event in metrics["events"]}
    cited = [severities[issue.evidence[0].ref] for issue in feedback.issues]
    assert cited == sorted(cited, reverse=True)  # worst first
    # lap 2's sampled 29.9 s beats lap 1's reported 30.0 s
    assert "Best lap 29.900 s on lap 2" in feedback.highlights[0]


# -- coach_run ----------------------------------------------------------------


@pytest.fixture
def stored_run(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    csv = tmp_path / "bot_run.csv"
    make_run_frame().to_csv(csv, index=False)
    import_run(csv)
    return list_runs()[0].run_id


def test_coach_run_mock_writes_feedback_and_audit(stored_run):
    destination = coach_run(stored_run)  # no analyze first: coach_run bootstraps it

    feedback = json.loads(destination.read_text("utf-8"))
    assert destination.name == FEEDBACK_NAME
    assert feedback["model"] == "mock" and len(feedback["issues"]) == 3
    assert (destination.parent / "metrics.json").is_file()

    (audit,) = list((destination.parent / "coaching").glob("*-mock.json"))
    record = json.loads(audit.read_text("utf-8"))
    assert record["ok"] is True
    assert "never invent values" in record["prompt"]
    assert record["feedback"]["overall"] == feedback["overall"]


def test_coach_run_validates_streamed_output(stored_run):
    def good_streamer(prompt, on_progress):
        metrics = json.loads(prompt.split("Run metrics:\n\n", 1)[1].split("\n\nReply", 1)[0])
        return json.dumps(payload_for(metrics)), "granite-test"

    destination = coach_run(stored_run, provider="watsonx", streamer=good_streamer)
    feedback = json.loads(destination.read_text("utf-8"))
    assert feedback["model"] == "granite-test"
    assert feedback["prompt_version"] == "feedback-v1"


def test_coach_run_refuses_untraceable_streamed_output(stored_run):
    def lying_streamer(prompt, on_progress):
        payload = {
            "overall": "story time", "highlights": [], "code_recommendations": [],
            "next_experiment": "n/a",
            "issues": [{"issue": "x", "cause": "y", "action": "z",
                        "evidence": [{"ref": "made-up-9", "detail": "d"}]}],
        }
        return json.dumps(payload), "granite-test"

    with pytest.raises(FeedbackSchemaError, match="made-up-9"):
        coach_run(stored_run, provider="watsonx", streamer=lying_streamer)


def test_failed_runs_are_audited_with_the_error(stored_run):
    def exploding_streamer(prompt, on_progress):
        raise RuntimeError("watsonx call failed: 401")

    from racecoach.telemetry.run_store import load_run

    with pytest.raises(FeedbackSchemaError, match="401"):
        coach_run(stored_run, provider="watsonx", streamer=exploding_streamer)
    run = load_run(stored_run)
    records = [json.loads(p.read_text("utf-8")) for p in (run.path / "coaching").glob("*.json")]
    failed = [r for r in records if not r["ok"]]
    assert failed and "401" in failed[0]["error"]
    assert not (run.path / FEEDBACK_NAME).exists()  # no feedback published on failure


# -- CLI ----------------------------------------------------------------------


def test_cli_coach_prints_the_five_sections(stored_run, capsys):
    from racecoach.cli import main

    assert main(["coach", stored_run]) == 0
    out = capsys.readouterr().out
    assert "Overall:" in out and "Next experiment:" in out
    assert "! Lap" in out and "->" in out and "# " in out

    assert main(["coach", "no-such-run"]) == 2
    assert "racecoach:" in capsys.readouterr().err
