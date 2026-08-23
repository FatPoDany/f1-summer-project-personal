"""Historical: the synthetic sample session this project shipped until 2026-08-23.

Kept for provenance. It no longer writes the bundled sample -- that is now five
real laps recorded by study participant 0822 (see ``f1coach_core.sample``), and
running this script would overwrite them with data nobody drove. The generator
is preserved because published figures and notes predating the change were made
against its output.

Generate the bundled sample session: three synthetic laps of one ~4.3 km circuit.

    lap_01  banker  — tidy but conservative: earlier braking, careful corner speed
    lap_02  best    — the reference pace
    lap_03  ragged  — overslowed T1 and T6, less commitment everywhere

Speed profile = the classic two-pass method on a 1 m distance grid (accelerate
forward under a traction/power/drag envelope, brake backward at a fixed limit),
run over two consecutive laps so the flying start matches the finish, then
resampled to 50 Hz. Not physics — just honest-looking enough that the Garage
has deltas and coaching has a story, without credentials, network, or a sim.

Deterministic. It used to rewrite src/f1coach_core/data/sample_session/*.csv in
place; OUT_DIR now points at a scratch directory so that cannot happen by
accident.
"""

from pathlib import Path

import numpy as np
import pandas as pd

# Not the bundled sample any more -- see the module docstring.
OUT_DIR = Path(__file__).resolve().parents[1] / ".runtime" / "synthetic-sample-session"

RATE_HZ = 50
DS = 1.0  # distance grid step (m)
V_TOP = 92.0  # asymptotic top speed (m/s), drag-limited
A_TRACTION = 13.0  # m/s^2, low-speed acceleration cap
POWER = 700.0  # power/mass proxy: a_engine = POWER / v
K_DRAG = POWER / V_TOP**3  # drag exactly cancels engine force at V_TOP
A_BRAKE = 34.0  # m/s^2

# (length m, corner speed cap in m/s or None for a straight, steer direction)
SEGMENTS = [
    (700, None, 0),  # start/finish straight
    (110, 26.0, +1),  # T1 hairpin — the heavy stop
    (260, None, 0),
    (300, 44.0, -1),  # T2-T3 esses (one arc as far as the profile cares)
    (480, None, 0),
    (150, 36.0, +1),  # T4
    (420, None, 0),
    (330, 64.0, -1),  # T5 fast sweep
    (110, 22.0, +1),  # T6 slowest corner
    (780, None, 0),  # back straight
    (150, 31.0, -1),  # T7 chicane
    (180, 42.0, +1),  # T8 onto the pit straight
    (320, None, 0),  # run to the line
]

# name -> (global corner-speed scale, braking limit m/s^2, per-segment cap overrides)
LAPS: dict[str, tuple[float, float, dict[int, float]]] = {
    "lap_01.csv": (0.97, 30.0, {}),  # banker
    "lap_02.csv": (1.00, 34.0, {}),  # best
    "lap_03.csv": (0.94, 30.5, {1: 0.84, 8: 0.86}),  # ragged: overslowed T1 + T6
}


def engine_envelope(v: float) -> float:
    """Achievable forward acceleration at speed v: traction, then power, minus drag."""
    return min(A_TRACTION, POWER / max(v, 10.0)) - K_DRAG * v**2


def speed_profile(
    corner_scale: float, a_brake: float, overrides: dict[int, float]
) -> tuple[np.ndarray, np.ndarray]:
    """Return (v, steer_shape) on the 1 m distance grid for one lap."""
    n = int(sum(seg[0] for seg in SEGMENTS) / DS)
    s = np.arange(n) * DS
    cap = np.full(n, V_TOP)
    steer_shape = np.zeros(n)
    edge = 0.0
    for index, (length, v_cap, direction) in enumerate(SEGMENTS):
        if v_cap is not None:
            scaled = v_cap * corner_scale * overrides.get(index, 1.0)
            sel = (s >= edge) & (s < edge + length)
            cap[sel] = scaled
            steer_shape[sel] = direction * (1.0 - scaled / V_TOP)  # tighter corner, more lock
        edge += length

    cap2 = np.tile(cap, 2)  # two laps: lap 2 starts at lap 1's finishing speed
    v = np.empty_like(cap2)
    v[0] = cap2[0] * 0.6
    for i in range(1, v.size):  # forward pass: accelerate when possible
        a = max(engine_envelope(float(v[i - 1])), 0.0)
        v[i] = min(cap2[i], np.sqrt(v[i - 1] ** 2 + 2 * a * DS))
    for i in range(v.size - 2, -1, -1):  # backward pass: brake in time for each cap
        v[i] = min(v[i], np.sqrt(v[i + 1] ** 2 + 2 * a_brake * DS))
    return v[n:], steer_shape


def resample(v: np.ndarray, steer_shape: np.ndarray, a_brake: float) -> pd.DataFrame:
    """Distance grid -> 50 Hz time samples with driver-input channels."""
    n = v.size
    s = np.arange(n) * DS
    t_grid = np.concatenate(([0.0], np.cumsum(2 * DS / (v[:-1] + v[1:]))))
    t = np.arange(0.0, t_grid[-1], 1.0 / RATE_HZ)
    dist = np.interp(t, t_grid, s)
    speed = np.interp(t, t_grid, v)

    steer = np.interp(t, t_grid, steer_shape)
    k = int(0.6 * RATE_HZ) | 1  # ~0.6 s smoothing: hands, not step functions
    steer = np.convolve(steer, np.ones(k) / k, mode="same").clip(-1, 1)

    accel = np.gradient(speed, t)
    demand = accel + K_DRAG * speed**2  # what engine (or brakes) must supply
    envelope = np.maximum(np.minimum(A_TRACTION, POWER / np.maximum(speed, 10.0)), 1.0)
    throttle = np.clip(demand / envelope, 0.0, 1.0)
    brake = np.clip(-demand / a_brake, 0.0, 1.0)

    shift_points = np.array([25.0, 35.0, 45.0, 55.0, 65.0, 74.0, 83.0])  # m/s
    gear = 1 + np.digitize(speed, shift_points)

    lap_length = n * DS
    sector = 1 + np.minimum((dist / (lap_length / 3)).astype(int), 2)

    return pd.DataFrame(
        {
            "t": t.round(3),
            "dist": dist.round(2),
            "speed": speed.round(3),
            "throttle": throttle.round(3),
            "brake": brake.round(3),
            "steer": steer.round(3),
            "gear": gear.astype(int),
            "sector": sector.astype(int),
        }
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for stale in OUT_DIR.glob("*.csv"):
        stale.unlink()
    for name, (corner_scale, a_brake, overrides) in LAPS.items():
        v, steer_shape = speed_profile(corner_scale, a_brake, overrides)
        df = resample(v, steer_shape, a_brake)
        with open(OUT_DIR / name, "w", encoding="utf-8", newline="") as fh:
            fh.write("# schema_version: 1\n")
            fh.write("# synthetic lap from scripts/make_sample_session.py — not real sim data\n")
            df.to_csv(fh, index=False)
        print(
            f"wrote {name}: {len(df)} samples @ {RATE_HZ} Hz · "
            f"lap {df['t'].iloc[-1]:.3f} s · {df['dist'].iloc[-1] / 1000:.3f} km · "
            f"top {df['speed'].max() * 3.6:.0f} km/h"
        )


if __name__ == "__main__":
    main()
