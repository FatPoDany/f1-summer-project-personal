"""AI Race Engineer panel: worker round-trip, cards, evidence-zoom, and the
per-run audit trail."""

import json
import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QRunnable, QThreadPool

from apex import theme
from apex.coach_panel import CoachPanel, FindingCard, _CoachTask
from apex.coaching_queue import MANAGED_TIMEOUT_S
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
from sample_laps import slow_and_best, slow_lap


def test_slow_ai_work_cannot_consume_the_capture_thread_pool(qtbot):
    """Minutes-long model calls must never queue TORCS behind them."""
    panel = CoachPanel()
    qtbot.addWidget(panel)

    assert panel._pool is not QThreadPool.globalInstance()
    assert panel._pool.maxThreadCount() == 1


def test_managed_cpu_coaching_allows_longer_than_the_observed_420_seconds():
    """The packaged 3B model's first answer has already exceeded 420 seconds."""
    assert MANAGED_TIMEOUT_S >= 900


def test_automatic_task_rechecks_a_report_written_while_it_waited_in_the_pool(qtbot):
    """Garage and Analysis can queue the same context during the settle timer."""
    lap, reference = slow_and_best()
    report, audit = _saved_granite_report(lap, reference)

    class MustNotRun:
        name = "granite"

        def generate(self, _summary, on_progress=None):
            raise AssertionError("the exact queued Garage result should be restored")

    task = _CoachTask(
        MustNotRun(),
        lap,
        reference,
        restore_before_run=True,
    )
    restored = []
    audited = []
    task.signals.finished.connect(restored.append)
    task.signals.audited.connect(audited.append)

    task.run()

    assert restored == [report]
    assert audited == [str(audit)]


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


