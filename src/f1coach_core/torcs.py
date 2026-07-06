"""Adapter for Lin's TORCS high-frequency exporter (simuv2, stride 10, ~50 Hz).

Field reference: M_Lin/torcs_highfreq_field_cheatsheet.csv (249 columns).
A run file holds a whole outing — grid start, N flying laps, a trailing
fragment — so this module splits the run at start-line crossings (where
dist_from_start_m snaps back to zero) and maps each lap onto the canonical
v1 schema (see schema.py):

    t        <- sim_time_s, rebased to the lap's first sample
    dist     <- dist_from_start_m
    speed    <- total_speed_mps (falls back to speed_body_x_mps)
    throttle <- accel_cmd
    brake    <- brake_cmd
    steer    <- steer_cmd
    gear     <- gear
    sector   <- derived: thirds of the observed track length (TORCS has none)
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from f1coach_core.loader import TelemetrySchemaError
from f1coach_core.schema import SCHEMA_VERSION

SIGNATURE_COLUMNS = (
    "sim_time_s",
    "dist_from_start_m",
    "accel_cmd",
    "brake_cmd",
    "steer_cmd",
    "gear",
)
SPEED_CANDIDATES = ("total_speed_mps", "speed_body_x_mps")
MIN_LAP_SAMPLES = 20


@dataclass(frozen=True)
class TorcsLap:
    """One start-line-to-start-line segment of a TORCS run, in canonical columns."""

    df: pd.DataFrame
    lap_label: int  # race_lap counter (segment ordinal when absent)
    lap_time: float
    car_name: str | None
    complete: bool  # started at the line AND ended by crossing it again


def is_torcs_export(path: str | Path) -> bool:
    """Cheap signature check on the header row only — no full parse."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                header = {c.strip().strip('"') for c in line.split(",")}
                return all(col in header for col in SIGNATURE_COLUMNS)
    except OSError:
        return False
    return False


def split_torcs_run(path: str | Path) -> list[TorcsLap]:
    """Split a run export into laps. Raises TelemetrySchemaError with readable text."""
    path = Path(path)
    try:
        df = pd.read_csv(path, comment="#", low_memory=False)
    except Exception as exc:
        raise TelemetrySchemaError(f"{path.name} is not parseable CSV: {exc}") from exc

    missing = [c for c in SIGNATURE_COLUMNS if c not in df.columns]
    if missing:
        raise TelemetrySchemaError(
            f"{path.name} looks like a TORCS export but is missing {missing}."
        )
    speed_col = next((c for c in SPEED_CANDIDATES if c in df.columns), None)
    if speed_col is None:
        raise TelemetrySchemaError(
            f"{path.name} has none of the speed channels {list(SPEED_CANDIDATES)}."
        )

    dist = pd.to_numeric(df["dist_from_start_m"], errors="coerce").to_numpy(dtype=float)
    track_length = float(np.nanmax(dist))
    if not np.isfinite(track_length) or track_length <= 0:
        raise TelemetrySchemaError(f"{path.name}: dist_from_start_m carries no usable values.")

    # a start-line crossing shows as dist snapping back toward zero
    resets = (np.flatnonzero(np.diff(dist) < -0.5 * track_length) + 1).tolist()
    bounds = [0, *resets, len(df)]

    laps: list[TorcsLap] = []
    for i in range(len(bounds) - 1):
        seg = df.iloc[bounds[i] : bounds[i + 1]]
        if len(seg) < MIN_LAP_SAMPLES:
            continue
        starts_at_line = float(seg["dist_from_start_m"].iloc[0]) < 0.02 * track_length
        ends_at_line = i < len(bounds) - 2  # a following reset closed this segment
        canonical = _to_canonical(seg, speed_col, track_length)
        if "race_lap" in seg.columns:
            lap_label = int(seg["race_lap"].iloc[len(seg) // 2])
        else:
            lap_label = i
        car_name = str(seg["car_name"].iloc[0]) if "car_name" in seg.columns else None
        laps.append(
            TorcsLap(
                df=canonical,
                lap_label=lap_label,
                lap_time=float(canonical["t"].iloc[-1]),
                car_name=car_name,
                complete=starts_at_line and ends_at_line,
            )
        )
    return laps


def write_canonical_lap(lap: TorcsLap, dest: str | Path, source_name: str) -> None:
    """Write one split lap as a canonical v1 CSV with provenance comments."""
    provenance = f"source: torcs:{source_name}"
    if lap.car_name:
        provenance += f" | car: {lap.car_name}"
    provenance += f" | race_lap: {lap.lap_label} | measured_lap_time: {lap.lap_time:.3f}"
    with open(dest, "w", encoding="utf-8", newline="") as fh:
        fh.write(f"# schema_version: {SCHEMA_VERSION}\n")
        fh.write(f"# {provenance}\n")
        fh.write("# sector: derived as thirds of track length (TORCS exports no sectors)\n")
        lap.df.to_csv(fh, index=False)


def _to_canonical(seg: pd.DataFrame, speed_col: str, track_length: float) -> pd.DataFrame:
    t = pd.to_numeric(seg["sim_time_s"], errors="coerce")
    dist = pd.to_numeric(seg["dist_from_start_m"], errors="coerce")
    sector = 1 + np.minimum((dist / (track_length / 3.0)).astype(int), 2)
    return pd.DataFrame(
        {
            "t": (t - t.iloc[0]).round(4).to_numpy(),
            "dist": dist.round(3).to_numpy(),
            "speed": pd.to_numeric(seg[speed_col], errors="coerce").round(4).to_numpy(),
            "throttle": pd.to_numeric(seg["accel_cmd"], errors="coerce").round(4).to_numpy(),
            "brake": pd.to_numeric(seg["brake_cmd"], errors="coerce").round(4).to_numpy(),
            "steer": pd.to_numeric(seg["steer_cmd"], errors="coerce").round(4).to_numpy(),
            "gear": pd.to_numeric(seg["gear"], errors="coerce").to_numpy(),
            "sector": sector.to_numpy(),
        }
    )
