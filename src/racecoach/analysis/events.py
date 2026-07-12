"""Rule-based event detection over a raw exporter run.

Each detector declares the columns it needs and is skipped with a readable
note when they are absent — a degraded run still analyses as far as its
channels allow. Every event carries lap, time, distance span, a 0–1
severity, and the numbers behind it: these are the citable facts the LLM
layer must ground its coaching in.
"""

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from racecoach.analysis import channels as ch

OFF_TRACK_LIMIT = 1.0  # |track_pos| beyond this means outside the track edge
PEDAL_OVERLAP_MIN = 0.1  # both pedals past this at once counts as overlap
STEER_RATE_LIMIT = 4.0  # |d steer_cmd / dt| (full-locks per second) that reads as a jerk
LOCKUP_BRAKE_MIN = 0.8
LOCKUP_SPIN_MAX_RAD_S = 1.0
LOCKUP_SPEED_MIN_MPS = 5.0
MIN_EVENT_SAMPLES = 3  # debounce: shorter blips are noise, not events

WHEEL_SPIN_COLUMNS = (
    "fr_spin_vel_rad_s",
    "fl_spin_vel_rad_s",
    "rr_spin_vel_rad_s",
    "rl_spin_vel_rad_s",
)


@dataclass(frozen=True)
class Event:
    event_id: str  # e.g. "off_track-2" — stable citation handle for the LLM
    kind: str
    lap: int
    t_start_s: float
    t_end_s: float
    dist_start_m: float
    dist_end_m: float
    severity: float  # 0..1
    summary: str  # one readable sentence with the numbers in it
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def detect_events(df: pd.DataFrame) -> tuple[list[Event], list[str]]:
    """Run every detector whose channels exist. Returns (events, skipped-notes)."""
    events: list[Event] = []
    notes: list[str] = []
    lateral = (  # SCR logs track_pos directly; exporter runs derive it
        ("track_pos",) if "track_pos" in df.columns
        else ("track_to_middle_m", "track_seg_width_m")
    )
    for detector, required in (
        (_off_track, lateral),
        (_collision, ("damage",)),
        (_pedal_overlap, ("accel_cmd", "brake_cmd")),
        (_steering_jerk, ("steer_cmd",)),
        (_wheel_lockup, ("brake_cmd", "total_speed_mps", *WHEEL_SPIN_COLUMNS)),
    ):
        gone = ch.missing(df, ch.TIME, ch.DIST, *required)
        if gone:
            notes.append(f"{detector.__name__.lstrip('_')}: skipped, missing column(s) {gone}")
            continue
        events.extend(detector(df))
    events.sort(key=lambda event: event.t_start_s)
    return events, notes


def _base(df: pd.DataFrame):
    return ch.numeric(df, ch.TIME), ch.numeric(df, ch.DIST), ch.lap_numbers(df)


def _make(kind, index, t, dist, laps, start, end, severity, summary, evidence) -> Event:
    last = end - 1
    return Event(
        event_id=f"{kind}-{index}",
        kind=kind,
        lap=int(laps[start]),
        t_start_s=round(float(t[start]), 3),
        t_end_s=round(float(t[last]), 3),
        dist_start_m=round(float(dist[start]), 1),
        dist_end_m=round(float(dist[last]), 1),
        severity=round(float(min(1.0, severity)), 2),
        summary=summary,
        evidence=evidence,
    )


def _off_track(df: pd.DataFrame) -> list[Event]:
    t, dist, laps = _base(df)
    pos = ch.track_pos(df)
    events = []
    for i, (start, end) in enumerate(
        ch.spans(np.abs(pos) > OFF_TRACK_LIMIT, MIN_EVENT_SAMPLES), start=1
    ):
        worst = float(np.nanmax(np.abs(pos[start:end])))
        side = "left" if float(np.nanmean(pos[start:end])) > 0 else "right"
        events.append(
            _make(
                "off_track", i, t, dist, laps, start, end,
                severity=min(1.0, (worst - OFF_TRACK_LIMIT) / 1.0 + 0.3),
                summary=(
                    f"Off track on the {side} for {t[end - 1] - t[start]:.1f} s "
                    f"(track_pos peaked at {worst:.2f}, edge is 1.0)."
                ),
                evidence={"max_track_pos": round(worst, 3), "side": side},
            )
        )
    return events


