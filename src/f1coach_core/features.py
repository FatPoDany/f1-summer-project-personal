"""Features stage: corner detection and the lap-vs-reference evidence summary.

The evidence summary is the single input every coach provider receives (and
the ground truth the UI's evidence-zoom points back into). Everything is
computed on a common distance grid so the two laps are compared at the same
points on the road, not the same points in time.
"""

import numpy as np

from f1coach_core.lap import Lap

GRID_STEP = 5.0  # metres
SMOOTH_WINDOW = 5  # grid points (~25 m) of speed smoothing before minima search
MINIMA_WINDOW = 12  # +/- grid points (~60 m) a corner apex must be the minimum of
PROMINENCE_WINDOW = 40  # +/- grid points (~200 m) used for the prominence check
MIN_PROMINENCE = 6.0  # m/s a dip must stand out by to count as a corner
CLUSTER_GAP = 100.0  # metres between apex candidates before a new corner starts
SIDE_WINDOW = 50  # grid points (~250 m) checked on each side of an apex
MIN_DIP = 3.0  # m/s the speed must rise by on BOTH sides — rejects plateau shoulders
ZONE_BEFORE = 250.0  # metres of approach (braking) included in a corner zone
ZONE_AFTER = 200.0  # metres of exit included in a corner zone
BRAKE_THRESHOLD = 0.2
THROTTLE_THRESHOLD = 0.5


def _increasing(dist: np.ndarray, *channels: np.ndarray) -> tuple[np.ndarray, ...]:
    """Keep only strictly-increasing distance samples (np.interp needs them)."""
    keep = np.concatenate(([True], np.diff(dist) > 0))
    return (dist[keep], *[channel[keep] for channel in channels])


def _on_grid(lap: Lap, grid: np.ndarray) -> dict[str, np.ndarray]:
    df = lap.df
    dist, t, speed, brake, throttle = _increasing(
        df["dist"].to_numpy(dtype=float),
        df["t"].to_numpy(dtype=float),
        df["speed"].to_numpy(dtype=float),
        df["brake"].to_numpy(dtype=float),
        df["throttle"].to_numpy(dtype=float),
    )
    return {
        "t": np.interp(grid, dist, t),
        "speed": np.interp(grid, dist, speed),
        "brake": np.interp(grid, dist, brake),
        "throttle": np.interp(grid, dist, throttle),
    }


