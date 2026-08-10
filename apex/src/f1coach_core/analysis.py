"""Lap-level analysis helpers (the seed of the features/analysis lane, M3-M4).

Everything here is computed from the validated Lap dataframe — front ends
render these results, they never recompute them.
"""

import numpy as np

from f1coach_core.lap import Lap


def sector_spans(lap: Lap) -> list[tuple[int, float, float]]:
    """Contiguous sector runs as (sector, dist_start, dist_end), in lap order.

    Empty list when the lap carries no sector channel.
    """
    if "sector" not in lap.df.columns:
        return []
    sec = lap.df["sector"].to_numpy()
    dist = lap.df["dist"].to_numpy(dtype=float)
    edges = np.flatnonzero(np.diff(sec) != 0) + 1
    starts = np.concatenate(([0], edges))
    ends = np.concatenate((edges, [len(sec)]))
    return [
        (int(sec[a]), float(dist[a]), float(dist[b - 1]))
        for a, b in zip(starts, ends, strict=True)
    ]


def sector_times(lap: Lap) -> dict[int, float]:
    """Seconds spent in each sector (summed over runs, robust to stray splits)."""
    if "sector" not in lap.df.columns:
        return {}
    t = lap.df["t"].to_numpy(dtype=float)
    sec = lap.df["sector"].to_numpy()
    edges = np.flatnonzero(np.diff(sec) != 0) + 1
    starts = np.concatenate(([0], edges))
    ends = np.concatenate((edges, [len(sec)]))
    times: dict[int, float] = {}
    for a, b in zip(starts, ends, strict=True):
        # a run covers t[a] .. t[b] (start of the next run, or lap end)
        t_end = t[b] if b < len(t) else t[-1]
        times[int(sec[a])] = times.get(int(sec[a]), 0.0) + float(t_end - t[a])
    return times
