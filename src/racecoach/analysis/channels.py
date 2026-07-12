"""Channel access with graceful degradation.

Detectors declare the exporter columns they need; anything missing skips
that detector with a human-readable note instead of failing the run. The
derivations mirror the SCR server's own formulas so Path A and Path B
produce comparable signals (see docs/DATA_AVAILABILITY.md):

    track_pos = 2 * track_to_middle_m / track_seg_width_m   # ±1 = track edge
    (scr_server.cpp:388)
"""

import numpy as np
import pandas as pd

TIME = "sim_time_s"
DIST = "dist_from_start_m"
LAP = "race_lap"


def missing(df: pd.DataFrame, *columns: str) -> list[str]:
    return [column for column in columns if column not in df.columns]


def numeric(df: pd.DataFrame, column: str) -> np.ndarray:
    return pd.to_numeric(df[column], errors="coerce").to_numpy(dtype=float)


def track_pos(df: pd.DataFrame) -> np.ndarray:
    """Signed lateral position normalized by half track width (±1 = edge)."""
    to_middle = numeric(df, "track_to_middle_m")
    width = numeric(df, "track_seg_width_m")
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(width > 0, 2.0 * to_middle / width, np.nan)


def lap_numbers(df: pd.DataFrame) -> np.ndarray:
    """Per-sample lap number: race_lap when present, else derived from
    dist_from_start_m snapping back toward zero (same rule as f1coach_core.torcs)."""
    if LAP in df.columns:
        return pd.to_numeric(df[LAP], errors="coerce").ffill().to_numpy(dtype=float)
    dist = numeric(df, DIST)
    track_length = np.nanmax(dist)
    resets = np.flatnonzero(np.diff(dist) < -0.5 * track_length) + 1
    laps = np.zeros(len(dist))
    laps[resets] = 1
    return np.cumsum(laps) + 1


def spans(mask: np.ndarray, min_samples: int) -> list[tuple[int, int]]:
    """Contiguous True stretches of at least min_samples, as (start, end) indices
    (end exclusive). NaN-safe: NaN counts as False."""
    clean = np.nan_to_num(mask.astype(float), nan=0.0) > 0
    edges = np.flatnonzero(np.diff(np.concatenate(([0], clean.astype(int), [0]))))
    return [
        (int(start), int(end))
        for start, end in zip(edges[::2], edges[1::2], strict=True)
        if end - start >= min_samples
    ]
