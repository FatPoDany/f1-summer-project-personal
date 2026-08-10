"""SCR protocol, baseline driver, and live capture against a stub scr_server.

The stub speaks the exact wire format verified from the torcs-1.3.7 sources
(SimpleParser tuples, ***identified***/***shutdown*** literals), and runs
lock-step: one state out, one action back — so the test is deterministic.
"""

import socket
import threading

import pytest

from racecoach.analysis.metrics import analyze_run
from racecoach.control.simple_driver import SimpleDriver
from racecoach.telemetry import scr
from racecoach.telemetry.live import LiveConfig, ScrConnectionError, capture_run
from racecoach.telemetry.run_store import list_runs, load_run

# -- protocol -------------------------------------------------------------


def test_init_string_carries_the_19_default_angles():
    line = scr.init_string("SCR")
    assert line.startswith("SCR(init -90 ") and line.endswith(" 90)")
    assert len(line.split()) == 20  # 'SCR(init' + 19 angles
    with pytest.raises(ValueError, match="exactly 19"):
        scr.init_string(angles=(0.0,) * 5)


def test_format_actions_matches_carcontrol_field_order():
    # CarControl::toString order: accel, brake, gear, steer, clutch, focus, meta
    assert scr.format_actions(0.8, 0.0, -0.25, 3) == (
        "(accel 0.8)(brake 0)(gear 3)(steer -0.25)(clutch 0)(focus 0)(meta 0)"
    )


def test_parse_state_unwraps_scalars_and_keeps_arrays():
    message = (
        "(angle 0.05)(speedX 120.5)(gear 4)"
        "(track " + " ".join(str(10 * i) for i in range(19)) + ")"
        "(wheelSpinVel 80 80 82 82)(junk)(broken 1 2"
    )
    state = scr.parse_state(message)
    assert state["angle"] == pytest.approx(0.05)
    assert state["gear"] == 4.0
    assert isinstance(state["track"], list) and len(state["track"]) == 19
    assert state["wheelSpinVel"] == [80.0, 80.0, 82.0, 82.0]
    assert "junk" not in state and "broken" not in state  # malformed tuples skipped


# -- driver ---------------------------------------------------------------


def state_for(**overrides) -> dict:
    base = {
        "angle": 0.0, "trackPos": 0.0, "speedX": 100.0, "rpm": 5000.0,
        "gear": 3, "track": [200.0] * 19,
    }
    base.update(overrides)
    return base


def test_driver_pushes_on_an_open_straight():
    actions = SimpleDriver().drive(state_for(speedX=100.0))
    assert actions["accel"] > 0 and actions["brake"] == 0
    assert abs(actions["steer"]) < 0.05


def test_driver_brakes_when_too_fast_for_the_road_ahead():
    tight = state_for(speedX=200.0, track=[200.0] * 9 + [40.0] + [200.0] * 9)
    actions = SimpleDriver().drive(tight)
    assert actions["brake"] > 0 and actions["accel"] == 0


def test_driver_never_overlaps_the_pedals():
    driver = SimpleDriver()
    for speed in range(0, 300, 20):
        for ahead in (10.0, 60.0, 120.0, 200.0):
            actions = driver.drive(
                state_for(speedX=float(speed), track=[200.0] * 9 + [ahead] + [200.0] * 9)
            )
            assert actions["accel"] == 0 or actions["brake"] == 0


def test_driver_recovers_when_off_track():
    actions = SimpleDriver().drive(state_for(trackPos=1.4, angle=-0.2, speedX=40.0))
    assert actions["accel"] == pytest.approx(0.3) and actions["brake"] == 0
    assert actions["steer"] < 0  # steering back toward the track


def test_driver_shifts_by_rpm():
    assert SimpleDriver().drive(state_for(rpm=8000.0, gear=3))["gear"] == 4
    assert SimpleDriver().drive(state_for(rpm=2000.0, gear=3))["gear"] == 2


# -- live capture against a stub scr_server --------------------------------


def fmt(tag, *values) -> str:
    return f"({tag} " + " ".join(f"{value:g}" for value in values) + ")"


def make_state(dist: float, lap_time: float, track_pos: float = 0.3) -> str:
    return (
        fmt("angle", 0.02) + fmt("curLapTime", lap_time) + fmt("damage", 0)
        + fmt("distFromStart", dist) + fmt("distRaced", dist) + fmt("fuel", 90)
        + fmt("gear", 3) + fmt("lastLapTime", 0)
        + fmt("opponents", *([200.0] * 36)) + fmt("racePos", 1)
        + fmt("rpm", 6000) + fmt("speedX", 90) + fmt("speedY", 0) + fmt("speedZ", 0)
        + fmt("track", *([200.0] * 19)) + fmt("trackPos", track_pos)
        + fmt("wheelSpinVel", 80, 80, 82, 82) + fmt("z", 0.3)
        + fmt("focus", -1, -1, -1, -1, -1)
    )


