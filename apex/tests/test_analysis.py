"""Sector helpers — the numbers the ribbon and timing colours are built from."""

import pytest

from f1coach_core import load_telemetry_csv, sector_spans, sector_times


@pytest.fixture
def lap(tmp_path):
    path = tmp_path / "lap.csv"
    path.write_text(
        "t,dist,speed,throttle,brake,steer,gear,sector\n"
        "0.0,0,10,1,0,0,3,1\n"
        "1.0,10,10,1,0,0,3,1\n"
        "2.0,20,10,1,0,0,3,2\n"
        "3.0,30,10,1,0,0,3,2\n"
        "4.0,40,10,1,0,0,3,3\n"
        "5.0,50,10,1,0,0,3,3\n"
    )
    return load_telemetry_csv(path)


def test_sector_spans(lap):
    assert sector_spans(lap) == [(1, 0.0, 10.0), (2, 20.0, 30.0), (3, 40.0, 50.0)]


def test_sector_times(lap):
    times = sector_times(lap)
    assert times == {1: pytest.approx(2.0), 2: pytest.approx(2.0), 3: pytest.approx(1.0)}
    # the last sector has no successor: it runs to the final sample (4.0 -> 5.0)


def test_no_sector_channel(tmp_path):
    path = tmp_path / "bare.csv"
    path.write_text("t,speed,throttle,brake,steer,gear\n0,10,1,0,0,3\n1,10,1,0,0,3\n")
    lap = load_telemetry_csv(path)
    assert sector_spans(lap) == []
    assert sector_times(lap) == {}