def detect_corners(reference: Lap) -> list[dict]:
    """Corner zones from the reference lap's speed profile.

    A corner is a prominent local minimum of smoothed speed; its zone spans
    the braking approach and the exit, clipped halfway to its neighbours.
    Returns [{"corner": "T1", "apex_m": float, "span_m": [d0, d1]}, ...].
    """
    dist, speed = _increasing(
        reference.df["dist"].to_numpy(dtype=float),
        reference.df["speed"].to_numpy(dtype=float),
    )[0:2]
    grid = np.arange(dist[0], dist[-1], GRID_STEP)
    v = np.interp(grid, dist, speed)
    kernel = np.ones(SMOOTH_WINDOW) / SMOOTH_WINDOW
    pad = SMOOTH_WINDOW // 2  # edge-padded: zero padding would fake dips at the line
    v = np.convolve(np.pad(v, pad, mode="edge"), kernel, mode="valid")
    n = v.size

    candidates = []
    for i in range(n):
        lo, hi = max(0, i - MINIMA_WINDOW), min(n, i + MINIMA_WINDOW + 1)
        if v[i] > v[lo:hi].min() + 0.05:
            continue
        plo, phi = max(0, i - PROMINENCE_WINDOW), min(n, i + PROMINENCE_WINDOW + 1)
        if v[plo:phi].max() - v[i] >= MIN_PROMINENCE:
            candidates.append(i)

    clusters: list[list[int]] = []
    for i in candidates:
        if clusters and (grid[i] - grid[clusters[-1][-1]]) <= CLUSTER_GAP:
            clusters[-1].append(i)
        else:
            clusters.append([i])
    apexes = []
    for cluster in clusters:
        v_min = min(v[i] for i in cluster)
        lowest = [i for i in cluster if v[i] <= v_min + 0.05]
        apex = lowest[len(lowest) // 2]
        # a corner dips and recovers; shoulders straight into a slower corner
        # (and the lap's first/last samples) only fall on one side
        left_max = v[max(0, apex - SIDE_WINDOW) : apex + 1].max()
        right_max = v[apex : min(n, apex + SIDE_WINDOW + 1)].max()
        if left_max >= v[apex] + MIN_DIP and right_max >= v[apex] + MIN_DIP:
            apexes.append(apex)

    corners = []
    for k, apex in enumerate(apexes):
        apex_m = float(grid[apex])
        left = float(grid[apexes[k - 1]] + (apex_m - grid[apexes[k - 1]]) / 2) if k else 0.0
        if k + 1 < len(apexes):
            right = float(apex_m + (grid[apexes[k + 1]] - apex_m) / 2)
        else:
            right = float(grid[-1])
        corners.append(
            {
                "corner": f"T{k + 1}",
                "apex_m": round(apex_m, 1),
                "span_m": [
                    round(max(left, apex_m - ZONE_BEFORE), 1),
                    round(min(right, apex_m + ZONE_AFTER), 1),
                ],
            }
        )
    return corners


def time_delta(lap: Lap, reference: Lap) -> tuple[np.ndarray, np.ndarray]:
    """Cumulative time delta over distance (positive = lap behind reference).

    The Compare view's trace, and the basis of every per-zone time_lost_s.
    """
    end = min(float(lap.df["dist"].iloc[-1]), float(reference.df["dist"].iloc[-1]))
    if end < 20 * GRID_STEP:
        raise ValueError("laps are too short to compare")
    grid = np.arange(0.0, end, GRID_STEP)
    return grid, _on_grid(lap, grid)["t"] - _on_grid(reference, grid)["t"]


def build_evidence_summary(lap: Lap, reference: Lap) -> dict:
    """Everything a coach provider is allowed to know, as one JSON-ready dict."""
    grid, delta = time_delta(lap, reference)
    mine = _on_grid(lap, grid)
    ref = _on_grid(reference, grid)

    corners = []
    for zone in detect_corners(reference):
        d0, d1 = zone["span_m"]
        i0 = int(np.searchsorted(grid, d0))
        i1 = min(int(np.searchsorted(grid, d1)), grid.size - 1)
        apex = int(np.searchsorted(grid, zone["apex_m"]))
        if i1 <= i0:
            continue
        corners.append(
            {
                **zone,
                "min_speed_kmh": round(float(mine["speed"][i0:i1].min()) * 3.6, 1),
                "ref_min_speed_kmh": round(float(ref["speed"][i0:i1].min()) * 3.6, 1),
                "brake_point_m": _first_above(grid, mine["brake"], i0, apex, BRAKE_THRESHOLD),
                "ref_brake_point_m": _first_above(grid, ref["brake"], i0, apex, BRAKE_THRESHOLD),
                "throttle_point_m": _first_above(
                    grid, mine["throttle"], apex, i1, THROTTLE_THRESHOLD
                ),
                "ref_throttle_point_m": _first_above(
                    grid, ref["throttle"], apex, i1, THROTTLE_THRESHOLD
                ),
                "time_lost_s": round(float(delta[i1] - delta[i0]), 3),
            }
        )

    return {
        "lap": {"name": lap.source.stem, "lap_time_s": round(lap.lap_time, 3)},
        "reference": {"name": reference.source.stem, "lap_time_s": round(reference.lap_time, 3)},
        "total_delta_s": round(lap.lap_time - reference.lap_time, 3),
        "corners": corners,
    }


def _first_above(
    grid: np.ndarray, channel: np.ndarray, i0: int, i1: int, threshold: float
) -> float | None:
    """Distance where the channel first crosses the threshold in [i0, i1)."""
    hits = np.flatnonzero(channel[i0:i1] >= threshold)
    return round(float(grid[i0 + hits[0]]), 1) if hits.size else None


EXIT_OFFSET = 200.0  # metres past the apex where exit speed is read (the mockup's column)


def corner_table(lap: Lap, reference: Lap) -> list[dict]:
    """Per-corner comparison rows for the Analysis screen's corner table.

    The evidence summary's corner zones plus exit speeds, one row per corner:
    brake point, minimum speed, exit speed at apex+200 m (mine vs reference)
    and the time gained/lost across the zone. Purely presentational — the
    coach prompt contract (build_evidence_summary) is untouched.
    """
    grid, delta = time_delta(lap, reference)
    mine = _on_grid(lap, grid)
    ref = _on_grid(reference, grid)
    rows = []
    for zone in detect_corners(reference):
        d0, d1 = zone["span_m"]
        i0 = int(np.searchsorted(grid, d0))
        i1 = min(int(np.searchsorted(grid, d1)), grid.size - 1)
        apex = int(np.searchsorted(grid, zone["apex_m"]))
        if i1 <= i0:
            continue
        exit_i = min(int(np.searchsorted(grid, zone["apex_m"] + EXIT_OFFSET)), grid.size - 1)
        rows.append(
            {
                **zone,
                "brake_point_m": _first_above(grid, mine["brake"], i0, apex, BRAKE_THRESHOLD),
                "ref_brake_point_m": _first_above(grid, ref["brake"], i0, apex, BRAKE_THRESHOLD),
                "min_speed_kmh": round(float(mine["speed"][i0:i1].min()) * 3.6, 1),
                "ref_min_speed_kmh": round(float(ref["speed"][i0:i1].min()) * 3.6, 1),
                "exit_speed_kmh": round(float(mine["speed"][exit_i]) * 3.6, 1),
                "ref_exit_speed_kmh": round(float(ref["speed"][exit_i]) * 3.6, 1),
                "delta_s": round(float(delta[i1] - delta[i0]), 3),
            }
        )
    return rows
