"""Session Debrief: the whole run a participant just drove, on a screen.

The measured half must never depend on the model — half the participants'
laptops will not run a 3B one — so most of what is asserted here is that the
numbers arrive, and arrive first.
"""

import pytest
from PySide6.QtWidgets import QLabel

from apex.coach_ready import DOWNLOAD_ELSEWHERE
from apex.debrief_view import (
    SessionDebriefView,
    _LapCard,
    lap_title,
)
from apex.main_window import MainWindow
from f1coach_core import load_session
from f1coach_core.exposure import DEBRIEF_VIEW, ExposureLog
from f1coach_core.workspace import ARRIVED_NAME
from racecoach.granite import report as gr
from racecoach.granite.debrief_store import latest_debrief
from racecoach.granite.host import Capability
from racecoach.granite.narrate import NarratedDebrief, NarratedPoint
from racecoach.granite.server import ServerError
from test_exposure import FakeClock
from test_granite_report import write_lap


class InlinePool:
    """Run the work where it was asked for, so a test sees a finished view.

    The view's own pool is a real one with a worker thread; driving it would mean
    waiting on an event loop for something that has nothing to prove.
    """

    def start(self, task, priority: int = 0) -> None:
        task.run()

    def tryTake(self, task) -> bool:  # noqa: N802 - QThreadPool's own spelling
        return False


class QueuedPool:
    """Takes the work and has not done it yet — the one state ``InlinePool`` hides.

    The real pool has a worker thread and the endpoint it resolves can take
    minutes to answer, so every re-entry to the screen lands while the debrief
    is still in flight. That is the window the duplicate runs were queued in.
    """

    def __init__(self) -> None:
        self.started: list = []
        self.taken: list = []

    def start(self, task, priority: int = 0) -> None:
        self.started.append(task)

    def tryTake(self, task) -> bool:  # noqa: N802 - QThreadPool's own spelling
        if task in self.started:
            self.started.remove(task)
            self.taken.append(task)
            return True
        return False


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
        "apex.coach_ready.gh.capability", lambda: Capability(**capability)
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


def test_asking_again_while_it_is_still_working_queues_nothing(
    qtbot, session, monkeypatch
):
    """Each extra click used to be another whole narration on a one-thread pool.

    A participant clicks Session debrief, sees "Reading your laps…", goes back
    to the Garage and clicks it again — and again. Every one of those started a
    fresh run behind the last, and the per-lap coach queued behind all of them.
    """
    ready(monkeypatch)
    view = SessionDebriefView(server=FakeServer())
    qtbot.addWidget(view)
    view._pool = pool = QueuedPool()
    view.set_session(session)

    view.set_session(load_session(session.path))  # back to the Garage, clicked again
    view.set_session(load_session(session.path))

    assert len(pool.started) == 1


def test_a_debrief_that_failed_can_be_asked_for_again(qtbot, session, monkeypatch):
    """Refusing the retry would leave the screen empty with no way out of it."""
    ready(monkeypatch)
    view = SessionDebriefView(server=FakeServer())
    qtbot.addWidget(view)
    view._pool = pool = QueuedPool()
    view.set_session(session)
    pool.started[0].signals.failed.emit("the laps could not be read")

    view.set_session(load_session(session.path))

    assert len(pool.started) == 2


def test_the_cards_do_not_pile_up_as_the_model_speaks(qtbot, session, monkeypatch):
    """One rendering per narrated lap must leave one column, not a growing one."""
    ready(monkeypatch)
    monkeypatch.setattr(gr, "narrate_debrief", _narrator)
    view = SessionDebriefView(server=FakeServer())
    qtbot.addWidget(view)
    open_on(view, session)

    assert len(cards(view)) == len(session.laps)
    # A stretch left behind by each render would outnumber the cards it spaced.
    assert view._cards.count() == len(session.laps) + 1


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
    # Building the window already handed the session to the debrief, on the
    # shared worker pool. Take that job back and do the same work here, so what
    # follows is about the screen rather than about a thread.
    window._debrief._pool = InlinePool()
    window._debrief.clear_session()
    window._garage._load_selected()

    assert window._garage._debrief_button.isEnabled()
    window._garage._debrief_button.click()

    assert window._stacked.currentWidget() is window._debrief
    assert window._debrief_action.isEnabled()
    assert window._debrief.session is not None
    assert window._debrief.report is not None
    assert window._debrief.report.findings > 0


