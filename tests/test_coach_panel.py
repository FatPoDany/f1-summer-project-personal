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
    load_sample_session,
    workspace_root,
)


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


@pytest.fixture
def analysis(qtbot):
    session = load_sample_session()
    window = MainWindow()
    qtbot.addWidget(window)
    window.show_analysis(session.laps[2], session)  # ragged lap, ref defaults to best
    # pin the provider: the picker persists the user's last choice via QSettings
    combo = window._analysis._panel._provider_combo
    combo.setCurrentIndex(combo.findData("mock"))
    return window._analysis


def test_analyze_lap_renders_finding_cards(qtbot, analysis):
    panel = analysis._panel
    assert panel._coach_button.isEnabled()  # reference (lap_02) is set by default
    assert panel._coach_button.text() == "Analyze lap"

    with qtbot.waitSignal(panel.reportReady, timeout=5000):
        panel._run()

    assert panel.report is not None and len(panel.report.findings) >= 1
    cards = analysis._panel._cards_host.findChildren(FindingCard)
    assert len(cards) == len(panel.report.findings)
    assert panel._chip.text() == f"{panel.report.model} · {panel.report.prompt_version}"
    assert panel._coach_button.text() == "Analyze lap"


def test_provider_picker_uses_friendly_labels_and_provider_keys(qtbot, analysis):
    panel = analysis._panel
    combo = panel._provider_combo
    assert {combo.itemText(index): combo.itemData(index) for index in range(combo.count())} == {
        "Granite 4.1 (local)": "granite",
        "Mock": "mock",
        "Ollama": "ollama",
        "Watsonx": "watsonx",
    }
    combo.setCurrentIndex(combo.findData("granite"))
    assert combo.currentText() == "Granite 4.1 (local)"
    assert panel.provider_name == "granite"


def test_provider_picker_defaults_to_granite_without_saved_setting(qtbot, monkeypatch):
    class EmptySettings:
        def __init__(self, *_args):
            self.values = {}

        def value(self, key, default=None):
            return self.values.get(key, default)

        def setValue(self, key, value):
            self.values[key] = value

    monkeypatch.setattr("apex.coach_panel.QSettings", EmptySettings)
    panel = CoachPanel()
    qtbot.addWidget(panel)

    assert panel.provider_name == "granite"
    assert panel._provider_combo.currentText() == "Granite 4.1 (local)"


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
    assert "lap_03" in html and "lap_02" in html


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
    assert record["lap"] == "lap_03" and record["reference"] == "lap_02"
    assert record["prompt"] == build_coach_prompt(record["evidence_summary"])
    assert record["raw_response"].lstrip().startswith("{")  # the raw stream, verbatim
    assert len(record["report"]["findings"]) == len(panel.report.findings)


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


def test_panel_requires_a_reference(qtbot, tmp_path):
    csv = tmp_path / "loose.csv"
    csv.write_text("t,speed,throttle,brake,steer,gear\n0.0,10,1,0,0,3\n1.0,20,1,0,0,3\n")
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_path(csv)

    panel = window._analysis._panel
    assert not panel._coach_button.isEnabled()
    assert "reference" in panel._placeholder.text().lower()
