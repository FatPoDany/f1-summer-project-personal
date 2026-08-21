"""The coach strip: offer it, explain it, and never let it eat the measurements."""

import pytest

from apex.coach_bar import CoachBar
from f1coach_core.debrief import DebriefPoint
from racecoach.granite import host as gh


def point(corner="Turn 3"):
    return DebriefPoint(
        corner=corner,
        apex_m=412.0,
        span_m=(380.0, 470.0),
        time_lost_s=0.31,
        difference="braked 12 m earlier",
        detail="you 118 m, best 130 m",
    )


def bar_with(qtbot, capability):
    widget = CoachBar()
    qtbot.addWidget(widget)
    widget._capability = capability
    return widget


def ready():
    return gh.Capability(
        can_run=True, reason="ready", total_memory_bytes=16 * 1024**3, model_present=True
    )


def needs_download():
    return gh.Capability(
        can_run=True,
        reason="needs a one-off 2.1 GB download",
        total_memory_bytes=16 * 1024**3,
        model_present=False,
        download_bytes=2_099_501_664,
    )


def too_small():
    return gh.Capability(
        can_run=False,
        reason="This computer has 4.0 GB of memory and the coach needs about 6 GB.",
        total_memory_bytes=4 * 1024**3,
    )


def test_a_lap_with_nothing_to_report_hides_the_offer(qtbot):
    widget = bar_with(qtbot, ready())
    widget.set_debrief("This was your quickest lap of the session.", [])
    assert widget.isHidden()


def test_a_machine_that_cannot_run_it_is_told_why_and_offered_nothing(qtbot):
    widget = bar_with(qtbot, too_small())
    widget.set_debrief("0.9 s off your best lap.", [point()])

    assert widget._button.isHidden()
    assert "4.0 GB" in widget._status.text()


def test_a_machine_without_the_weights_is_offered_the_download(qtbot):
    widget = bar_with(qtbot, needs_download())
    widget.set_debrief("0.9 s off your best lap.", [point()])

    assert widget._button.text() == "Download coach"
    assert "2.1 GB" in widget._status.text()


def test_a_ready_machine_is_offered_the_explanation(qtbot):
    widget = bar_with(qtbot, ready())
    widget.set_debrief("0.9 s off your best lap.", [point()])
    assert widget._button.text() == "Explain my lap"


def test_a_stopped_download_says_the_progress_is_kept(qtbot):
    """It resumes from the .part file, so "cancel" must not read as "start over"."""
    widget = bar_with(qtbot, needs_download())
    widget.set_debrief("s", [point()])
    widget._on_cancelled(object())

    assert "resumes rather than restarts" in widget._status.text()
    assert widget._button.text() == "Download coach"


def test_a_failed_download_reports_the_reason_and_offers_a_retry(qtbot):
    widget = bar_with(qtbot, needs_download())
    widget.set_debrief("s", [point()])
    widget._on_download_failed(object(), "Not enough free space")

    assert "Not enough free space" in widget._status.text()
    assert widget._button.text() == "Try again"


def test_a_coach_that_could_not_speak_says_so_rather_than_looking_empty(qtbot):
    """Silence is the invented-number guard working, not a missing result."""
    widget = bar_with(qtbot, ready())
    widget.set_debrief("s", [point()])

    class Nothing:
        spoken_count = 0
        summary = ""
        points = ()

    widget._narration_token = token = object()
    widget._on_narrated(token, Nothing())

    assert "without guessing" in widget._status.text()
    assert widget._button.isEnabled()


def test_a_coaching_failure_is_explicit_that_the_analysis_still_stands(qtbot):
    widget = bar_with(qtbot, ready())
    widget.set_debrief("s", [point()])
    widget._narration_token = token = object()
    widget._on_narration_failed(token, "The model server did not answer.")

    assert "unaffected" in widget._status.text()
    assert widget._button.isEnabled()


def test_a_stale_narration_from_a_previous_lap_is_ignored(qtbot):
    """The participant may have clicked another lap while the model was thinking."""
    widget = bar_with(qtbot, ready())
    widget.set_debrief("s", [point()])
    widget._narration_token = object()
    before = widget._status.text()

    widget._on_narrated(object(), None)
    assert widget._status.text() == before


def test_download_progress_is_reported_in_the_units_a_person_reads(qtbot):
    widget = bar_with(qtbot, needs_download())
    widget.set_debrief("s", [point()])
    widget._on_progress(object(), 500_000_000, 2_099_501_664)

    assert "0.5 of 2.1 GB" in widget._status.text()


@pytest.mark.parametrize(
    "memory,expected",
    [(4 * 1024**3, False), (16 * 1024**3, True)],
)
def test_capability_refuses_machines_that_would_only_swap(monkeypatch, memory, expected):
    monkeypatch.setattr(gh, "total_memory_bytes", lambda: memory)
    monkeypatch.setattr(gh.gm, "available", lambda: True)

    assert gh.capability(server_present=True).can_run is expected


def test_a_build_without_the_server_declines_without_blaming_the_machine(monkeypatch):
    monkeypatch.setattr(gh, "total_memory_bytes", lambda: 32 * 1024**3)
    capability = gh.capability(server_present=False)

    assert not capability.can_run
    assert "installed without the local model server" in capability.reason
    assert "Everything else works" in capability.reason