def test_the_debrief_that_was_shown_is_kept(qtbot, session, monkeypatch):
    """It is the study's intervention, and it used to survive only on screen."""
    ready(monkeypatch)
    monkeypatch.setattr(gr, "narrate_debrief", _narrator)
    view = SessionDebriefView(server=FakeServer())
    qtbot.addWidget(view)
    open_on(view, session)

    stored = latest_debrief(session.path)
    assert stored is not None
    assert stored.narrated
    assert "You braked before you needed to." in stored.markdown
    assert stored.findings == view.report.findings


def test_reopening_puts_the_same_prose_back_without_asking_again(
    qtbot, session, monkeypatch
):
    """Minutes of a laptop CPU, and the second answer would not be the first."""
    ready(monkeypatch)
    monkeypatch.setattr(gr, "narrate_debrief", _narrator)
    open_on(SessionDebriefView(server=FakeServer()), session)

    def refuse(*_args, **_kwargs):
        raise AssertionError("the model was asked for prose that was already on file")

    monkeypatch.setattr(gr, "narrate_debrief", refuse)
    server = FakeServer()
    later = SessionDebriefView(server=server)
    qtbot.addWidget(later)
    open_on(later, session)

    assert server.starts == 0
    assert "You braked before you needed to." in screen_text(later)
    assert not later._coach_button.isEnabled()
    # And no second record: nothing new was produced to record.
    assert len(list((session.path / "debrief").glob("*.json"))) == 1


def test_a_laptop_that_cannot_reach_the_model_files_one_record_not_one_per_open(
    qtbot, session, monkeypatch
):
    """Otherwise five looks at the same screen become five identical records."""
    unavailable(monkeypatch)
    for _ in range(3):
        view = SessionDebriefView()
        qtbot.addWidget(view)
        open_on(view, session)

    records = list((session.path / "debrief").glob("*.json"))
    assert len(records) == 1
    assert latest_debrief(session.path).narrated is False


def test_the_written_coaching_is_added_to_a_session_already_measured(
    qtbot, session, monkeypatch
):
    """The measured record is not the last word once the model can be reached."""
    unavailable(monkeypatch)
    open_on(SessionDebriefView(), session)
    assert latest_debrief(session.path).narrated is False

    ready(monkeypatch)
    monkeypatch.setattr(gr, "narrate_debrief", _narrator)
    view = SessionDebriefView(server=FakeServer())
    qtbot.addWidget(view)
    open_on(view, session)

    stored = latest_debrief(session.path)
    assert stored.narrated
    assert len(list((session.path / "debrief").glob("*.json"))) == 2


def test_a_session_that_lands_starts_its_debrief_with_nobody_clicking(
    qtbot, tmp_path, monkeypatch
):
    """The participant is sent away to read this; it must not start when they arrive."""
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
    window._debrief.clear_session()
    window._garage._load_selected()  # what selecting a session in the list does

    # Nothing was navigated to, and the debrief is already there.
    assert window._stacked.currentWidget() is window._garage
    assert window._debrief.report is not None
    assert latest_debrief(sessions / "P001-baseline") is not None


def test_the_garage_says_whether_this_session_has_a_debrief_on_file(
    qtbot, tmp_path, monkeypatch
):
    """A folder whose participant never opened theirs has to look different."""
    unavailable(monkeypatch)
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    from f1coach_core import workspace

    sessions = workspace.sessions_root()
    sessions.mkdir(parents=True, exist_ok=True)
    write_lap(sessions / "P001-baseline", 1, speed_scale=0.88, brake_shift_m=-60.0)
    write_lap(sessions / "P001-baseline", 2)

    window = MainWindow()
    qtbot.addWidget(window)
    assert "No debrief on file" in window._garage._debrief_status.text()

    window._debrief._pool = InlinePool()
    window._debrief.clear_session()
    window._garage._load_selected()

    # Said as soon as it is true, not the next time somebody selects the row.
    text = window._garage._debrief_status.text()
    assert "Debrief on file" in text
    assert "measurements only" in text  # this laptop cannot reach the model


