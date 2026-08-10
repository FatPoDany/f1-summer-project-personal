"""Path B live capture: drive the car over SCR, log every tick into the
run store. After `finalize_run` the result is indistinguishable from an
imported exporter run, so analyze/coach/report work unchanged.

Logging discipline (spec: "must not slow the simulation loop"): the hot loop
does parse -> buffered csv row -> drive -> send, nothing else; rows flush to
disk every FLUSH_ROWS. Units are normalized to SI at logging time (SCR
speeds arrive in km/h — scr_server.cpp:493-495). `sim_time_s` is the game
clock reconstructed from the fixed 20 ms tick; wall_time_s is also kept.
"""

import csv
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from racecoach.control.simple_driver import SimpleDriver
from racecoach.telemetry import scr
from racecoach.telemetry.run_store import finalize_run, new_run_dir

TICK_S = 0.02  # scr_server sends one state per 20 ms of game time
FLUSH_ROWS = 250
LAP_RESET_M = 100.0  # distFromStart dropping this far back means a new lap

FIELDS = (
    "sim_time_s", "wall_time_s", "dist_from_start_m", "dist_raced_m", "race_lap",
    "cur_lap_time_s", "last_lap_time_s", "total_speed_mps",
    "speed_body_x_mps", "speed_body_y_mps", "speed_body_z_mps",
    "angle_rad", "track_pos", "damage", "fuel_l", "gear", "engine_rpm",
    "race_pos", "pos_z_m",
    "fr_spin_vel_rad_s", "fl_spin_vel_rad_s", "rr_spin_vel_rad_s", "rl_spin_vel_rad_s",
    "opp_nearest_m",
    *(f"track_beam_{i:02d}_m" for i in range(19)),
    "accel_cmd", "brake_cmd", "steer_cmd", "gear_cmd", "clutch_cmd",
)


class ScrConnectionError(RuntimeError):
    """No scr_server answered. The message says how to get one."""


@dataclass(frozen=True)
class LiveConfig:
    host: str = "localhost"
    port: int = scr.DEFAULT_PORT
    bot_id: str = "SCR"
    run_name: str = "live"
    max_laps: int | None = 3
    max_ticks: int = 90_000  # 30 minutes of game time — a hard safety stop
    timeout_s: float = 1.0
    identify_attempts: int = 5


def capture_run(
    config: LiveConfig, driver: SimpleDriver | None = None, quiet: bool = False
) -> Path:
    """Identify, drive, log; returns the finalized run directory."""
    driver = driver or SimpleDriver()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(config.timeout_s)
        server = (config.host, config.port)
        _identify(sock, server, config)

        run_dir = new_run_dir(config.run_name)
        writer = _RowWriter(run_dir / "telemetry.csv")
        tick, lap, previous_dist = 0, 1, None
        try:
            while tick < config.max_ticks:
                try:
                    message = sock.recv(1000).decode("ascii", errors="replace")
                except TimeoutError:
                    continue  # paused menu/loading screen; the race clock isn't running
                if scr.SHUTDOWN in message:
                    break
                if scr.RESTART in message:
                    lap, previous_dist = 1, None
                    continue
                state = scr.parse_state(message)
                if "distFromStart" not in state:
                    continue  # not a state packet
                dist = float(state["distFromStart"])
                if previous_dist is not None and previous_dist - dist > LAP_RESET_M:
                    lap += 1
                    if config.max_laps is not None and lap > config.max_laps:
                        break
                previous_dist = dist

                actions = driver.drive(state)
                writer.write(_row(tick, state, actions, lap))
                sock.sendto(
                    scr.format_actions(
                        actions["accel"], actions["brake"], actions["steer"],
                        actions["gear"], actions["clutch"],
                    ).encode("ascii"),
                    server,
                )
                tick += 1
        except KeyboardInterrupt:
            if not quiet:
                print("capture interrupted — keeping what was logged", file=sys.stderr)
        finally:
            rows = writer.close()
        if rows == 0:
            (run_dir / "telemetry.csv").unlink(missing_ok=True)
            run_dir.rmdir()
            raise ScrConnectionError(
                f"the server at {config.host}:{config.port} identified us but never "
                "sent a race state — is a race actually running in TORCS?"
            )
        finalize_run(run_dir, f"scr:{config.host}:{config.port}", capture="scr-client")
        return run_dir


