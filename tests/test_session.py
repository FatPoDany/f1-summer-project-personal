"""Session loading: a directory of laps, with problems reported not raised."""

import pytest

from f1coach_core import load_sample_session, load_session

GOOD_FAST = (
    "t,dist,speed,throttle,brake,steer,gear,sector\n"
    "0.0,0,20,1,0,0,3,1\n1.0,20,20,1,0,0,3,2\n2.0,40,20,1,0,0,3,3\n"
)
GOOD_SLOW = (
    "t,dist,speed,throttle,brake,steer,gear,sector\n"
    "0.0,0,10,1,0,0,3,1\n1.5,15,10,1,0,0,3,2\n3.0,30,10,1,0,0,3,3\n"
)


def test_load_session_mixes_laps_and_problems(tmp_path):
    (tmp_path / "a_fast.csv").write_text(GOOD_FAST)
    (tmp_path / "b_slow.csv").write_text(GOOD_SLOW)
    (tmp_path / "c_broken.csv").write_text("nope,not,telemetry\n1,2,3\n")
    session = load_session(tmp_path)

    assert session.name == tmp_path.name
    assert [lap.source.stem for lap in session.laps] == ["a_fast", "b_slow"]
    assert len(session.problems) == 1
    assert session.problems[0][0] == "c_broken.csv"
    assert "missing required column" in session.problems[0][1]

    best = session.best_lap
    assert best is not None and best.source.stem == "a_fast"
    assert session.delta_to_best(session.laps[1]) == pytest.approx(1.0)
    assert session.delta_to_best(best) == 0.0


def test_best_sector_times_take_the_min_per_sector(tmp_path):
    (tmp_path / "a.csv").write_text(GOOD_FAST)  # sectors: 1.0, 1.0
    (tmp_path / "b.csv").write_text(GOOD_SLOW)  # sectors: 1.5, 1.5
    session = load_session(tmp_path)
    best = session.best_sector_times
    assert best[1] == pytest.approx(1.0)
    assert best[2] == pytest.approx(1.0)


def test_sample_session_is_three_clean_laps():
    session = load_sample_session()
    assert [lap.source.stem for lap in session.laps] == ["lap_01", "lap_02", "lap_03"]
    assert session.problems == ()
    best = session.best_lap
    assert best is not None and best.source.stem == "lap_02"
    assert set(session.best_sector_times) == {1, 2, 3}