def test_a_finished_debrief_is_not_regenerated_by_asking_for_it_again(
    qtbot, tmp_path, monkeypatch
):
    """Every way back to a debrief that is already on screen, and none of them
    may restart it.

    It costs minutes of a participant's own CPU and the answer would be the
    same, so a second run is time taken from somebody who is waiting to drive
    again. The guard in ``set_session`` is the only thing standing between them
    and that, and it holds by a conjunction of five clauses -- exactly the kind
    of thing a later change breaks silently, because the symptom is a screen
    that works and a laptop that is busy.
    """
    ready(monkeypatch)
    monkeypatch.setattr(gr, "narrate_debrief", _narrator)
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    from f1coach_core import workspace

    sessions = workspace.sessions_root()
    sessions.mkdir(parents=True, exist_ok=True)
    write_lap(sessions / "P001-baseline", 1, speed_scale=0.88, brake_shift_m=-60.0)
    write_lap(sessions / "P001-baseline", 2)

    window = MainWindow()
    window._debrief._server = FakeServer()
    qtbot.addWidget(window)
    window._debrief._pool = InlinePool()
    window._debrief.clear_session()
    window._garage._load_selected()
    assert window._debrief.report is not None
    finished = window._debrief.report
    assert any(item.narrated is not None for item in finished.laps)

    idle = QueuedPool()
    window._debrief._pool = idle

    # No lap row is selected here, which is how a participant who has just
    # driven arrives at this screen.
    assert window._garage._table.currentRow() < 0
    window._garage._debrief_button.click()
    window.show_garage()
    window._garage._debrief_button.click()
    window._debrief_action.trigger()
    window._garage._table.selectRow(0)
    window._garage._debrief_button.click()
    window._garage._load_selected()  # the session reloaded into a new object
    window._garage._debrief_button.click()
    window._garage.refresh_sessions()
    window._garage._debrief_button.click()

    assert idle.started == []
    assert window._debrief.report is finished
    assert window._stacked.currentWidget() is window._debrief


def test_going_to_another_session_and_back_does_not_pay_for_the_first_twice(
    qtbot, tmp_path, monkeypatch
):
    """The screen remembers one session, and a researcher has six.

    Look at A, look at B, come back to A: the guard in ``set_session`` compares
    paths, so the return is a different session as far as it is concerned and
    the whole narration runs again. On a machine with the weights installed
    that is minutes of a 3B model per visit, for an answer already produced --
    and it is the ordinary way anybody uses the Garage, not an edge case.

    What makes the return cheap is the record on disk, not a bigger cache in
    memory: the reports hold every lap's telemetry, and keeping six of those
    alive on a participant's laptop would trade minutes of CPU for hundreds of
    megabytes. The numbers are re-measured, which is fast and keeps them honest
    against the build doing the measuring; only the prose is restored.
    """
    ready(monkeypatch)
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    from f1coach_core import workspace

    sessions = workspace.sessions_root()
    sessions.mkdir(parents=True, exist_ok=True)
    for name in ("P001-baseline", "P001-coached"):
        write_lap(sessions / name, 1, speed_scale=0.88, brake_shift_m=-60.0)
        write_lap(sessions / name, 2)

    asked = []

    def counting(summary, points, **kwargs):
        asked.append(summary)
        return _narrator(summary, points, **kwargs)

    monkeypatch.setattr(gr, "narrate_debrief", counting)

    window = MainWindow()
    window._debrief._server = FakeServer()
    qtbot.addWidget(window)
    window._debrief._pool = InlinePool()
    window._debrief.clear_session()

    def open_session(row: int) -> None:
        window._garage._session_list.setCurrentRow(row)
        window._garage._debrief_button.click()

    open_session(0)
    first = window._debrief.session.name
    cost = len(asked)
    assert cost > 0

    open_session(1)
    assert window._debrief.session.name != first

    before = len(asked)
    open_session(0)

    assert window._debrief.session.name == first
    assert len(asked) == before  # the model was not asked a second time
    assert "You braked before you needed to." in screen_text(window._debrief)
    assert not window._debrief._coach_button.isEnabled()


def timed(view: SessionDebriefView):
    """Drive the reading clock instead of waiting on it, and keep the views."""
    clock = FakeClock()
    written = []
    view._exposure = ExposureLog(clock=clock, sink=written.append)
    return clock, written


