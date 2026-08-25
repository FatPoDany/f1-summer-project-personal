"""Session Debrief: the whole run a participant just drove, on a screen.

The measured half must never depend on the model — half the participants'
laptops will not run a 3B one — so most of what is asserted here is that the
numbers arrive, and arrive first.
"""

import pytest
from PySide6.QtWidgets import QLabel

from apex.debrief_view import (
    DOWNLOAD_ELSEWHERE,
    SessionDebriefView,
    _LapCard,
    lap_title,
)
from apex.main_window import MainWindow
from f1coach_core import load_session
from racecoach.granite import report as gr
from racecoach.granite.host import Capability
from racecoach.granite.narrate import NarratedDebrief, NarratedPoint
from racecoach.granite.server import ServerError
from test_granite_report import write_lap


class InlinePool:
    """Run the work where it was asked for, so a test sees a finished view.

    The view's own pool is a real one with a worker thread; driving it would mean
    waiting on an event loop for something that has nothing to prove.
    """

    def start(self, task, priority: int = 0) -> None:
        task.run()


class FakeServer:
    """A model endpoint that is already up, or one that refuses to come up."""

    def __init__(self, base_url: str = "http://127.0.0.1:8080/v1", error: str = "") -> None:
        self.base_url = base_url
        self._error = error
        self.starts = 0
        self.stops = 0

    def start(self, **_kwargs) -> str:
        self.starts += 1
        if self._error:
            raise ServerError(self._error)
        return self.base_url

    def stop(self, **_kwargs) -> None:
        self.stops += 1


@pytest.fixture(autouse=True)
def no_configured_endpoint(monkeypatch):
    monkeypatch.delenv("GRANITE_BASE_URL", raising=False)
    monkeypatch.delenv("GRANITE_MODEL", raising=False)


@pytest.fixture
def session(tmp_path):
    """Two laps of the same corner, one of them braking 60 m early."""
    write_lap(tmp_path, 1, speed_scale=0.88, brake_shift_m=-60.0)
    write_lap(tmp_path, 2)
    return load_session(tmp_path)


def coach(monkeypatch, **capability):
    monkeypatch.setattr(
        "apex.debrief_view.gh.capability", lambda: Capability(**capability)
    )


def unavailable(monkeypatch):
    coach(
        monkeypatch,
        can_run=False,
        reason="This copy of Apex was installed without the local model server.",
    )


def ready(monkeypatch):
    coach(monkeypatch, can_run=True, reason="ready", model_present=True)


def open_on(view: SessionDebriefView, session) -> SessionDebriefView:
    view._pool = InlinePool()
    view.set_session(session)
    return view


def cards(view: SessionDebriefView) -> list[_LapCard]:
    widgets = (view._cards.itemAt(i).widget() for i in range(view._cards.count()))
    return [widget for widget in widgets if isinstance(widget, _LapCard)]


def card_text(card: _LapCard) -> str:
    return " ".join(label.text() for label in card.findChildren(QLabel))


def screen_text(view: SessionDebriefView) -> str:
    return " ".join(card_text(card) for card in cards(view))


def test_the_numbers_are_complete_with_no_model_anywhere(qtbot, session, monkeypatch):
    """A laptop that cannot run the coach still gets the whole debrief."""
    unavailable(monkeypatch)
    view = SessionDebriefView()
    qtbot.addWidget(view)
    open_on(view, session)

    assert view.report is not None
    assert view.report.findings > 0
    assert len(cards(view)) == len(session.laps)
    assert all(item.narrated is None for item in view.report.laps)
    # And it says why it is quiet, rather than looking as though the session
    # had nothing worth saying.
    assert "installed without the local model server" in view._state.text()
    assert not view._coach_button.isEnabled()


def test_opening_the_screen_never_starts_the_model_download(qtbot, session, monkeypatch):
    """2.1 GB is something a participant asks for, not something a screen does."""
    coach(
        monkeypatch,
        can_run=True,
        reason="A one-off model download is required.",
        model_present=False,
        download_bytes=1,
    )
    server = FakeServer()
    view = SessionDebriefView(server=server)
    qtbot.addWidget(view)
    open_on(view, session)

    assert server.starts == 0
    assert view.report is not None and view.report.findings > 0
    assert DOWNLOAD_ELSEWHERE in view._state.text()
    assert not view._coach_button.isEnabled()


