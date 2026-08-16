"""AI Race Engineer panel: worker round-trip, cards, evidence-zoom, and the
per-run audit trail."""

import json
from pathlib import Path

import pytest

from apex import theme
from apex.coach_panel import CoachPanel, FindingCard, _CoachTask
from apex.main_window import MainWindow
from f1coach_core import (
    CoachProvider,
    Evidence,
    Finding,
    build_coach_prompt,
    build_evidence_summary,
    get_provider,
    latest_coaching_report,
    load_sample_session,
    workspace_root,
    write_coaching_audit,
)
from f1coach_core.coach import opportunity_catalog
from f1coach_core.llm import report_from_llm_text


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


@pytest.fixture
def analysis(qtbot, monkeypatch):
    session = load_sample_session()
    monkeypatch.setattr(
        "apex.coach_panel.get_provider", lambda name: get_provider("mock")
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window.show_analysis(session.laps[2], session)
    return window._analysis


def test_analyze_lap_renders_finding_cards(qtbot, analysis):
    panel = analysis._panel
    assert panel._coach_button.isEnabled()
    assert panel._coach_button.text() == "Analyze lap"

    with qtbot.waitSignal(panel.reportReady, timeout=5000):
        panel._run()

    assert panel.report is not None and len(panel.report.findings) >= 1
    cards = analysis._panel._cards_host.findChildren(FindingCard)
    assert len(cards) == len(panel.report.findings)
    assert panel._chip.text() == f"{panel.report.model} · {panel.report.prompt_version}"
    assert panel._coach_button.text() == "Analyze lap"


def test_panel_is_granite_only(qtbot, analysis):
    panel = analysis._panel
    assert panel.provider_name == "granite"
    assert panel._provider_label.text() == "Granite 4.1 · local"
    assert not hasattr(panel, "_provider_combo")


def test_panel_defaults_to_granite(qtbot):
    panel = CoachPanel()
    qtbot.addWidget(panel)

    assert panel.provider_name == "granite"
    assert panel._provider_label.text() == "Granite 4.1 · local"


@pytest.mark.parametrize(
    ("focus", "label", "colour"),
    [
        ("braking", "Braking", theme.RED),
        ("cornering", "Cornering", theme.BLUE),
        ("throttle", "Throttle", theme.GREEN),
    ],
)
def test_finding_card_displays_coloured_focus_chip(qtbot, focus, label, colour):
    finding = Finding(
        focus=focus,
        issue="T1 opportunity",
        cause="The trace differs from the reference.",
        action="Follow the reference trace.",
        confidence=0.8,
        evidence=(
            Evidence(
                metric="brake_point",
                corner="T1",
                value=580.0,
                ref=595.0,
                unit="m",
                span=(510.0, 960.0),
            ),
        ),
    )
    card = FindingCard(finding)
    qtbot.addWidget(card)

    assert card._focus_chip.text() == label
    assert colour in card._focus_chip.styleSheet()


def test_evidence_zoom_targets_the_span(qtbot, analysis):
    panel = analysis._panel
    with qtbot.waitSignal(panel.reportReady, timeout=5000):
        panel._run()
    d0, d1 = panel.report.findings[0].evidence[0].span

    panel.evidenceRequested.emit(d0, d1)

    stack = analysis._stack
    assert len(stack._highlights) == 3  # one region per strip
    x_lo, x_hi = stack._strips[0].vb.viewRange()[0]
    assert x_lo == pytest.approx(d0 - 60, abs=5)
    assert x_hi == pytest.approx(d1 + 60, abs=5)

    stack.reset_view()
    assert stack._highlights == []


def test_export_report_roundtrip(qtbot, analysis, tmp_path):
    panel = analysis._panel
    with qtbot.waitSignal(panel.reportReady, timeout=5000):
        panel._run()

    out = analysis.export_report(tmp_path / "report.html")
    html = out.read_text(encoding="utf-8")
    assert panel.report.findings[0].issue in html
    assert "lap_03" in html and "review guide" in html


def test_every_run_writes_an_audit_record(qtbot, analysis):
    panel = analysis._panel
    with qtbot.waitSignal(panel.reportReady, timeout=5000):
        panel._run()

    assert panel.audit_path is not None and panel.audit_path.is_file()
    assert panel._audit_button.isEnabled()
    # sample laps live in package data, so the record falls back to <workspace>/coaching
    assert panel.audit_path.parent == workspace_root() / "coaching"

    record = json.loads(panel.audit_path.read_text(encoding="utf-8"))
    assert record["ok"] is True and record["error"] is None
    assert record["provider"] == "mock" and record["model"] == "mock"
    assert record["lap"] == "lap_03" and record["reference"] is None
    assert record["prompt"] == build_coach_prompt(record["evidence_summary"])
    assert record["raw_response"].lstrip().startswith("{")  # the raw stream, verbatim
    assert len(record["report"]["findings"]) == len(panel.report.findings)


def _saved_granite_report(lap, reference=None):
    summary = build_evidence_summary(lap, reference)
    grounded = next(iter(opportunity_catalog(summary).values()))
    evidence = {
        key: grounded[key]
        for key in ("metric", "corner", "value", "ref", "unit", "span_m")
    }
    raw = json.dumps(
        {
            "findings": [
                {
                    "focus": grounded["focus"],
                    "issue": "A repeatable technique opportunity is visible.",
                    "cause": "The cited trace differs from its comparison guide.",
                    "action": "Use the cited marker to make the input more progressive.",
                    "confidence": 0.8,
                    "evidence": [evidence],
                }
            ]
        }
    )
    report = report_from_llm_text(raw, "granite-4.1-local", summary)
    path = write_coaching_audit(
        lap_source=lap.source,
        provider="granite",
        lap_name=lap.source.stem,
        reference_name=reference.source.stem if reference is not None else None,
        evidence_summary=summary,
        prompt=build_coach_prompt(summary),
        raw_response=raw,
        report=report,
    )
    return report, path


def test_panel_restores_saved_granite_report_for_the_same_context(qtbot):
    lap = load_sample_session().laps[1]
    report, audit_path = _saved_granite_report(lap)
    panel = CoachPanel()
    qtbot.addWidget(panel)

    with qtbot.waitSignal(panel.reportReady, timeout=5000):
        panel.set_context(lap, None)

    assert panel.report == report
    assert panel.audit_path == audit_path
    assert panel._audit_button.isEnabled()
    assert len(panel.findChildren(FindingCard)) == len(report.findings)


def test_saved_comparison_report_is_not_reused_for_single_lap_context(qtbot):
    session = load_sample_session()
    lap, reference = session.laps[2], session.laps[0]
    report, audit_path = _saved_granite_report(lap, reference)

    assert latest_coaching_report(lap, None, provider="granite") is None
    restored = latest_coaching_report(lap, reference, provider="granite")
    assert restored is not None
    assert restored.report == report
    assert restored.path == audit_path


def test_saved_report_is_rejected_when_its_frozen_evidence_no_longer_matches():
    lap = load_sample_session().laps[1]
    _report, audit_path = _saved_granite_report(lap)
    record = json.loads(audit_path.read_text(encoding="utf-8"))
    record["evidence_summary"]["lap"]["lap_time_s"] += 1.0
    audit_path.write_text(json.dumps(record), encoding="utf-8")

    assert latest_coaching_report(lap, None, provider="granite") is None


def test_saved_report_with_duplicate_cards_is_not_restored():
    lap = load_sample_session().laps[1]
    _report, audit_path = _saved_granite_report(lap)
    record = json.loads(audit_path.read_text(encoding="utf-8"))
    record["report"]["findings"] = record["report"]["findings"] * 2
    audit_path.write_text(json.dumps(record), encoding="utf-8")

    assert latest_coaching_report(lap, None, provider="granite") is None


def test_non_utf8_audit_does_not_hide_an_earlier_valid_report():
    lap = load_sample_session().laps[1]
    report, audit_path = _saved_granite_report(lap)
    (audit_path.parent / "99999999-999999-granite.json").write_bytes(b"\xff")

    restored = latest_coaching_report(lap, None, provider="granite")
    assert restored is not None
    assert restored.report == report
    assert restored.path == audit_path


class ExplodingCoach(CoachProvider):
    name = "boom"

    def generate(self, evidence_summary, on_progress=None):
        if on_progress is not None:
            on_progress('{"partial')  # died mid-stream
        raise RuntimeError("watsonx call failed: 401 Unauthorized")


def test_failed_runs_are_audited_too(qtbot, analysis):
    panel = analysis._panel
    task = _CoachTask(ExplodingCoach(), panel._lap, panel._reference)
    audits: list[str] = []
    failures: list[str] = []
    task.signals.audited.connect(audits.append)
    task.signals.failed.connect(failures.append)

    task.run()  # QRunnable.run is a plain method — run it synchronously

    assert failures == ["watsonx call failed: 401 Unauthorized"]
    record = json.loads(Path(audits[0]).read_text(encoding="utf-8"))
    assert record["ok"] is False and "401" in record["error"]
    assert record["provider"] == "boom"
    assert record["raw_response"] == '{"partial'  # what arrived before the failure
    assert record["model"] is None and record["report"] is None


def test_panel_allows_single_lap_analysis(qtbot, tmp_path):
    csv = tmp_path / "loose.csv"
    csv.write_text("t,speed,throttle,brake,steer,gear\n0.0,10,1,0,0,3\n1.0,20,1,0,0,3\n")
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_path(csv)

    panel = window._analysis._panel
    assert panel._coach_button.isEnabled()
    assert "single lap" in panel._placeholder.text().lower()


def test_stale_worker_result_cannot_replace_a_new_context(qtbot):
    session = load_sample_session()
    panel = CoachPanel()
    qtbot.addWidget(panel)
    stale = _CoachTask(get_provider("mock"), session.laps[0], None)
    panel._task = stale
    panel.set_context(session.laps[1], None)
    stale_report = get_provider("mock").generate(
        build_evidence_summary(session.laps[0])
    )

    panel._task_finished(stale, stale_report)

    assert panel.report is None
    assert session.laps[1].source.stem in panel._placeholder.text()