def _collision(df: pd.DataFrame) -> list[Event]:
    t, dist, laps = _base(df)
    damage = ch.numeric(df, "damage")
    jumps = np.concatenate(([0.0], np.diff(damage)))
    events = []
    for i, (start, end) in enumerate(ch.spans(jumps > 0, 1), start=1):
        gained = float(np.nansum(jumps[start:end]))
        events.append(
            _make(
                "collision", i, t, dist, laps, start, end,
                severity=min(1.0, gained / 500.0 + 0.2),
                summary=f"Contact: damage +{gained:.0f} (total {damage[end - 1]:.0f}).",
                evidence={"damage_delta": round(gained, 1), "damage_after": float(damage[end - 1])},
            )
        )
    return events


def _pedal_overlap(df: pd.DataFrame) -> list[Event]:
    t, dist, laps = _base(df)
    accel = ch.numeric(df, "accel_cmd")
    brake = ch.numeric(df, "brake_cmd")
    both = (accel > PEDAL_OVERLAP_MIN) & (brake > PEDAL_OVERLAP_MIN)
    events = []
    for i, (start, end) in enumerate(ch.spans(both, MIN_EVENT_SAMPLES), start=1):
        overlap = float(np.nanmean(np.minimum(accel[start:end], brake[start:end])))
        events.append(
            _make(
                "pedal_overlap", i, t, dist, laps, start, end,
                severity=min(1.0, overlap + 0.2),
                summary=(
                    f"Throttle and brake pressed together for "
                    f"{t[end - 1] - t[start]:.1f} s (mean overlap {overlap:.2f})."
                ),
                evidence={
                    "mean_overlap": round(overlap, 3),
                    "max_throttle": round(float(np.nanmax(accel[start:end])), 3),
                    "max_brake": round(float(np.nanmax(brake[start:end])), 3),
                },
            )
        )
    return events


def _steering_jerk(df: pd.DataFrame) -> list[Event]:
    t, dist, laps = _base(df)
    steer = ch.numeric(df, "steer_cmd")
    dt = np.diff(t)
    rate = np.concatenate(([0.0], np.abs(np.diff(steer)) / np.where(dt > 0, dt, np.nan)))
    events = []
    # jerks are near-instant: no debounce, but merge samples of one incident
    for i, (start, end) in enumerate(ch.spans(rate > STEER_RATE_LIMIT, 1), start=1):
        peak = float(np.nanmax(rate[start:end]))
        events.append(
            _make(
                "steering_jerk", i, t, dist, laps, start, end,
                severity=min(1.0, peak / (4 * STEER_RATE_LIMIT) + 0.2),
                summary=(
                    f"Abrupt steering input: {peak:.1f} full-locks/s "
                    f"(smooth is < {STEER_RATE_LIMIT:.0f})."
                ),
                evidence={"peak_rate_per_s": round(peak, 2)},
            )
        )
    return events


def _wheel_lockup(df: pd.DataFrame) -> list[Event]:
    t, dist, laps = _base(df)
    brake = ch.numeric(df, "brake_cmd")
    speed = ch.numeric(df, "total_speed_mps")
    slowest = np.nanmin(
        np.column_stack([ch.numeric(df, column) for column in WHEEL_SPIN_COLUMNS]), axis=1
    )
    locked = (brake > LOCKUP_BRAKE_MIN) & (slowest < LOCKUP_SPIN_MAX_RAD_S) & (
        speed > LOCKUP_SPEED_MIN_MPS
    )
    events = []
    for i, (start, end) in enumerate(ch.spans(locked, MIN_EVENT_SAMPLES), start=1):
        at_speed = float(np.nanmax(speed[start:end]))
        events.append(
            _make(
                "wheel_lockup", i, t, dist, laps, start, end,
                severity=min(1.0, at_speed / 60.0 + 0.3),
                summary=(
                    f"Wheel lock-up under braking at {at_speed * 3.6:.0f} km/h "
                    f"for {t[end - 1] - t[start]:.1f} s."
                ),
                evidence={
                    "speed_mps": round(at_speed, 1),
                    "brake_cmd": round(float(np.nanmax(brake[start:end])), 2),
                },
            )
        )
    return events
