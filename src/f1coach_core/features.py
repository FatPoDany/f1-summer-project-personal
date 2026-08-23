"""Features stage: corner detection and evidence summaries.

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
BRAKE_RELEASE_THRESHOLD = 0.1
THROTTLE_REAPPLY_THRESHOLD = 0.1
FULL_THROTTLE_THRESHOLD = 0.95
EXIT_OFFSET = 200.0  # metres past the apex where exit speed/throttle are read


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


def build_evidence_summary(lap: Lap, reference: Lap | None = None) -> dict:
    """Everything a coach provider is allowed to know, as one JSON-ready dict.

    With no reference, deterministic technique checks use explicit review
    thresholds. They are not lap-time targets and are labelled as guides all
    the way through the prompt and UI.
    """
    if reference is None:
        return {
            "analysis_mode": "single_lap",
            "lap": {"name": lap.source.stem, "lap_time_s": round(lap.lap_time, 3)},
            "reference": None,
            "total_delta_s": None,
            "corners": _single_lap_corner_facts(lap),
        }
    return {
        "analysis_mode": "comparison",
        "lap": {"name": lap.source.stem, "lap_time_s": round(lap.lap_time, 3)},
        "reference": {"name": reference.source.stem, "lap_time_s": round(reference.lap_time, 3)},
        "total_delta_s": round(lap.lap_time - reference.lap_time, 3),
        "corners": _corner_facts(lap, reference),
    }


def _first_above(
    grid: np.ndarray, channel: np.ndarray, i0: int, i1: int, threshold: float
) -> float | None:
    """First true upward threshold crossing wholly inside ``[i0, i1)``.

    A channel already above the threshold at ``i0`` is deliberately not a
    crossing.  This prevents a clipped corner zone from turning its left edge
    into a made-up brake/throttle point.
    """
    index = _first_crossing_index(channel, i0, i1, threshold, rising=True)
    return round(float(grid[index]), 1) if index is not None else None


def _first_below(
    grid: np.ndarray, channel: np.ndarray, i0: int, i1: int, threshold: float
) -> float | None:
    """First true downward threshold crossing wholly inside ``[i0, i1)``."""
    index = _first_crossing_index(channel, i0, i1, threshold, rising=False)
    return round(float(grid[index]), 1) if index is not None else None


def _first_crossing_index(
    channel: np.ndarray,
    i0: int,
    i1: int,
    threshold: float,
    *,
    rising: bool,
) -> int | None:
    """Index of a threshold crossing made by two samples inside a half-open span."""
    start = max(0, int(i0))
    stop = min(int(i1), channel.size)
    if stop - start < 2:
        return None
    previous = channel[start : stop - 1]
    current = channel[start + 1 : stop]
    finite = np.isfinite(previous) & np.isfinite(current)
    if rising:
        crossed = finite & (previous < threshold) & (current >= threshold)
    else:
        crossed = finite & (previous > threshold) & (current <= threshold)
    hits = np.flatnonzero(crossed)
    return start + 1 + int(hits[0]) if hits.size else None


def _crossing_count(
    channel: np.ndarray, i0: int, i1: int, threshold: float
) -> int:
    """Count rising threshold crossings wholly inside a half-open span."""
    start = max(0, int(i0))
    stop = min(int(i1), channel.size)
    if stop - start < 2:
        return 0
    previous = channel[start : stop - 1]
    current = channel[start + 1 : stop]
    crossed = (
        np.isfinite(previous)
        & np.isfinite(current)
        & (previous < threshold)
        & (current >= threshold)
    )
    return int(np.count_nonzero(crossed))


def _rounded_channel_value(channel: np.ndarray, index: int, scale: float = 1.0) -> float | None:
    value = float(channel[index]) * scale
    return round(value, 1) if np.isfinite(value) else None


def _minimum_speed(
    grid: np.ndarray, speed: np.ndarray, i0: int, i1: int
) -> tuple[float | None, float | None]:
    """Minimum speed and its first distance in a non-empty half-open zone."""
    values = speed[i0:i1]
    finite = np.flatnonzero(np.isfinite(values))
    if not finite.size:
        return None, None
    relative = int(finite[np.argmin(values[finite])])
    index = i0 + relative
    return round(float(speed[index]) * 3.6, 1), round(float(grid[index]), 1)


def _coast_distance(release_m: float | None, reapply_m: float | None) -> float | None:
    """Distance between brake release and throttle reapplication.

    Pedal overlap is represented as zero coasting distance; if either boundary
    is not observed in the corner zone there is not enough evidence to report
    a distance.
    """
    if release_m is None or reapply_m is None:
        return None
    return round(max(0.0, reapply_m - release_m), 1)


def _channel_corner_facts(
    grid: np.ndarray,
    channels: dict[str, np.ndarray],
    i0: int,
    i1: int,
    apex: int,
    exit_i: int,
) -> dict:
    """One lap's deterministic pedal/speed facts for a shared corner zone."""
    min_speed, min_speed_point = _minimum_speed(grid, channels["speed"], i0, i1)
    brake_point = _first_above(grid, channels["brake"], i0, apex, BRAKE_THRESHOLD)
    brake_release = _first_below(grid, channels["brake"], i0, i1, BRAKE_RELEASE_THRESHOLD)
    throttle_reapply = _first_above(
        grid, channels["throttle"], apex, i1, THROTTLE_REAPPLY_THRESHOLD
    )
    throttle_point = _first_above(grid, channels["throttle"], apex, i1, THROTTLE_THRESHOLD)
    full_throttle = _first_above(grid, channels["throttle"], apex, i1, FULL_THROTTLE_THRESHOLD)
    brake_values = channels["brake"][i0 : min(apex + 1, i1)]
    finite_brake = brake_values[np.isfinite(brake_values)]
    peak_brake = round(float(finite_brake.max()) * 100.0, 1) if finite_brake.size else None
    brake_applications = _crossing_count(
        channels["brake"], i0, min(apex + 1, i1), BRAKE_THRESHOLD
    )
    throttle_applications = _crossing_count(
        channels["throttle"], apex, i1, THROTTLE_REAPPLY_THRESHOLD
    )
    brake_zone = channels["brake"][i0:i1]
    throttle_zone = channels["throttle"][i0:i1]
    finite_pedals = np.isfinite(brake_zone) & np.isfinite(throttle_zone)
    pedal_overlap = (
        round(
            100.0
            * float(
                np.count_nonzero(
                    finite_pedals
                    & (brake_zone > BRAKE_RELEASE_THRESHOLD)
                    & (throttle_zone > THROTTLE_REAPPLY_THRESHOLD)
                )
            )
            / float(np.count_nonzero(finite_pedals)),
            1,
        )
        if np.count_nonzero(finite_pedals)
        else None
    )
    return {
        "entry_speed_kmh": _rounded_channel_value(channels["speed"], i0, 3.6),
        "min_speed_kmh": min_speed,
        "min_speed_point_m": min_speed_point,
        "exit_speed_kmh": _rounded_channel_value(channels["speed"], exit_i, 3.6),
        "brake_point_m": brake_point,
        "peak_brake_pct": peak_brake,
        "brake_release_m": brake_release,
        "throttle_reapply_m": throttle_reapply,
        "throttle_point_m": throttle_point,
        "full_throttle_m": full_throttle,
        "exit_throttle_pct": _rounded_channel_value(channels["throttle"], exit_i, 100.0),
        "coast_distance_m": _coast_distance(brake_release, throttle_reapply),
        "brake_applications": brake_applications,
        "throttle_applications": throttle_applications,
        "pedal_overlap_pct": pedal_overlap,
    }


