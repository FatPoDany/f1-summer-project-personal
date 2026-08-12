"""Visible Live Pit Wall state, background lifecycle, and safe failures."""

import threading
import time
from pathlib import Path

import pytest

from apex.live_view import (
    LivePitWallView,
    _DashboardAdvisor,
    _LatestTelemetry,
    _LiveSignals,
    _ReportingBackend,
)
from apex.main_window import MainWindow
from racecoach.control.simple_driver import SimpleDriver
from racecoach.granite import MockGraniteClient
from racecoach.granite.client import TelemetrySnapshot
from racecoach.telemetry.live import LiveConfig, ScrConnectionError


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


def make_snapshot(**overrides) -> TelemetrySnapshot:
    values = {
        "tick": 500,
        "lap": 2,
        "sim_time_s": 10.0,
        "speed_kmh": 142.5,
        "track_position": 0.1,
        "heading_angle_rad": 0.02,
        "clear_road_ahead_m": 80.0,
        "fuel_l": 42.0,
        "damage": 0.0,
        "rpm": 6800.0,
        "gear": 4,
        "nearest_opponent_m": 30.0,
        "throttle_cmd": 0.4,
        "brake_cmd": 0.0,
        "steer_cmd": 0.05,
        "tire_wear": (0.1, 0.2, 0.3, 0.4),
        "tire_temp_c": (88.0, 89.0, 90.0, 91.0),
        "tire_pressure_kpa": (201.0, 202.0, 203.0, 204.0),
        "tire_graining": (0.01, 0.02, 0.03, 0.04),
    }
    values.update(overrides)
    return TelemetrySnapshot(**values)


