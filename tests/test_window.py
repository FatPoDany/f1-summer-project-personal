"""Window smoke tests — these run offscreen on Mac/Linux CI (QT_QPA_PLATFORM=offscreen)."""

import pytest

from apex.main_window import MainWindow
from f1coach_core import load_sample_lap


def test_window_shows_sample_lap(qtbot):
    lap = load_sample_lap()
    window = MainWindow(lap)
    qtbot.addWidget(window)
    window.show()

    assert "sample_lap.csv" in window.windowTitle()
    assert window._trace.lap is lap
    x, y = window._trace._curve.getData()  # display data (may be peak-downsampled)
    assert len(x) > 100
    assert max(y) == pytest.approx(lap.top_speed_kmh, abs=0.5)
    assert "km/h" in window.statusBar().currentMessage()


def test_load_path_replaces_trace(qtbot, tmp_path):
    csv = tmp_path / "two_points.csv"
    csv.write_text("t,speed,throttle,brake,steer,gear\n0.0,10.0,1,0,0,3\n1.0,20.0,1,0,0,3\n")

    window = MainWindow(load_sample_lap())
    qtbot.addWidget(window)
    window.load_path(csv)

    assert "two_points.csv" in window.windowTitle()
    x, _ = window._trace._curve.getData()
    assert len(x) == 2
    assert "dist derived" in window.statusBar().currentMessage()