def test_time_spent_reading_the_debrief_is_recorded_as_a_dose(
    qtbot, session, monkeypatch
):
    """This screen is the intervention and it used to record nothing at all."""
    unavailable(monkeypatch)
    view = SessionDebriefView()
    qtbot.addWidget(view)
    clock, written = timed(view)
    view.show()
    qtbot.waitExposed(view)
    open_on(view, session)

    clock.tick(240.0)
    view.shutdown()

    assert len(written) == 1
    read = written[0]
    assert read.kind == DEBRIEF_VIEW
    assert read.driver == "A001"
    assert read.phase == "baseline"
    assert read.seconds == 240.0
    assert read.findings == view.report.findings
    # No model on this laptop, so the measurements were the whole of it.
    assert read.advice is False
    # The lap the rest of the run was measured against, which also says which
    # session the reading came from.
    assert read.lap == view.report.reference.source.name


def test_a_debrief_produced_in_the_background_is_nobody_s_dose(
    qtbot, session, monkeypatch
):
    """Every session that lands is debriefed now, whether anybody looks or not.

    A dose is what a participant had in front of them. Counting work their
    laptop did while they were on another screen would inflate the one number
    the dose-response argument rests on.
    """
    unavailable(monkeypatch)
    view = SessionDebriefView()
    qtbot.addWidget(view)
    clock, written = timed(view)
    open_on(view, session)  # never shown

    clock.tick(600.0)
    view.shutdown()

    assert view.report is not None  # it was produced
    assert written == []  # and nobody read it


def test_written_coaching_that_lands_mid_reading_still_counts_as_advice(
    qtbot, session, monkeypatch
):
    """Narration arrives lap by lap, minutes after the numbers do."""
    ready(monkeypatch)
    monkeypatch.setattr(gr, "narrate_debrief", _narrator)
    view = SessionDebriefView(server=FakeServer())
    qtbot.addWidget(view)
    clock, written = timed(view)
    view.show()
    qtbot.waitExposed(view)
    open_on(view, session)

    clock.tick(90.0)
    view.shutdown()

    assert len(written) == 1
    assert written[0].advice is True


def test_stepping_out_to_the_garage_holds_the_reading_rather_than_ending_it(
    qtbot, session, monkeypatch
):
    """Two short views instead of one long one would read as engagement."""
    unavailable(monkeypatch)
    view = SessionDebriefView()
    qtbot.addWidget(view)
    clock, written = timed(view)
    view.show()
    qtbot.waitExposed(view)
    open_on(view, session)

    clock.tick(100.0)
    view.hide()
    clock.tick(3600.0)  # a long time on some other screen
    view.show()
    qtbot.waitExposed(view)
    clock.tick(50.0)
    view.shutdown()

    assert len(written) == 1
    assert written[0].seconds == 150.0


def test_moving_to_another_session_ends_the_reading_of_the_first(
    qtbot, tmp_path, session, monkeypatch
):
    unavailable(monkeypatch)
    other = tmp_path / "second"
    write_lap(other, 1, speed_scale=0.9, brake_shift_m=-40.0)
    write_lap(other, 2)

    view = SessionDebriefView()
    qtbot.addWidget(view)
    clock, written = timed(view)
    view.show()
    qtbot.waitExposed(view)
    open_on(view, session)
    clock.tick(120.0)

    view.set_session(load_session(other))
    clock.tick(30.0)
    view.shutdown()

    assert [round(v.seconds) for v in written] == [120, 30]


def test_a_session_that_arrived_from_another_machine_is_not_a_dose(
    qtbot, session, monkeypatch
):
    """On the researcher's workspace, reading a debrief is analysis.

    Every session there is imported study data, and recording the researcher's
    reading against the participant measures the wrong person -- on the machine
    the dose is finally exported from.
    """
    unavailable(monkeypatch)
    (session.path / ARRIVED_NAME).write_text("{}", encoding="utf-8")
    view = SessionDebriefView()
    qtbot.addWidget(view)
    clock, written = timed(view)
    view.show()
    qtbot.waitExposed(view)
    open_on(view, session)

    clock.tick(300.0)
    view.shutdown()

    assert view.report is not None
    assert written == []


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
