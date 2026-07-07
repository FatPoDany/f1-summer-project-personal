"""AI Race Engineer panel: worker round-trip, cards, and evidence-zoom."""

import pytest

from apex.coach_panel import FindingCard
from apex.main_window import MainWindow
from f1coach_core import load_sample_session


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
    window._analysis._panel._provider_combo.setCurrentText("mock")
    return window._analysis


def test_coach_me_renders_finding_cards(qtbot, analysis):
    panel = analysis._panel
    assert panel._coach_button.isEnabled()  # reference (lap_02) is set by default

    with qtbot.waitSignal(panel.reportReady, timeout=5000):
        panel._run()

    assert panel.report is not None and len(panel.report.findings) >= 1
    cards = analysis._panel._cards_host.findChildren(FindingCard)
    assert len(cards) == len(panel.report.findings)
    assert panel._chip.text() == "mock · mock-1"


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


def test_panel_requires_a_reference(qtbot, tmp_path):
    csv = tmp_path / "loose.csv"
    csv.write_text("t,speed,throttle,brake,steer,gear\n0.0,10,1,0,0,3\n1.0,20,1,0,0,3\n")
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_path(csv)

    panel = window._analysis._panel
    assert not panel._coach_button.isEnabled()
    assert "reference" in panel._placeholder.text().lower()