def test_the_written_coaching_lands_on_top_of_the_measurements(
    qtbot, session, monkeypatch
):
    ready(monkeypatch)
    monkeypatch.setattr(gr, "narrate_debrief", _narrator)
    server = FakeServer()
    view = SessionDebriefView(server=server)
    qtbot.addWidget(view)
    open_on(view, session)

    assert server.starts == 1
    assert server.stops == 0  # the window owns this server's lifetime, not the view
    text = screen_text(view)
    assert "You braked before you needed to." in text
    assert "Carry the brake in a little later next lap." in text
    # The measurement is still the measurement.
    assert "Braked 60 m earlier" in text
    assert not view._coach_button.isEnabled()  # nothing left for it to do
    assert "already has its written coaching" in view._coach_button.toolTip()


def test_a_server_that_will_not_start_still_shows_the_session(
    qtbot, session, monkeypatch
):
    ready(monkeypatch)
    view = SessionDebriefView(server=FakeServer(error="the port was already taken"))
    qtbot.addWidget(view)
    open_on(view, session)

    assert view.report is not None and view.report.findings > 0
    assert len(cards(view)) == len(session.laps)
    assert "the port was already taken" in view._state.text()
    # Retryable: the participant may have closed whatever was holding the port.
    assert view._coach_button.isEnabled()


def test_the_best_lap_is_named_as_the_one_the_others_are_measured_against(
    qtbot, session, monkeypatch
):
    unavailable(monkeypatch)
    view = SessionDebriefView()
    qtbot.addWidget(view)
    open_on(view, session)

    best = min(session.laps, key=lambda lap: lap.lap_time)
    reference_card = next(
        card for card in cards(view) if lap_title(best) in card_text(card)
    )
    assert "quickest lap" in card_text(reference_card)


def test_returning_to_the_same_session_keeps_the_prose(qtbot, session, monkeypatch):
    """Re-measuring would discard minutes of model work to reach the same numbers."""
    ready(monkeypatch)
    monkeypatch.setattr(gr, "narrate_debrief", _narrator)
    server = FakeServer()
    view = SessionDebriefView(server=server)
    qtbot.addWidget(view)
    open_on(view, session)
    first = view.report

    view.set_session(load_session(session.path))  # a fresh Session, same laps

    assert view.report is first
    assert server.starts == 1


def test_the_saved_debrief_is_the_file_the_command_line_writes(
    qtbot, session, monkeypatch, tmp_path
):
    unavailable(monkeypatch)
    view = SessionDebriefView()
    qtbot.addWidget(view)
    open_on(view, session)

    written = view.save_debrief(tmp_path / "debrief.md")
    text = written.read_text(encoding="utf-8")

    assert text == gr.render_markdown(view.report)
    assert text.startswith("# Driving debrief")
    assert "Driver: A001" in text and "Phase: baseline" in text


def test_a_session_with_no_readable_laps_says_so(qtbot, tmp_path, monkeypatch):
    unavailable(monkeypatch)
    empty = load_session(tmp_path)
    view = SessionDebriefView()
    qtbot.addWidget(view)
    open_on(view, empty)

    assert view.report is None
    assert "no readable laps" in view._state.text().lower()
    assert not view._save_button.isEnabled()


def test_the_garage_opens_the_whole_session_rather_than_one_lap_of_it(
    qtbot, tmp_path, monkeypatch
):
    """The study's intervention is the session, and it has to be reachable."""
    unavailable(monkeypatch)
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    from f1coach_core import workspace

    sessions = workspace.sessions_root()
    sessions.mkdir(parents=True, exist_ok=True)
    write_lap(sessions / "P001-baseline", 1, speed_scale=0.88, brake_shift_m=-60.0)
    write_lap(sessions / "P001-baseline", 2)

    window = MainWindow()
    qtbot.addWidget(window)
    window._debrief._pool = InlinePool()
    window._garage.refresh_sessions()

    assert window._garage._debrief_button.isEnabled()
    window._garage._debrief_button.click()

    assert window._stacked.currentWidget() is window._debrief
    assert window._debrief_action.isEnabled()
    assert window._debrief.session is not None
    assert window._debrief.report is not None
    assert window._debrief.report.findings > 0


def test_the_debrief_shares_the_one_model_server_and_the_one_worker(qtbot, monkeypatch):
    """Two 3B models on one participant's CPU is the failure this prevents."""
    unavailable(monkeypatch)
    window = MainWindow()
    qtbot.addWidget(window)

    assert window._debrief._server is window._coaching_server
    assert window._debrief._pool is window._coaching_pool
    assert window._coaching_pool.maxThreadCount() == 1


def _narrator(summary, points, **_kwargs) -> NarratedDebrief:
    """A model that says the same acceptable thing about every stretch."""
    return NarratedDebrief(
        summary="Most of the time went in one place.",
        points=tuple(
            NarratedPoint(
                point=point,
                narration="You braked before you needed to.",
                advice="Carry the brake in a little later next lap.",
            )
            for point in points
        ),
        model="granite",
    )
