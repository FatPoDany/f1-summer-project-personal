"""racecoach HTML run report: renders the persisted artifacts, end to end."""

import pytest

from racecoach.feedback.engine import coach_run
from racecoach.report.run_report import report_run
from racecoach.telemetry.run_store import import_run, list_runs
from test_racecoach_analysis import make_run_frame


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


@pytest.fixture
def stored_run(tmp_path):
    csv = tmp_path / "bot_run.csv"
    make_run_frame().to_csv(csv, index=False)
    import_run(csv)
    return list_runs()[0].run_id


def test_report_renders_the_whole_pipeline(stored_run):
    coach_run(stored_run)  # feedback (and metrics via bootstrap)
    destination = report_run(stored_run)

    html = destination.read_text("utf-8")
    assert destination.name == "report.html"
    assert stored_run in html
    assert "29.900 s" in html  # lap table carries the sampled lap-2 time
    assert html.count("<svg") == 5  # section ribbon + four channel charts
    assert "off_track-1" in html and "T1" in html and "T2" in html
    assert 'opacity=\'0.18\'' in html  # event bands drawn on the charts
    assert "mock · mock-1" in html  # feedback chip, stamped not echoed
    assert "Next experiment:" in html
    assert "coaching/" in html  # points the reader at the audit records


def test_report_without_feedback_still_stands(stored_run):
    destination = report_run(stored_run)  # bootstraps analyze, no coach run
    html = destination.read_text("utf-8")
    assert "No feedback generated yet" in html
    assert "racecoach coach" in html


def test_report_notes_missing_channels(tmp_path):
    # total_speed_mps is chartable but not part of the exporter signature,
    # so the degraded file still imports — and must degrade readably
    frame = make_run_frame().drop(columns=["total_speed_mps"])
    csv = tmp_path / "degraded.csv"
    frame.to_csv(csv, index=False)
    import_run(csv)
    run_id = list_runs()[0].run_id

    html = report_run(run_id).read_text("utf-8")
    assert "speed (km/h): channel not captured." in html
    assert html.count("<svg") == 4  # ribbon + the three surviving channels
    assert "wheel_lockup" in html  # the skipped detector is disclosed in the notes