def _corner_facts(lap: Lap, reference: Lap) -> list[dict]:
    """Single fact source shared by coaching evidence and the Analysis table."""
    grid, delta = time_delta(lap, reference)
    mine = _on_grid(lap, grid)
    ref = _on_grid(reference, grid)
    facts = []
    for zone in detect_corners(reference):
        d0, d1 = zone["span_m"]
        i0 = min(int(np.searchsorted(grid, d0)), grid.size - 1)
        i1 = min(int(np.searchsorted(grid, d1)), grid.size - 1)
        apex = min(int(np.searchsorted(grid, zone["apex_m"])), grid.size - 1)
        if i1 <= i0 or not i0 <= apex < i1:
            continue
        exit_i = min(
            int(np.searchsorted(grid, zone["apex_m"] + EXIT_OFFSET)),
            grid.size - 1,
        )
        mine_facts = _channel_corner_facts(grid, mine, i0, i1, apex, exit_i)
        ref_facts = _channel_corner_facts(grid, ref, i0, i1, apex, exit_i)
        row = {**zone}
        for key, value in mine_facts.items():
            row[key] = value
            row[f"ref_{key}"] = ref_facts[key]
        row["time_lost_s"] = round(float(delta[i1] - delta[i0]), 3)
        facts.append(row)
    return facts


