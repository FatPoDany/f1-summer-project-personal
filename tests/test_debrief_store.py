"""The session debrief, kept beside the session it was about.

The debrief is what a coached participant is sent away to read, so it is the
study's intervention and not a screen. It used to exist only while that screen
was open. What is asserted here is that producing one leaves a record, that the
record says whose machine produced it, and that prose put back onto a later
measurement is only ever put back where it still belongs.
"""

import json
from dataclasses import replace

import pytest

from f1coach_core import load_session
from f1coach_core.workspace import ARRIVED_NAME
from racecoach.granite import report as gr
from racecoach.granite.debrief_store import (
    SCHEMA_VERSION,
    latest_debrief,
    restore_narration,
    write_debrief,
)
from racecoach.granite.narrate import NarratedDebrief, NarratedPoint
from test_granite_report import write_lap


@pytest.fixture
def session(tmp_path):
    """Two laps of the same corner, one of them braking 60 m early."""
    write_lap(tmp_path, 1, speed_scale=0.88, brake_shift_m=-60.0)
    write_lap(tmp_path, 2)
    return load_session(tmp_path)


def measured(session) -> gr.SessionReport:
    return gr.measure_report(list(session.laps))


def spoken(report: gr.SessionReport, text: str = "You braked early.") -> gr.SessionReport:
    """The same report with a model's prose on every measured stretch."""
    laps = []
    for item in report.laps:
        if not item.points:
            laps.append(item)
            continue
        laps.append(
            replace(
                item,
                summary="Most of the time went in one place.",
                narrated=NarratedDebrief(
                    summary="Most of the time went in one place.",
                    points=tuple(
                        NarratedPoint(point=point, narration=text, advice="Brake later.")
                        for point in item.points
                    ),
                    model="granite",
                ),
            )
        )
    return replace(report, laps=tuple(laps))


def test_producing_a_debrief_leaves_a_record_of_what_it_said(session):
    """Without this the folder cannot say whether a participant was coached."""
    report = spoken(measured(session))
    path = write_debrief(session.path, report, model="granite")

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["schema"] == SCHEMA_VERSION
    assert record["driver"] == "A001"
    assert record["phase"] == "baseline"
    assert record["setup"] == "apex-study-v1"
    assert record["narrated"] is True
    assert record["findings"] == report.findings
    assert "You braked early." in record["markdown"]
    assert path.parent.name == "debrief"
    assert path.parent.parent == session.path


def test_a_debrief_with_no_model_is_still_a_record(session):
    """Half the participants' laptops will not run a 3B model, and drove anyway."""
    path = write_debrief(session.path, measured(session))

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["narrated"] is False
    assert record["model"] == ""
    assert record["findings"] > 0
    stored = latest_debrief(session.path)
    assert stored is not None and not stored.narrated


def test_a_debrief_built_here_over_somebody_elses_laps_says_so(session):
    """A researcher's rerun is an analysis artefact, not what anybody was told."""
    (session.path / ARRIVED_NAME).write_text("{}", encoding="utf-8")
    path = write_debrief(session.path, spoken(measured(session)))

    assert json.loads(path.read_text(encoding="utf-8"))["arrived"] is True
    assert latest_debrief(session.path).arrived is True


def test_the_newest_record_is_the_one_read_back(session):
    """Two in the same second is the ordinary case, not the exotic one: the
    screen files a measured debrief the moment it has one and the narrated one
    right behind it."""
    first = write_debrief(session.path, measured(session))
    second = write_debrief(session.path, spoken(measured(session), text="Later here."))

    assert first != second
    stored = latest_debrief(session.path)
    assert stored is not None
    assert stored.path == second
    assert stored.narrated


def test_prose_comes_back_onto_the_same_measurements(session):
    """Reopening must not cost minutes of CPU to arrive at what is on file."""
    write_debrief(session.path, spoken(measured(session), text="You braked early."))
    stored = latest_debrief(session.path)

    restored = restore_narration(measured(session), stored)

    assert any(item.narrated is not None for item in restored.laps)
    assert "You braked early." in gr.render_markdown(restored)
    assert restored.findings == measured(session).findings


def test_prose_is_refused_when_the_stretches_it_was_written_about_moved(session):
    """Attaching by position would read perfectly and be a fabrication."""
    write_debrief(session.path, spoken(measured(session)))
    stored = latest_debrief(session.path)
    assert stored is not None
    # The same laps, measured into a different set of stretches.
    moved = replace(stored, narration={
        lap: replace(narration, keys=(("Turn 9", "brake_point_m", "moved 40 m"),))
        for lap, narration in stored.narration.items()
    })

    restored = restore_narration(measured(session), moved)

    assert all(item.narrated is None for item in restored.laps)


def test_an_unreadable_record_costs_a_rerun_and_not_the_screen(session):
    """These are read on a participant's laptop to decide whether to spend it."""
    directory = session.path / "debrief"
    directory.mkdir()
    (directory / "20260902-120000-000000-debrief.json").write_text(
        "{ not json", encoding="utf-8"
    )

    assert latest_debrief(session.path) is None


def test_a_record_from_a_schema_this_build_does_not_know_is_skipped(session):
    write_debrief(session.path, measured(session))
    directory = session.path / "debrief"
    (directory / "20990101-000000-000000-debrief.json").write_text(
        json.dumps({"schema": "apex-debrief-v99", "markdown": "later"}), encoding="utf-8"
    )

    stored = latest_debrief(session.path)
    assert stored is not None
    assert stored.markdown != "later"