class StubServer(threading.Thread):
    """Lock-step scr_server: identify, then state->action per tick, then shutdown."""

    def __init__(self, states: list[str]) -> None:
        super().__init__(daemon=True)
        self._states = states
        self.actions: list[str] = []
        self.init_line = ""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.settimeout(3.0)
        self.port = self._sock.getsockname()[1]

    def run(self) -> None:
        try:
            data, address = self._sock.recvfrom(1000)
            self.init_line = data.decode("ascii")
            self._sock.sendto(scr.IDENTIFIED.encode("ascii"), address)
            for state in self._states:
                self._sock.sendto(state.encode("ascii"), address)
                self.actions.append(self._sock.recvfrom(1000)[0].decode("ascii"))
            self._sock.sendto(scr.SHUTDOWN.encode("ascii"), address)
        except TimeoutError:
            pass
        finally:
            self._sock.close()


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


def test_capture_run_end_to_end_against_the_stub():
    ticks_per_lap = 300
    states = []
    for lap in range(2):
        for i in range(ticks_per_lap):
            dist = i * 5.0
            off = lap == 1 and 500 <= dist <= 575  # off-track window on lap 2
            states.append(
                make_state(dist, lap_time=i * 0.02, track_pos=1.3 if off else 0.3)
            )
    stub = StubServer(states)
    stub.start()

    run_dir = capture_run(
        LiveConfig(host="127.0.0.1", port=stub.port, run_name="stubrace", timeout_s=2.0),
        quiet=True,
    )
    stub.join(timeout=5.0)

    assert "(init -90 " in stub.init_line  # rangefinder handshake reached the server
    assert len(stub.actions) == 600 and "(accel" in stub.actions[0]

    (meta,) = list_runs()
    assert meta.run_id == run_dir.name and meta.capture == "scr-client"
    assert meta.n_samples == 600 and meta.laps_seen == (1, 2)
    assert meta.cadence_hz == pytest.approx(50.0)  # reconstructed 20 ms game clock

    run = load_run(meta.run_id)
    assert run.df["total_speed_mps"].iloc[0] == pytest.approx(25.0)  # 90 km/h -> SI

    import json

    metrics = json.loads(analyze_run(meta.run_id).read_text("utf-8"))
    off_track = [event for event in metrics["events"] if event["kind"] == "off_track"]
    assert len(off_track) == 1 and off_track[0]["lap"] == 2
    assert 500.0 <= off_track[0]["dist_start_m"] <= off_track[0]["dist_end_m"] <= 575.0
    assert any("sections" in note for note in metrics["analysis_notes"])  # no seg ground truth


def test_no_server_reads_as_instructions():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    silent_port = probe.getsockname()[1]
    probe.close()

    config = LiveConfig(
        host="127.0.0.1", port=silent_port, timeout_s=0.05, identify_attempts=2
    )
    with pytest.raises(ScrConnectionError, match="no scr_server answered"):
        capture_run(config, quiet=True)
    assert list_runs() == []  # nothing half-created


def test_identified_but_raceless_server_leaves_no_debris():
    class ShutdownServer(StubServer):
        def run(self) -> None:
            data, address = self._sock.recvfrom(1000)
            self.init_line = data.decode("ascii")
            self._sock.sendto(scr.IDENTIFIED.encode("ascii"), address)
            self._sock.sendto(scr.SHUTDOWN.encode("ascii"), address)
            self._sock.close()

    stub = ShutdownServer([])
    stub.start()
    with pytest.raises(ScrConnectionError, match="never sent a race state"):
        capture_run(
            LiveConfig(host="127.0.0.1", port=stub.port, timeout_s=2.0), quiet=True
        )
    stub.join(timeout=5.0)
    assert list_runs() == []


# -- config ---------------------------------------------------------------


def test_load_race_config_reads_toml_and_applies_overrides(tmp_path):
    from racecoach.cli import load_race_config

    config_path = tmp_path / "race.toml"
    config_path.write_text(
        '[connection]\nhost = "sim.local"\nport = 3005\n'
        "[race]\nmax_laps = 5\n[driver]\nmax_speed_kmh = 180.0\n"
    )
    config, driver = load_race_config(config_path, max_laps=2)
    assert config.host == "sim.local" and config.port == 3005
    assert config.max_laps == 2  # CLI override wins
    assert driver.max_speed_kmh == 180.0

    config_path.write_text("[driver]\nwarp_drive = 9\n")
    from racecoach.telemetry.run_store import RunImportError

    with pytest.raises(RunImportError, match="warp_drive"):
        load_race_config(config_path)
