"""Path B live capture: drive the car over the Granite/SCR bridge, log every tick into the
run store. After `finalize_run` the result is indistinguishable from an
imported exporter run, so analyze/coach/report work unchanged.

Logging discipline (spec: "must not slow the simulation loop"): the hot loop
does parse -> drive -> send before buffered CSV logging and advisory sampling;
rows flush to disk every FLUSH_ROWS. Units are normalized to SI at logging time (SCR
speeds arrive in km/h). `sim_time_s` uses the TORCS 1.3.9 game clock when the
Granite Bridge extension supplies it, with the fixed 20 ms cadence as a
backward-compatible fallback; wall_time_s is also kept.
"""

import csv
import math
import select
import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Protocol

from racecoach.control.simple_driver import SimpleDriver
from racecoach.telemetry import scr
from racecoach.telemetry.run_store import finalize_run, new_run_dir

TICK_S = 0.02  # the TORCS robot callback normally runs every 20 ms of game time
FLUSH_ROWS = 250
LAP_RESET_M = 100.0  # distFromStart dropping this far back means a new lap
FINAL_LAP_MIN_COVERAGE = 0.98

FIELDS = (
    "sim_time_s", "wall_time_s", "dist_from_start_m", "dist_raced_m", "race_lap",
    "race_finished", "capture_lap_closed",
    "cur_lap_time_s", "last_lap_time_s", "total_speed_mps",
    "speed_body_x_mps", "speed_body_y_mps", "speed_body_z_mps",
    "angle_rad", "track_pos", "damage", "fuel_l", "gear", "engine_rpm",
    "race_pos", "pos_z_m",
    "fr_spin_vel_rad_s", "fl_spin_vel_rad_s", "rr_spin_vel_rad_s", "rl_spin_vel_rad_s",
    "fr_tire_wear", "fl_tire_wear", "rr_tire_wear", "rl_tire_wear",
    "fr_tire_temp_c", "fl_tire_temp_c", "rr_tire_temp_c", "rl_tire_temp_c",
    "fr_tire_pressure_kpa", "fl_tire_pressure_kpa",
    "rr_tire_pressure_kpa", "rl_tire_pressure_kpa",
    "fr_tire_graining", "fl_tire_graining", "rr_tire_graining", "rl_tire_graining",
    "opp_nearest_m",
    *(f"track_beam_{i:02d}_m" for i in range(19)),
    "accel_cmd", "brake_cmd", "steer_cmd", "gear_cmd", "clutch_cmd",
)


class ScrConnectionError(RuntimeError):
    """No compatible TORCS UDP bridge answered. The message says how to get one."""


class CaptureCancelled(RuntimeError):
    """A caller requested a clean stop before a usable run could be saved."""


class TickAdvisor(Protocol):
    """A non-blocking observer attached to the deterministic control loop."""

    def start(self, run_dir: Path) -> None: ...

    def observe(self, tick: int, state: dict, actions: dict, lap: int) -> None: ...

    def close(self) -> None: ...


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
    max_idle_s: float | None = 30.0

    def __post_init__(self) -> None:
        if not self.host:
            raise ValueError("SCR host must not be empty")
        if (
            not isinstance(self.port, int)
            or isinstance(self.port, bool)
            or not 1 <= self.port <= 65535
        ):
            raise ValueError("SCR port must be an integer from 1 to 65535")
        if (
            not isinstance(self.max_ticks, int)
            or isinstance(self.max_ticks, bool)
            or self.max_ticks <= 0
        ):
            raise ValueError("SCR max_ticks must be a positive integer")
        if self.max_laps is not None and (
            not isinstance(self.max_laps, int)
            or isinstance(self.max_laps, bool)
            or self.max_laps <= 0
        ):
            raise ValueError("SCR max_laps must be a positive integer or null")
        if (
            not isinstance(self.identify_attempts, int)
            or isinstance(self.identify_attempts, bool)
            or self.identify_attempts <= 0
        ):
            raise ValueError("SCR identify_attempts must be a positive integer")
        if (
            not isinstance(self.timeout_s, (int, float))
            or isinstance(self.timeout_s, bool)
            or not math.isfinite(float(self.timeout_s))
            or self.timeout_s <= 0
        ):
            raise ValueError("SCR timeout_s must be positive and finite")
        if self.max_idle_s is not None and (
            not isinstance(self.max_idle_s, (int, float))
            or isinstance(self.max_idle_s, bool)
            or not math.isfinite(float(self.max_idle_s))
            or self.max_idle_s <= 0
        ):
            raise ValueError("SCR max_idle_s must be positive and finite or null")