def _identify(sock: socket.socket, server: tuple[str, int], config: LiveConfig) -> None:
    hello = scr.init_string(config.bot_id).encode("ascii")
    for _ in range(config.identify_attempts):
        sock.sendto(hello, server)
        try:
            reply = sock.recv(1000).decode("ascii", errors="replace")
        except TimeoutError:
            continue
        if scr.IDENTIFIED in reply:
            return
    raise ScrConnectionError(
        f"no scr_server answered at {config.host}:{config.port} after "
        f"{config.identify_attempts} attempts. Start TORCS with the scr_server robot "
        "in the race (Practice/Quickrace), or see docs/DATA_AVAILABILITY.md §6 for "
        "ways to get a runnable TORCS."
    )


def _row(tick: int, state: dict, actions: dict, lap: int) -> dict:
    def one(tag: str, default: float | None = None) -> float | None:
        value = state.get(tag, default)
        return float(value) if value is not None and not isinstance(value, list) else default

    def many(tag: str, size: int) -> list[float]:
        value = state.get(tag)
        return [float(v) for v in value] if isinstance(value, list) and len(value) == size else []

    speeds = [one("speedX", 0.0), one("speedY", 0.0), one("speedZ", 0.0)]  # km/h on the wire
    total = (speeds[0] ** 2 + speeds[1] ** 2 + speeds[2] ** 2) ** 0.5 / 3.6
    wheels = many("wheelSpinVel", 4)  # FR, FL, RR, RL — car.h:40-43
    beams = many("track", 19)
    opponents = many("opponents", 36)

    row = {
        "sim_time_s": round(tick * TICK_S, 3),
        "wall_time_s": round(monotonic(), 3),
        "dist_from_start_m": one("distFromStart"),
        "dist_raced_m": one("distRaced"),
        "race_lap": lap,
        "cur_lap_time_s": one("curLapTime"),
        "last_lap_time_s": one("lastLapTime"),
        "total_speed_mps": round(total, 4),
        "speed_body_x_mps": round(speeds[0] / 3.6, 4),
        "speed_body_y_mps": round(speeds[1] / 3.6, 4),
        "speed_body_z_mps": round(speeds[2] / 3.6, 4),
        "angle_rad": one("angle"),
        "track_pos": one("trackPos"),
        "damage": one("damage"),
        "fuel_l": one("fuel"),
        "gear": one("gear"),
        "engine_rpm": one("rpm"),
        "race_pos": one("racePos"),
        "pos_z_m": one("z"),
        "opp_nearest_m": round(min(opponents), 1) if opponents else None,
        "accel_cmd": actions["accel"],
        "brake_cmd": actions["brake"],
        "steer_cmd": actions["steer"],
        "gear_cmd": actions["gear"],
        "clutch_cmd": actions["clutch"],
    }
    for name, value in zip(("fr", "fl", "rr", "rl"), wheels, strict=False):
        row[f"{name}_spin_vel_rad_s"] = value
    for i, beam in enumerate(beams):
        row[f"track_beam_{i:02d}_m"] = beam
    return row


class _RowWriter:
    """Fixed-header buffered CSV writer — cheap enough for the 20 ms loop."""

    def __init__(self, path: Path) -> None:
        self._handle = open(path, "w", encoding="utf-8", newline="")
        self._writer = csv.DictWriter(self._handle, fieldnames=FIELDS, restval="")
        self._writer.writeheader()
        self._buffer: list[dict] = []
        self._rows = 0

    def write(self, row: dict) -> None:
        self._buffer.append(row)
        self._rows += 1
        if len(self._buffer) >= FLUSH_ROWS:
            self._flush()

    def _flush(self) -> None:
        self._writer.writerows(self._buffer)
        self._buffer.clear()

    def close(self) -> int:
        self._flush()
        self._handle.close()
        return self._rows