@pytest.fixture
def analysis(qtbot, monkeypatch):
    # A configured endpoint means the panel talks to that and manages nothing --
    # the same path a researcher with their own server takes.
    monkeypatch.setenv("GRANITE_BASE_URL", "http://127.0.0.1:8080/v1")
    session = load_sample_session()
    monkeypatch.setattr(
        "apex.coach_panel.get_provider", lambda name: get_provider("mock")
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window.show_analysis(slow_lap(session), session)
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
    panel._settle.stop()  # this test asks for the run; don't race the automatic one
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
    # Single-lap: this test is about the report, not about what it compares to.
    analysis._ref_combo.setCurrentIndex(analysis._ref_combo.findData(None))
    panel = analysis._panel
    panel._settle.stop()  # this test asks for the run; don't race the automatic one
    with qtbot.waitSignal(panel.reportReady, timeout=5000):
        panel._run()

    out = analysis.export_report(tmp_path / "report.html")
    html = out.read_text(encoding="utf-8")
    assert panel.report.findings[0].issue in html
    assert analysis.lap.source.stem in html and "review guide" in html


def test_every_run_writes_an_audit_record(qtbot, analysis):
    panel = analysis._panel
    panel._settle.stop()  # this test asks for the run; don't race the automatic one
    with qtbot.waitSignal(panel.reportReady, timeout=5000):
        panel._run()

    assert panel.audit_path is not None and panel.audit_path.is_file()
    assert panel._audit_button.isEnabled()
    # sample laps live in package data, so the record falls back to <workspace>/coaching
    assert panel.audit_path.parent == workspace_root() / "coaching"

    record = json.loads(panel.audit_path.read_text(encoding="utf-8"))
    assert record["ok"] is True and record["error"] is None
    assert record["provider"] == "mock" and record["model"] == "mock"
    # The reference is whatever the analysis was actually run against, and it is
    # recorded: a coaching record that did not say what the lap was compared
    # with could not be reproduced from the file.
    assert record["lap"] == analysis.lap.source.stem
    assert record["reference"] == analysis._ref_combo.currentData().source.stem
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


def test_a_saved_report_is_read_back_while_the_model_pool_is_busy(qtbot):
    """Putting a lap's answer back on screen is a disk read, not a model call.

    Both used to share the one serial model pool, so returning to a lap that had
    already been analysed sat on the "Ready" placeholder until whatever run was
    in flight finished -- minutes on a CPU, and indistinguishable from the
    earlier answer having been lost.
    """
    lap = load_sample_session().laps[1]
    report, _audit_path = _saved_granite_report(lap)
    panel = CoachPanel()
    qtbot.addWidget(panel)
    panel._auto = False

    occupied, release = threading.Event(), threading.Event()

    class HoldTheModelPool(QRunnable):
        def run(self):
            occupied.set()
            release.wait(30)

    panel._pool.start(HoldTheModelPool())
    assert occupied.wait(5), "the model pool never picked the work up"

    try:
        with qtbot.waitSignal(panel.reportReady, timeout=5000):
            panel.set_context(lap, None)
    finally:
        release.set()
        panel._pool.waitForDone(30000)

    assert panel.report == report


def test_returning_to_a_pair_shows_its_answer_at_once(qtbot):
    """A pair read once stays read for as long as the window is open.

    Every context switch used to re-derive the evidence summary from the lap
    CSVs and re-scan the audit directory, so a pair the participant had just
    been looking at came back blank before it came back at all.
    """
    session = load_sample_session()
    lap, other = session.laps[1], session.laps[0]
    report, audit_path = _saved_granite_report(lap)
    panel = CoachPanel()
    qtbot.addWidget(panel)
    panel._auto = False

    with qtbot.waitSignal(panel.reportReady, timeout=5000):
        panel.set_context(lap, None)

    panel.set_context(other, None)
    assert panel.report is None  # a different pair is never served the first's

    panel.set_context(lap, None)

    assert panel.report == report  # painted by the call itself, not by a worker
    assert panel.audit_path == audit_path
    assert panel._restore_task is None  # answered from memory; nothing queued
    # The pair it left is cleared with deleteLater, which needs the loop to turn.
    qtbot.waitUntil(lambda: len(panel.findChildren(FindingCard)) == len(report.findings))


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


def test_panel_allows_single_lap_analysis(qtbot, tmp_path, monkeypatch):
    """One lap on its own is still analysable -- there is just no lap to compare it to."""
    monkeypatch.setenv("GRANITE_BASE_URL", "http://127.0.0.1:8080/v1")
    csv = tmp_path / "loose.csv"
    csv.write_text("t,speed,throttle,brake,steer,gear\n0.0,10,1,0,0,3\n1.0,20,1,0,0,3\n")
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_path(csv)

    panel = window._analysis._panel
    assert panel._coach_button.isEnabled()
    assert panel._coach_button.text() == "Analyze lap"
    assert "single lap" in panel._placeholder.text().lower()


def test_a_build_without_the_coach_disables_it_and_says_why(qtbot, tmp_path, monkeypatch):
    """Better a disabled button with a reason than one that fails when pressed."""
    monkeypatch.delenv("GRANITE_BASE_URL", raising=False)
    monkeypatch.setattr("apex.coach_panel.gh.capability", lambda: __import__(
        "racecoach.granite.host", fromlist=["Capability"]
    ).Capability(can_run=False, reason="installed without the local model server"))
    csv = tmp_path / "loose.csv"
    csv.write_text("t,speed,throttle,brake,steer,gear\n0.0,10,1,0,0,3\n1.0,20,1,0,0,3\n")
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_path(csv)

    panel = window._analysis._panel
    assert not panel._coach_button.isEnabled()
    # The lap itself is still open and analysed; only the writing is unavailable.
    assert "single lap" in panel._placeholder.text().lower()


def test_automatic_analysis_never_starts_the_model_download(qtbot, monkeypatch):
    from racecoach.granite.host import Capability

    monkeypatch.delenv("GRANITE_BASE_URL", raising=False)
    monkeypatch.setattr(
        "apex.coach_panel.gh.capability",
        lambda: Capability(
            can_run=True,
            reason="A one-off model download is required.",
            model_present=False,
            download_bytes=1,
        ),
    )
    panel = CoachPanel()
    qtbot.addWidget(panel)
    panel._settle.setInterval(0)
    preparations = []
    panel._start_preparation = lambda *, download: preparations.append(download)

    panel.set_context(load_sample_session().laps[0], None)
    qtbot.waitUntil(lambda: panel._restore_task is None, timeout=2000)
    qtbot.wait(20)

    assert preparations == []
    assert panel._coach_button.text() == "Download coach"
    assert "download" in panel._placeholder.text().lower()


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


def test_analysis_starts_by_itself_rather_than_waiting_to_be_asked(qtbot, analysis):
    """Waiting for a click on a minutes-long job only means waiting longer."""
    panel = analysis._panel
    panel._settle.setInterval(0)

    with qtbot.waitSignal(panel.reportReady, timeout=5000):
        panel.set_context(analysis._lap, analysis._ref_combo.currentData())

    assert panel.report is not None


def test_changing_the_comparison_does_not_throw_away_work_in_progress(qtbot, analysis):
    """It used to orphan the run and ask somebody to press the button again."""
    panel = analysis._panel
    key = panel._context_key()

    class Pending:
        pass

    panel._running[key] = Pending()  # a run already under way for this pair
    panel._settle.stop()  # clear anything an earlier context scheduled
    panel._restore_into_view(panel._restore_task, None)

    assert "Still reading" in panel._placeholder.text()
    assert not panel._settle.isActive()  # this restore queued no second run


def test_a_run_that_finishes_for_a_lap_nobody_is_looking_at_is_still_kept(
    qtbot, analysis
):
    """The task writes its audit either way, so coming back restores it."""
    panel = analysis._panel
    panel._settle.stop()  # this test asks for the run; don't race the automatic one
    with qtbot.waitSignal(panel.reportReady, timeout=5000):
        panel._run()
    saved = panel.audit_path

    assert saved is not None and saved.is_file()
    assert panel._running == {}  # and nothing is left marked as running


def test_the_progress_bar_says_it_is_working_and_stops_when_it_is_not(qtbot, analysis):
    """A 3B model on a CPU gives no honest estimate, so presence is the signal."""
    panel = analysis._panel
    assert panel._progress.isHidden()

    with qtbot.waitSignal(panel.reportReady, timeout=5000):
        panel._run()

    assert panel._progress.isHidden()
    assert panel._progress.minimum() == 0 and panel._progress.maximum() == 0


def test_closing_the_panel_mid_analysis_does_not_crash_the_app(qtbot, analysis):
    """Its Qt children are gone; there is nobody left to show anything to."""
    panel = analysis._panel
    task = panel._restore_task or object()
    panel.deleteLater()
    qtbot.wait(10)

    panel._restore_finished(task, None)  # must not raise


def test_late_coaching_signals_ignore_a_deleted_panel(qtbot, analysis):
    """Lambda-held workers can outlive Qt children during a quick close."""
    panel = analysis._panel
    task = _CoachTask(get_provider("mock"), analysis._lap, None)
    panel._task = task
    panel._running[panel._context_key()] = task
    report = get_provider("mock").generate(build_evidence_summary(analysis._lap))

    panel.deleteLater()
    qtbot.wait(10)

    panel._task_audited(task, "/tmp/late-audit.json")
    panel._task_progress(task, "late text")
    panel._task_finished(task, report)


def test_a_result_is_shown_by_what_it_was_for_not_by_which_object_finished(
    qtbot, analysis
):
    """Somebody who looks at another lap and comes back is owed this result.

    By then `_task` is a different object, so identity alone left them watching a
    progress bar for a run that had already finished. Driven directly rather than
    raced against the pool, because the point is the rule, not the timing.
    """
    from f1coach_core import load_sample_session

    session = load_sample_session()
    panel = analysis._panel
    panel._settle.stop()

    first = analysis._lap
    # By file, not by identity: this session was loaded separately from the one
    # the view holds, so every lap in it fails an `is` check -- including the one
    # that is the same lap, whose context key would then collide with it.
    other = next(lap for lap in session.laps if lap.source != first.source)

    panel.set_context(first, None)
    key = panel._context_key()
    pending = _CoachTask(get_provider("mock"), first, None)
    panel._running[key] = pending

    panel.set_context(other, None)  # away...
    assert panel._running[key] is pending  # nothing cancelled it
    panel.set_context(first, None)  # ...and back
    panel._task = None  # a different object is current by now

    report = get_provider("mock").generate(build_evidence_summary(first))
    panel._task_finished(pending, report)

    assert panel.report is report
    assert panel._running == {}


def test_a_result_for_a_lap_the_participant_left_is_kept_but_not_shown(qtbot, analysis):
    """The task writes its audit either way, so returning later restores it."""
    from f1coach_core import load_sample_session

    session = load_sample_session()
    panel = analysis._panel
    panel._settle.stop()

    first = analysis._lap
    # By file, not by identity: this session was loaded separately from the one
    # the view holds, so every lap in it fails an `is` check -- including the one
    # that is the same lap, whose context key would then collide with it.
    other = next(lap for lap in session.laps if lap.source != first.source)
    panel.set_context(first, None)
    pending = _CoachTask(get_provider("mock"), first, None)
    panel._running[panel._context_key()] = pending

    panel.set_context(other, None)
    panel._task = None
    panel._task_finished(pending, get_provider("mock").generate(
        build_evidence_summary(first)
    ))

    assert panel.report is None  # not shown: it is about a lap they left


def test_the_same_pair_is_never_read_twice_at_once(qtbot, analysis):
    """Two runs under one key left one of them unable to find itself."""
    panel = analysis._panel
    panel._settle.stop()
    panel.set_context(analysis._lap, None)
    panel._start_analysis()
    key = panel._context_key()
    first = panel._running[key]

    panel._start_analysis()  # asked again before the first finished

    assert panel._running[key] is first
    assert "Still reading" in panel._placeholder.text()
