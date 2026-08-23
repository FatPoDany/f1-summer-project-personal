"""HTML report export: self-contained, honest about what was (not) run."""

import re

from f1coach_core import (
    build_evidence_summary,
    get_provider,
    load_sample_session,
    render_html_report,
)
from sample_laps import slow_and_best


def test_full_report_contains_everything():
    ragged, best = slow_and_best()
    report = get_provider("mock").generate(build_evidence_summary(ragged, best))
    html = render_html_report(ragged, best, report, session_name="sample-session")

    assert html.startswith("<!doctype html>")
    assert ragged.source.stem in html and best.source.stem in html
    assert "sample-session" in html
    assert f"{ragged.lap_time:.3f} s" in html and f"{best.lap_time:.3f} s" in html
    assert html.count("<polyline") >= 3  # ref speed, lap speed, zero line, delta
    # the delta grid stops a few metres shy of the flag, so it lands just under
    gap = ragged.lap_time - best.lap_time
    flag = re.search(r"\+(\d+\.\d+) s at the flag", html)
    assert flag and gap - 0.05 < float(flag.group(1)) <= gap
    assert "S1" in html and "S3" in html  # sector table
    # Sector times say where the lap was slow; the corner table says what
    # happened there, and the report is what leaves the app.
    assert "<h2>Corners</h2>" in html
    assert "technique review" in html and "Δ vs ref" in html
    assert "REVIEW · " in html or "No flag" in html
    assert report.findings[0].issue in html
    assert f"mock · {report.prompt_version}" in html
    assert "http" not in html.split("xmlns")[0]  # no external assets before the svg ns


def test_report_without_reference_or_coaching():
    session = load_sample_session()
    html = render_html_report(session.best_lap, None, None)
    assert "No coaching was run" in html
    assert "Cumulative time delta" not in html
    assert "<polyline" in html  # speed trace still there
