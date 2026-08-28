"""How much coaching somebody actually took in, and what the number may claim."""

import json

import pytest

from f1coach_core.exposure import (
    CORNER_VIEW,
    REPORT_VIEW,
    ExposureLog,
    ReviewView,
    adopt_views,
    append_view,
    exposure_columns,
    exposure_path,
    load_exposure,
    phase_exposure,
    read_views,
)


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


class FakeClock:
    """A clock a test can move, so seconds can be asserted rather than waited."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


def _log(clock=None):
    written: list[ReviewView] = []
    log = ExposureLog(clock=clock or FakeClock(), sink=written.append)
    return log, written


def test_a_corner_is_timed_from_when_it_appeared_to_when_it_went():
    clock = FakeClock()
    log, written = _log(clock)

    log.opened(driver="P001", phase="baseline", kind=CORNER_VIEW, corner="T3")
    clock.tick(12.5)
    log.closed()

    assert [(v.corner, v.seconds) for v in written] == [("T3", 12.5)]


def test_opening_the_next_corner_files_the_one_before_it():
    """Nothing else closes a view when a participant simply moves on."""
    clock = FakeClock()
    log, written = _log(clock)

    log.opened(driver="P001", phase="baseline", kind=CORNER_VIEW, corner="T1")
    clock.tick(4.0)
    log.opened(driver="P001", phase="baseline", kind=CORNER_VIEW, corner="T2")
    clock.tick(9.0)
    log.closed()

    assert [(v.corner, v.seconds) for v in written] == [("T1", 4.0), ("T2", 9.0)]


def test_a_glance_too_short_to_have_been_read_is_not_a_dose():
    """Arrowing down the corner list is keystrokes, not attention."""
    log, written = _log()

    log.opened(driver="P001", phase="baseline", kind=CORNER_VIEW, corner="T1")
    log.closed()  # no time passed at all

    assert written == []


def test_time_behind_another_window_is_not_counted_but_is_not_lost():
    clock = FakeClock()
    log, written = _log(clock)

    log.opened(driver="P001", phase="baseline", kind=REPORT_VIEW)
    clock.tick(20.0)
    log.paused()
    clock.tick(600.0)  # they went and did something else entirely
    log.resumed()
    clock.tick(10.0)
    log.closed()

    assert written[0].seconds == 30.0


def test_a_lap_with_no_participant_records_nothing():
    """The sample session and a loose CSV are not somebody's study data."""
    log, written = _log()

    log.opened(driver=None, phase=None, kind=CORNER_VIEW, corner="T1")
    log.closed()

    assert written == [] and log.open_view is None


def test_advice_that_arrives_late_still_counts_as_advice_seen():
    """Coaching runs in the background; corners open before it lands."""
    clock = FakeClock()
    log, written = _log(clock)

    log.opened(driver="P001", phase="baseline", kind=CORNER_VIEW, corner="T3")
    clock.tick(5.0)
    log.advice_arrived()
    clock.tick(5.0)
    log.closed()

    assert written[0].advice is True
    assert written[0].seconds == 10.0


def test_a_view_lands_in_its_own_participants_log():
    path = append_view(
        ReviewView(driver="P001", phase="baseline", kind=CORNER_VIEW, seconds=3.0,
                   corner="T3", id="abc")
    )

    assert path == exposure_path("P001")
    assert [v.corner for v in read_views(path)] == ["T3"]


def test_a_log_line_that_is_not_a_view_is_skipped_not_repaired():
    """Logs arrive from participants' machines: untrusted, like their telemetry."""
    path = exposure_path("P001")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([
            "not json at all",
            json.dumps({"driver": "P001", "phase": "baseline", "kind": "invented",
                        "seconds": 5}),
            json.dumps({"driver": "", "phase": "baseline", "kind": CORNER_VIEW,
                        "seconds": 5}),
            json.dumps({"driver": "P001", "phase": "baseline", "kind": CORNER_VIEW,
                        "seconds": "ages"}),
            json.dumps({"driver": "P001", "phase": "baseline", "kind": CORNER_VIEW,
                        "seconds": 7.0, "corner": "T3"}),
        ]),
        encoding="utf-8",
    )

    assert [(v.corner, v.seconds) for v in read_views(path)] == [("T3", 7.0)]


def test_a_log_that_travels_twice_does_not_count_twice(tmp_path):
    """The second package repeats every view the first one carried."""
    first = tmp_path / "exposure.jsonl"
    view = ReviewView(driver="P001", phase="baseline", kind=CORNER_VIEW, seconds=8.0,
                      corner="T3", id="fixed-id")
    first.write_text(json.dumps(view.to_dict()) + "\n", encoding="utf-8")

    assert adopt_views(first, "P001") == 1
    assert adopt_views(first, "P001") == 0
    assert len(read_views(exposure_path("P001"))) == 1