def capture_run(
    config: LiveConfig,
    driver: SimpleDriver | None = None,
    quiet: bool = False,
    advisor: TickAdvisor | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> Path:
    """Identify, drive, log, and optionally observe; return the finalized run directory.

    An advisor must make ``observe`` non-blocking. Granite uses a one-item
    background queue so model latency can never delay a 20 ms SCR response.
    """
    if _stop_requested(stop_requested):
        raise CaptureCancelled("live capture was stopped before connecting")
    driver = driver or SimpleDriver()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(config.timeout_s)
        server = (config.host, config.port)
        sock.connect(server)  # kernel rejects datagrams from any foreign local process
        _identify(sock, config, stop_requested)

        run_dir = new_run_dir(config.run_name)
        writer = _RowWriter(run_dir / "telemetry.csv")
        tick, lap, previous_dist = 0, 1, None
        observed_track_m: float | None = None
        lap_peak_m = 0.0
        close_last_lap = False
        last_state_at = monotonic()
        advisor_started = False
        advisor_error_reported = False
        try:
            if advisor is not None:
                advisor.start(run_dir)
                advisor_started = True
            while tick < config.max_ticks and not _stop_requested(stop_requested):
                stop_after_sample = False
                try:
                    message = _receive_latest(sock)
                except TimeoutError:
                    if _stop_requested(stop_requested):
                        break
                    if _idle_expired(last_state_at, config.max_idle_s):
                        if not quiet:
                            print(
                                "TORCS bridge stopped sending telemetry; stopping safely",
                                file=sys.stderr,
                            )
                        break
                    continue
                if scr.SHUTDOWN in message:
                    close_last_lap = _shutdown_closes_expected_lap(
                        config.max_laps,
                        lap,
                        previous_dist,
                        observed_track_m,
                    )
                    break
                if scr.RESTART in message:
                    lap, previous_dist = 1, None
                    observed_track_m = None
                    lap_peak_m = 0.0
                    continue
                state = scr.parse_state(message)
                if "distFromStart" not in state:
                    if _idle_expired(last_state_at, config.max_idle_s):
                        if not quiet:
                            print(
                                "TORCS bridge sent no valid telemetry; stopping safely",
                                file=sys.stderr,
                            )
                        break
                    continue  # not a state packet
                last_state_at = monotonic()
                dist = float(state["distFromStart"])
                crossed_start_line = (
                    previous_dist is not None and previous_dist - dist > LAP_RESET_M
                )
                if crossed_start_line:
                    completed_peak_m = max(lap_peak_m, previous_dist)
                    observed_track_m = max(observed_track_m or 0.0, completed_peak_m)
                    lap_peak_m = dist
                else:
                    lap_peak_m = max(lap_peak_m, dist)
                torcs_lap = _reported_race_lap(state)
                previous_lap = lap
                if torcs_lap is not None:
                    # TORCS starts at lap 0 on the grid, changes to 1 at the
                    # start line, and marks the finish while changing N to
                    # N+1.  Display grid telemetry as lap 1, but never count
                    # that first crossing as a completed lap.
                    lap = max(1, torcs_lap)
                    stop_after_sample = (
                        _state_flag(state, "raceFinished")
                        or config.max_laps is not None
                        and torcs_lap > config.max_laps
                    )
                elif crossed_start_line:
                    # Backward compatibility for stock SCR servers which do
                    # not expose TORCS' authoritative race counter.
                    lap += 1
                    if config.max_laps is not None and lap > config.max_laps:
                        stop_after_sample = True
                previous_dist = dist

                # When TORCS advances the counter on a finish-line reset,
                # keep that closing sample attached to lap N so metadata does
                # not expose a one-sample phantom lap. Some race modes instead
                # finish at the end of lap N without advancing the counter.
                recorded_lap = (
                    previous_lap if stop_after_sample and lap > previous_lap else lap
                )

                # The finish packet is also the start-line reset that closes the
                # final lap.  Persist it before stopping, otherwise the session
                # importer can only classify that lap as a cut-off fragment.
                actions = (
                    {"accel": 0.0, "brake": 1.0, "steer": 0.0, "gear": 1, "clutch": 0.0}
                    if stop_after_sample
                    else driver.drive(state)
                )
                # Controls have the deadline. Telemetry I/O and advisory sampling
                # deliberately happen only after the action is on the wire.
                sock.send(_action_message(actions, advisor).encode("ascii"))
                row = _row(tick, state, actions, recorded_lap)
                if stop_after_sample:
                    row["capture_lap_closed"] = 1
                writer.write(row)
                if advisor is not None:
                    try:
                        advisor.observe(tick, state, actions, recorded_lap)
                    except Exception as exc:
                        if not quiet and not advisor_error_reported:
                            print(
                                f"live advisor disabled this sample: {type(exc).__name__}: {exc}",
                                file=sys.stderr,
                            )
                            advisor_error_reported = True
                tick += 1
                if stop_after_sample:
                    break
        except KeyboardInterrupt:
            if not quiet:
                print("capture interrupted — keeping what was logged", file=sys.stderr)
        finally:
            try:
                try:
                    sock.send(scr.format_actions(0.0, 1.0, 0.0, 1, 0.0).encode("ascii"))
                except OSError:
                    pass
                rows = writer.close(mark_last_lap_closed=close_last_lap)
            finally:
                if advisor is not None and advisor_started:
                    advisor.close()
        if rows == 0:
            (run_dir / "telemetry.csv").unlink(missing_ok=True)
            coaching_dir = run_dir / "coaching"
            if coaching_dir.is_dir() and not any(coaching_dir.iterdir()):
                coaching_dir.rmdir()
            run_dir.rmdir()
            if _stop_requested(stop_requested):
                raise CaptureCancelled("live capture stopped before the first telemetry sample")
            raise ScrConnectionError(
                f"the server at {config.host}:{config.port} identified us but never "
                "sent a race state — is a race actually running in TORCS?"
            )
        finalize_run(run_dir, f"scr:{config.host}:{config.port}", capture="scr-client")
        return run_dir


def _identify(
    sock: socket.socket,
    config: LiveConfig,
    stop_requested: Callable[[], bool] | None = None,
) -> None:
    hello = scr.init_string(config.bot_id).encode("ascii")
    for _ in range(config.identify_attempts):
        if _stop_requested(stop_requested):
            raise CaptureCancelled("live capture was stopped while connecting")
        sock.send(hello)
        try:
            reply = sock.recv(scr.MAX_DATAGRAM_BYTES).decode("ascii", errors="replace")
        except (TimeoutError, ConnectionRefusedError, ConnectionResetError):
            # A connected UDP socket reports an ICMP "port unreachable" as
            # WSAECONNRESET on Windows.  It means the same thing as a timeout
            # here: no bridge answered this identification attempt.
            continue
        if scr.IDENTIFIED in reply:
            return
    raise ScrConnectionError(
        f"no TORCS Granite/SCR bridge answered at {config.host}:{config.port} after "
        f"{config.identify_attempts} attempts. Start TORCS with the Granite Bridge robot "
        "in a Practice or Quick Race session; see integrations/torcs-1.3.9/README.md."
    )


def _stop_requested(callback: Callable[[], bool] | None) -> bool:
    return callback is not None and callback()


def _receive_latest(sock: socket.socket) -> str:
    """Wait for one bridge packet, then discard any queued older packets."""
    message = sock.recv(scr.MAX_DATAGRAM_BYTES)
    while select.select((sock,), (), (), 0.0)[0]:
        message = sock.recv(scr.MAX_DATAGRAM_BYTES)
    return message.decode("ascii", errors="replace")


def _action_message(actions: dict, advisor: TickAdvisor | None) -> str:
    """Format controls first; an invalid display value can never suppress them."""
    try:
        hud_advice = getattr(advisor, "hud_advice", None) if advisor is not None else None
    except Exception:
        hud_advice = None
    try:
        return scr.format_actions(
            actions["accel"],
            actions["brake"],
            actions["steer"],
            actions["gear"],
            actions["clutch"],
            hud_advice=hud_advice,
        )
    except (TypeError, ValueError):
        return scr.format_actions(
            actions["accel"],
            actions["brake"],
            actions["steer"],
            actions["gear"],
            actions["clutch"],
        )


def _idle_expired(last_state_at: float, max_idle_s: float | None) -> bool:
    return max_idle_s is not None and monotonic() - last_state_at >= max_idle_s


def _reported_race_lap(state: dict) -> int | None:
    """Return the bridge's authoritative TORCS counter when it is well formed."""
    value = state.get("raceLap")
    if isinstance(value, list) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        return None
    return int(number)


def _state_flag(state: dict, key: str) -> bool:
    value = state.get(key)
    if isinstance(value, list) or value is None:
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number >= 0.5


def _shutdown_closes_expected_lap(
    max_laps: int | None,
    lap: int,
    last_dist_m: float | None,
    observed_track_m: float | None,
) -> bool:
    """Recognise a race-end shutdown without treating an abort as a completed lap.

    Some TORCS race modes send ``***shutdown***`` at the line without a final
    ``raceFinished`` state or distance reset.  The shutdown closes a lap only
    when the configured final lap is active and its sampled distance covers a
    previously observed start-line-to-start-line track length.
    """
    return bool(
        max_laps is not None
        and lap == max_laps
        and last_dist_m is not None
        and observed_track_m is not None
        and observed_track_m > 0.0
        and last_dist_m >= FINAL_LAP_MIN_COVERAGE * observed_track_m
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
    tire_wear = many("tireWear", 4)  # 0 new, 1 fully worn — TORCS 1.3.9 car.h
    tire_temp = many("tireTempC", 4)
    tire_pressure = many("tirePressureKPa", 4)
    tire_graining = many("tireGraining", 4)
    beams = many("track", 19)
    opponents = many("opponents", 36)

    row = {
        "sim_time_s": round(one("simTime", tick * TICK_S), 3),
        "wall_time_s": round(monotonic(), 3),
        "dist_from_start_m": one("distFromStart"),
        "dist_raced_m": one("distRaced"),
        "race_lap": lap,
        "race_finished": int(_state_flag(state, "raceFinished")),
        # Deterministic capture boundary; unlike race_finished this is not a raw
        # TORCS field. It can be promoted when a trusted stop condition closes
        # the final sample.
        "capture_lap_closed": 0,
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
    for channel, values in (
        ("tire_wear", tire_wear),
        ("tire_temp_c", tire_temp),
        ("tire_pressure_kpa", tire_pressure),
        ("tire_graining", tire_graining),
    ):
        for name, value in zip(("fr", "fl", "rr", "rl"), values, strict=False):
            row[f"{name}_{channel}"] = value
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
        self._pending: dict | None = None
        self._rows = 0

    def write(self, row: dict) -> None:
        if self._pending is not None:
            self._buffer.append(self._pending)
        self._pending = row
        self._rows += 1
        if len(self._buffer) >= FLUSH_ROWS:
            self._flush()

    def _flush(self) -> None:
        self._writer.writerows(self._buffer)
        self._buffer.clear()

    def close(self, *, mark_last_lap_closed: bool = False) -> int:
        if self._pending is not None:
            if mark_last_lap_closed:
                self._pending["capture_lap_closed"] = 1
            self._buffer.append(self._pending)
            self._pending = None
        self._flush()
        self._handle.close()
        return self._rows
