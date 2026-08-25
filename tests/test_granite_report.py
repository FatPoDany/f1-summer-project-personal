"""A session report both a participant and a researcher can generate."""

import json

import numpy as np
import pytest

from f1coach_core.lap import StudyIdentity
from racecoach.granite import report as gr
from racecoach.granite.client import GraniteError


def write_lap(directory, number, *, speed_scale=1.0, brake_shift_m=0.0, driver="A001"):
    """One clear corner at ~300 m, mirroring tests/test_debrief.py's fixture.

    The debrief only reports a stretch once a measurement moves past its own
    threshold, so a lap has to have a corner to lose time in before any of this
    is exercised at all.
    """
    directory.mkdir(parents=True, exist_ok=True)
    dist = np.arange(0.0, 900.0, 5.0)
    dip = 30.0 * np.exp(-(((dist - 300.0) / 90.0) ** 2))
    speed = (60.0 - dip) * speed_scale
    brake = np.where((dist > 120.0 + brake_shift_m) & (dist < 290.0), 0.8, 0.0)
    throttle = np.where(dist > 310.0, 0.9, 0.0)
    t = np.concatenate([[0.0], np.cumsum(np.diff(dist) / speed[:-1])])
    rows = [
        f"{ti:.4f},{d:.1f},{v:.4f},{th:.1f},{b:.1f},0.0,4,1"
        for ti, d, v, th, b in zip(t, dist, speed, throttle, brake, strict=True)
    ]
    path = directory / f"telemetry-lap{number:02d}.csv"
    path.write_text(
        "# schema_version: 1\n"
        f"# lap: {number}\n"
        f"# driver: {driver}\n"
        "# phase: baseline\n"
        "# setup: apex-study-v1\n"
        "t,dist,speed,throttle,brake,steer,gear,sector\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def session(tmp_path):
    write_lap(tmp_path, 1, speed_scale=0.88, brake_shift_m=-60.0)  # slower, brakes early
    write_lap(tmp_path, 2)  # the reference
    return tmp_path


def transport_returning(content):
    def send(_url, _payload, _headers, _timeout):
        return {"model": "granite", "choices": [{"message": {"content": content}}]}

    return send


def test_laps_load_in_lap_order_and_carry_their_identity(session):
    laps = gr.session_laps(session)
    assert [lap.lap_number for lap in laps] == [1, 2]
    assert laps[0].identity == StudyIdentity(
        driver="A001", phase="baseline", setup="apex-study-v1"
    )


def test_a_stray_file_does_not_cost_the_session_its_report(session):
    (session / "notes.csv").write_text("this is not telemetry\n", encoding="utf-8")
    assert len(gr.session_laps(session)) == 2


def test_the_fastest_lap_is_the_reference_and_reports_nothing_against_itself(session):
    laps = gr.session_laps(session)
    result = gr.build_report(laps)

    assert result.reference is not None
    reference_report = next(r for r in result.laps if r.is_reference)
    assert reference_report.points == ()
    assert "quickest lap" in reference_report.summary


def test_measurement_happens_with_no_model_at_all(session):
    """Half the laptops will not run a model; the findings must not depend on it."""
    result = gr.build_report(gr.session_laps(session))

    assert result.findings > 0
    assert all(r.narrated is None for r in result.laps)
    assert result.narration_error == ""


def test_a_model_adds_prose_without_replacing_the_measurements(session):
    laps = gr.session_laps(session)
    measured = gr.build_report(laps)
    slow = next(r for r in measured.laps if not r.is_reference)
    point = slow.points[0]

    body = json.dumps({
        "summary": "You lost time braking early.",
        "stretches": ["You braked earlier here than on your best lap."]
        + [""] * (len(slow.points) - 1),
    })
    result = gr.build_report(laps, base_url="http://x/v1", model="granite",
                             transport=transport_returning(body))

    narrated = next(r for r in result.laps if not r.is_reference)
    assert narrated.points[0].headline == point.headline  # measurement unchanged
    assert narrated.spoken_count >= 1


def test_a_model_that_dies_mid_session_keeps_every_measurement(session):
    def fail(*_a, **_k):
        raise GraniteError("connection refused")

    result = gr.build_report(gr.session_laps(session), base_url="http://x/v1",
                             model="granite", transport=fail)

    assert result.findings > 0
    assert "connection refused" in result.narration_error


def test_the_report_says_why_it_is_quiet_rather_than_looking_empty(session):
    def fail(*_a, **_k):
        raise GraniteError("connection refused")

    result = gr.build_report(gr.session_laps(session), base_url="http://x/v1",
                             model="granite", transport=fail)
    text = gr.render_markdown(result)

    assert "Written coaching was unavailable" in text
    assert "connection refused" in text


def test_the_rendered_report_carries_the_study_identity(session):
    text = gr.render_markdown(gr.build_report(gr.session_laps(session)))

    assert "Driver: A001" in text
    assert "Phase: baseline" in text
    assert "Setup: apex-study-v1" in text


def test_the_measurements_arrive_before_the_model_is_asked(session):
    """A screen must be able to show numbers while the model is still thinking.

    On a laptop CPU the first answer takes minutes, and a participant staring at
    an empty panel for that long concludes the app is broken.
    """
    order = []

    def refuse(*_a, **_k):
        order.append("asked")
        raise GraniteError("connection refused")

    def measured(report):
        order.append("measured")
        assert report.findings > 0
        assert all(item.narrated is None for item in report.laps)

    gr.build_report(
        gr.session_laps(session),
        base_url="http://x/v1",
        model="granite",
        transport=refuse,
        on_measured=measured,
    )

    assert order[0] == "measured"
    assert order.count("measured") == 1  # once for the session, not once per lap
    assert "asked" in order  # and the model really was tried afterwards


def test_each_narrated_lap_arrives_as_it_is_written(session, monkeypatch):
    body = json.dumps({
        "summary": "You lost time braking early.",
        "stretches": [{"observation": "You braked earlier here.", "advice": ""}] * 4,
    })
    updates = []

    result = gr.build_report(
        gr.session_laps(session),
        base_url="http://x/v1",
        model="granite",
        transport=transport_returning(body),
        on_narrated=updates.append,
    )

    spoken = sum(1 for item in result.laps if item.narrated is not None)
    assert spoken >= 1
    assert len(updates) == spoken
    # Each update carries one more spoken lap than the one before it.
    counts = [sum(1 for item in u.laps if item.narrated is not None) for u in updates]
    assert counts == sorted(counts) and counts[-1] == spoken


def test_callbacks_are_optional_and_change_nothing(session):
    """The CLI passes neither; it must get exactly the report it always got."""
    laps = gr.session_laps(session)
    plain = gr.build_report(laps)
    watched = gr.build_report(laps, on_measured=lambda _r: None, on_narrated=lambda _r: None)

    assert gr.render_markdown(plain) == gr.render_markdown(watched)


def test_measure_report_is_the_whole_debrief_with_no_model_argument_at_all(session):
    laps = gr.session_laps(session)
    assert gr.render_markdown(gr.measure_report(laps)) == gr.render_markdown(
        gr.build_report(laps)
    )


def test_an_empty_session_renders_something_honest(tmp_path):
    result = gr.build_report(gr.session_laps(tmp_path))
    assert result.laps == () and result.reference is None
    assert "No readable laps" in gr.render_markdown(result)