def test_a_log_that_arrived_empty_still_creates_one(tmp_path):
    """Recorded and looked at nothing is a measurement; no log is not."""
    (tmp_path / "exposure.jsonl").write_text("", encoding="utf-8")

    adopt_views(tmp_path / "exposure.jsonl", "P001")

    assert load_exposure() == {"P001": []}


def test_views_belong_to_the_phase_they_were_looking_at():
    """Reviewing the second run afterwards is exposure that came too late."""
    views = [
        ReviewView(driver="P001", phase="baseline", kind=CORNER_VIEW, seconds=20.0,
                   corner="T3", advice=True),
        ReviewView(driver="P001", phase="baseline", kind=CORNER_VIEW, seconds=10.0,
                   corner="T3"),
        ReviewView(driver="P001", phase="coached", kind=CORNER_VIEW, seconds=99.0,
                   corner="T1"),
    ]

    before = phase_exposure(views, "baseline")
    after = phase_exposure(views, "coached")

    assert before.review_seconds == 30.0
    assert before.review_corners == 2
    assert before.review_corners_seen == 1  # the same corner twice is not two
    assert before.advice_seconds == 20.0
    assert after.review_seconds == 99.0


def test_nothing_recorded_is_blank_and_recorded_nothing_is_zero():
    """Reading the first as the second rewards the participants nobody watched."""
    unknown = exposure_columns(None, phase="baseline")
    watched = exposure_columns([], phase="baseline")

    assert set(unknown.values()) == {""}
    assert watched["review_seconds"] == 0
    assert watched["review_corners"] == 0


def test_the_demonstration_that_ships_with_apex_is_not_somebodys_dose(tmp_path):
    """Its five laps are real and still carry a real participant's identity."""
    clock = FakeClock()
    log, written = _log(clock)

    log.opened(
        driver="0822",
        phase="coached",
        kind=CORNER_VIEW,
        lap_source=tmp_path / "sample-session" / "lap02.csv",
        corner="T3",
    )
    clock.tick(120.0)  # long enough that only the guard can keep it out
    log.closed()

    assert written == []


def test_a_lap_a_participant_actually_drove_is_recorded(tmp_path):
    clock = FakeClock()
    log, written = _log(clock)

    log.opened(
        driver="P007",
        phase="baseline",
        kind=CORNER_VIEW,
        lap_source=tmp_path / "P007-baseline-20260827" / "run-lap02.csv",
        corner="T3",
    )
    clock.tick(6.0)
    log.closed()

    assert [(v.lap, v.corner) for v in written] == [("run-lap02.csv", "T3")]


def test_a_byte_order_mark_does_not_cost_the_first_view(tmp_path):
    """Found by running the frozen build: three views in, two out.

    Logs cross machines. Anything on Windows that rewrites one -- a text editor,
    a sync client, PowerShell's own Set-Content -- puts three bytes in front of
    the first line, and a sound view then vanishes with no malformed line to
    point at.
    """
    path = tmp_path / "exposure.jsonl"
    body = "\n".join(
        json.dumps(
            ReviewView(driver="P001", phase="baseline", kind=CORNER_VIEW,
                       seconds=float(n), corner=f"T{n}", id=f"v{n}").to_dict()
        )
        for n in (1, 2, 3)
    )
    path.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))

    assert [v.corner for v in read_views(path)] == ["T1", "T2", "T3"]
    assert adopt_views(path, "P001") == 3


def test_the_researcher_reading_a_collected_session_is_not_the_participants_dose(tmp_path):
    """Found by looking at the real workspace: fifteen views recorded against
    two participants, all of them the researcher clicking through their laps
    on the analysis machine. Every session there arrived in a handover."""
    from f1coach_core.workspace import ARRIVED_NAME

    clock = FakeClock()
    log, written = _log(clock)
    session = tmp_path / "B0826-baseline-20260826-091209"
    session.mkdir()
    (session / ARRIVED_NAME).write_text('{"participant_id": "B0826"}', encoding="utf-8")

    log.opened(driver="B0826", phase="baseline", kind=REPORT_VIEW,
               lap_source=session / "run-lap01.csv")
    clock.tick(300.0)
    log.closed()

    assert written == []


def test_a_participant_reading_on_their_own_machine_still_counts(tmp_path):
    """The guard must not switch the measure off everywhere it matters."""
    clock = FakeClock()
    log, written = _log(clock)
    session = tmp_path / "B0826-baseline-20260826-091209"
    session.mkdir()  # driven here: no arrived marker

    log.opened(driver="B0826", phase="baseline", kind=REPORT_VIEW,
               lap_source=session / "run-lap01.csv")
    clock.tick(30.0)
    log.closed()

    assert [(v.kind, v.seconds) for v in written] == [("report", 30.0)]
