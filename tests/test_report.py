"""HTML report export: self-contained, honest about what was (not) run."""

import re

from f1coach_core import (
    build_evidence_summary,
    get_provider,
    load_sample_session,
    render_html_report,
)


def test_full_report_contains_everything():
    session = load_sample_session()
    best, ragged = session.best_lap, session.laps[2]
    report = get_provider("mock").generate(build_evidence_summary(ragged, best))
    html = render_html_report(ragged, best, report, session_name="sample-session")

    assert html.startswith("<!doctype html>")
    assert "lap_03" in html and "lap_02" in html and "sample-session" in html
    assert "85.060 s" in html and "79.740 s" in html
    assert html.count("<polyline") >= 3  # ref speed, lap speed, zero line, delta
    # the delta grid stops a few metres shy of the flag, so allow +5.31x
    assert re.search(r"\+5\.3\d+ s at the flag", html)
    assert "S1" in html and "S3" in html  # sector table
    assert report.findings[0].issue in html
    assert f"mock · {report.prompt_version}" in html
    assert "http" not in html.split("xmlns")[0]  # no external assets before the svg ns


def test_report_without_reference_or_coaching():
    session = load_sample_session()
    html = render_html_report(session.best_lap, None, None)
    assert "No coaching was run" in html
    assert "Cumulative time delta" not in html
    assert "<polyline" in html  # speed trace still there