def test_live_pit_wall_is_toolbar_reachable_but_does_not_auto_connect(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    assert window._live_action.isEnabled()
    assert window._live._model_combo.currentData() == "granite"
    assert not window._live.running
    assert "Offline" in window._live._bridge_status.text()
    assert not window._live._stop_button.isEnabled()

    window._live_action.trigger()
    assert window._stacked.currentWidget() is window._live
    assert not window._live.running  # navigation itself must never open a socket


def test_live_readouts_show_protocol_wheel_order_and_mark_old_advice_stale(qtbot):
    view = LivePitWallView()
    qtbot.addWidget(view)
    source = make_snapshot()
    result = MockGraniteClient().generate(source)

    view._render_telemetry(source)
    view._render_advice(source, result, 8.25)

    assert [view._tires.item(row, 0).text() for row in range(4)] == [
        "FR",
        "FL",
        "RR",
        "RL",
    ]
    assert view._lap_value.text() == "2"
    assert view._speed_value.text() == "142.5 km/h"
    assert view._tires.item(0, 1).text() == "10.0%"
    assert view._tires.item(3, 2).text() == "91.0 °C"
    assert view._tires.item(2, 3).text() == "203.0 kPa"
    assert view._tires.item(1, 4).text() == "2.0%"
    assert "Source lap 2 · sim 10.0 s" in view._advice_source.text()
    assert "latency 8.25 s" in view._advice_source.text()
    assert result.advice.evidence[0].metric in view._evidence.toPlainText()
    assert "Online" in view._model_status.text()

    view._render_telemetry(make_snapshot(tick=1600, sim_time_s=32.0))
    assert view._advice_source.text().startswith("STALE ·")


def test_live_telemetry_bursts_coalesce_to_one_pending_latest_snapshot():
    buffer = _LatestTelemetry()
    wakeups = 0
    for tick in range(100):
        wakeups += buffer.put(make_snapshot(tick=tick, sim_time_s=tick * 0.02))

    assert wakeups == 1
    assert buffer.take().tick == 99
    assert buffer.take() is None
    assert buffer.put(make_snapshot(tick=100, sim_time_s=2.0)) is True


def test_bridge_is_not_reported_streaming_until_first_observed_packet(tmp_path):
    class InnerAdvisor:
        hud_advice = None

        def start(self, run_dir):
            pass

        def observe(self, tick, state, actions, lap):
            pass

        def close(self):
            pass

    token = object()
    signals = _LiveSignals(token)
    statuses = []
    signals.bridgeStatus.connect(lambda received, text, tone: statuses.append((text, tone)))
    advisor = _DashboardAdvisor(InnerAdvisor(), signals, lambda: True)

    advisor.start(tmp_path)
    assert statuses[-1] == ("Connected — waiting for first telemetry packet", "waiting")
    advisor.observe(0, {}, {}, 1)
    assert statuses[-1] == ("Online — Granite Bridge is streaming", "online")


def test_model_latency_bookkeeping_is_a_single_latest_slot():
    signals = _LiveSignals(object())
    backend = _ReportingBackend(
        MockGraniteClient(), signals, test_mode=True, is_active=lambda: True
    )
    backend.generate(make_snapshot(tick=1))
    backend.generate(make_snapshot(tick=2))

    assert len(backend._latencies) == 1
    assert backend.take_latency(1) == 0.0
    assert backend.take_latency(2) >= 0.0


def test_start_and_stop_run_capture_off_the_gui_thread(qtbot, tmp_path):
    capture_thread_ids: list[int] = []
    capture_started = threading.Event()

    def config_loader(_path: Path):
        return LiveConfig(host="127.0.0.1", port=3001), SimpleDriver()

    def fake_capture(config, driver, quiet, advisor, stop_requested):
        del config, driver, quiet
        capture_thread_ids.append(threading.get_ident())
        run_dir = tmp_path / "live-run"
        run_dir.mkdir()
        advisor.start(run_dir)
        advisor.observe(
            0,
            {
                "speedX": 100.0,
                "simTime": 0.0,
                "distFromStart": 1.0,
                "track": [200.0] * 19,
                "tireWear": [0.1, 0.2, 0.3, 0.4],
                "tireTempC": [85.0, 86.0, 87.0, 88.0],
                "tirePressureKPa": [200.0, 201.0, 202.0, 203.0],
                "tireGraining": [0.01, 0.02, 0.03, 0.04],
            },
            {"accel": 0.2, "brake": 0.0, "steer": 0.0},
            1,
        )
        capture_started.set()
        while not stop_requested():
            time.sleep(0.005)
        advisor.close()
        return run_dir

    view = LivePitWallView(
        capture_fn=fake_capture,
        config_loader=config_loader,
        backend_factory=lambda _mode: MockGraniteClient(),
    )
    qtbot.addWidget(view)
    view._model_combo.setCurrentIndex(1)  # explicit TEST mode
    gui_thread = threading.get_ident()

    view.start_capture()
    assert capture_started.wait(1.0)
    qtbot.waitUntil(lambda: "Online" in view._bridge_status.text(), timeout=2000)
    qtbot.waitUntil(lambda: "Online — TEST" in view._model_status.text(), timeout=2000)
    assert capture_thread_ids != [gui_thread]
    assert view._stop_button.isEnabled() and not view._start_button.isEnabled()

    with qtbot.waitSignal(view.sessionFinished, timeout=3000) as finished:
        view.stop_capture()
    assert finished.args == [str(tmp_path / "live-run")]
    assert not view.running and view._start_button.isEnabled()
    assert "stopped safely" in view._bridge_status.text()
    assert view.shutdown(timeout_s=0.1)


def test_bridge_failure_is_readable_and_restores_controls(qtbot):
    def config_loader(_path: Path):
        return LiveConfig(host="127.0.0.1", port=3001), SimpleDriver()

    def failing_capture(*_args, **_kwargs):
        raise ScrConnectionError("Granite Bridge did not answer")

    view = LivePitWallView(capture_fn=failing_capture, config_loader=config_loader)
    qtbot.addWidget(view)

    with qtbot.waitSignal(view.sessionFailed, timeout=2000) as failure:
        view.start_capture()

    assert "Granite Bridge did not answer" in failure.args[0]
    assert "Connection error" in view._bridge_status.text()
    assert "no validated model response" in view._model_status.text()
    assert view._start_button.isEnabled() and not view._stop_button.isEnabled()


def test_late_signals_from_an_old_task_cannot_mutate_a_new_session(qtbot, tmp_path):
    first_started = threading.Event()
    first_release = threading.Event()
    second_started = threading.Event()
    calls = 0

    def config_loader(_path: Path):
        return LiveConfig(host="127.0.0.1", port=3001), SimpleDriver()

    def fake_capture(config, driver, quiet, advisor, stop_requested):
        nonlocal calls
        del config, driver, quiet, advisor
        calls += 1
        if calls == 1:
            first_started.set()
            while not first_release.is_set():
                time.sleep(0.005)
            return tmp_path / "first"
        second_started.set()
        while not stop_requested():
            time.sleep(0.005)
        return tmp_path / "second"

    view = LivePitWallView(capture_fn=fake_capture, config_loader=config_loader)
    qtbot.addWidget(view)
    view.start_capture()
    assert first_started.wait(1.0)
    old_task = view._task
    assert old_task is not None
    with qtbot.waitSignal(view.sessionFinished, timeout=2000):
        first_release.set()

    view.start_capture()
    assert second_started.wait(1.0)
    qtbot.waitUntil(lambda: view.running, timeout=1000)
    current_task = view._task
    assert current_task is not None and current_task is not old_task

    old_task.signals.modelStatus.emit(old_task.token, "Online — stale old model", "online")
    old_task.signals.completed.emit(old_task.token, str(tmp_path / "old-late"))
    qtbot.wait(50)

    assert view.running and view._task is current_task
    assert "stale old model" not in view._model_status.text()
    assert "old-late" not in view._run_path.text()

    with qtbot.waitSignal(view.sessionFinished, timeout=2000):
        view.stop_capture()