SINGLE_LAP_GUIDES = {
    "coast_distance_m": 20.0,
    "brake_applications": 1.0,
    "throttle_applications": 1.0,
    "pedal_overlap_pct": 5.0,
}

TECHNIQUE_CHECKS = (
    ("coast_distance_m", "long coast", lambda value, guide: value > guide),
    ("brake_applications", "repeated braking", lambda value, guide: value > guide),
    ("throttle_applications", "interrupted throttle", lambda value, guide: value > guide),
    ("pedal_overlap_pct", "pedal overlap", lambda value, guide: value >= guide),
)


def _flag_technique(row: dict, *, publish_guides: bool) -> None:
    """Score one corner against the fixed technique guides, in place.

    The guides describe the driver's own corner — how far they coasted, how many
    separate times they went back to a pedal — so they hold whether or not a
    reference lap is present, and both Analysis modes can show the same column.

    Only single-lap mode publishes the guides as the row's ``ref_*`` values. In a
    comparison those slots already hold the reference lap's own numbers, and
    overwriting them with a fixed guide would put a figure in the table that no
    lap ever recorded.
    """
    flags: list[str] = []
    score = 0.0
    for key, label, is_flagged in TECHNIQUE_CHECKS:
        value = row.get(key)
        guide = SINGLE_LAP_GUIDES[key]
        if publish_guides:
            row[f"ref_{key}"] = guide
        if isinstance(value, (int, float)) and is_flagged(float(value), guide):
            flags.append(label)
            score += max(1.0, float(value) / max(guide, 1.0))
    row["technique_flags"] = flags
    row["technique_score"] = round(score, 3)


def corner_table(lap: Lap, reference: Lap) -> list[dict]:
    """Per-corner comparison rows for the Analysis screen's corner table.

    The evidence summary's corner zones plus exit speeds, one row per corner:
    brake point, minimum speed, exit speed at apex+200 m (mine vs reference)
    and the time gained/lost across the zone. Each row also carries the same
    deterministic technique flags single-lap mode shows: a comparison says where
    the time went, and choosing one dropped the column that says what to do
    about the corner. Purely presentational — the coach prompt contract
    (build_evidence_summary) is untouched.
    """
    rows = []
    for fact in _corner_facts(lap, reference):
        row = dict(fact)
        row["delta_s"] = row.pop("time_lost_s")
        _flag_technique(row, publish_guides=False)
        rows.append(row)
    return rows


def _single_lap_corner_facts(lap: Lap) -> list[dict]:
    """Absolute corner facts plus conservative deterministic review flags."""
    rows = _corner_facts(lap, lap)
    for row in rows:
        row["time_lost_s"] = None
        _flag_technique(row, publish_guides=True)
    return rows


def single_lap_corner_table(lap: Lap) -> list[dict]:
    """Inspectable absolute corner rows used by the default Analysis mode."""
    return [dict(row) for row in _single_lap_corner_facts(lap)]
