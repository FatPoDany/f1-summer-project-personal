"""Straight/corner sections from the exporter's own track geometry.

`track_seg_type` is ground truth from the track file (1 right turn, 2 left
turn, 3 straight — track.h TR_RGT/TR_LFT/TR_STR), so unlike speed-minima
corner detection there is nothing to infer. One clean lap defines the
section map; per-lap stats are then computed against it for every lap.
"""

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from racecoach.analysis import channels as ch

SEG_KINDS = {1: "corner_right", 2: "corner_left", 3: "straight"}
MIN_SECTION_M = 25.0  # slivers shorter than this merge into the previous section
BRAKE_ON = 0.2
APPROACH_M = 150.0  # how far before a corner we look for the braking point


@dataclass(frozen=True)
class Section:
    label: str  # T1..Tn for corners, S1..Sn for straights, in track order
    kind: str  # straight | corner_left | corner_right
    dist_start_m: float
    dist_end_m: float

    def to_dict(self) -> dict:
        return asdict(self)


def detect_sections(df: pd.DataFrame) -> tuple[list[Section], list[str]]:
    gone = ch.missing(df, ch.DIST, "track_seg_type")
    if gone:
        return [], [f"sections: skipped, missing column(s) {gone}"]

    laps = ch.lap_numbers(df)
    values, counts = np.unique(laps[~np.isnan(laps)], return_counts=True)
    if values.size == 0:
        return [], ["sections: skipped, no usable lap samples"]
    reference_lap = values[np.argmax(counts)]

    rows = df.loc[laps == reference_lap, [ch.DIST, "track_seg_type"]]
    dist = pd.to_numeric(rows[ch.DIST], errors="coerce").to_numpy(dtype=float)
    seg = pd.to_numeric(rows["track_seg_type"], errors="coerce").to_numpy(dtype=float)
    order = np.argsort(dist)
    dist, seg = dist[order], seg[order]
    keep = ~(np.isnan(dist) | np.isnan(seg))
    dist, seg = dist[keep], seg[keep].astype(int)
    if dist.size < 2:
        return [], ["sections: skipped, reference lap too short"]

    # raw runs of equal seg type -> (kind, start, end)
    raw: list[list] = []
    for i in range(dist.size):
        kind = SEG_KINDS.get(seg[i], "straight")
        if raw and raw[-1][0] == kind:
            raw[-1][2] = dist[i]
        else:
            raw.append([kind, dist[i], dist[i]])

    merged: list[list] = []
    for kind, start, end in raw:
        if merged and (end - start) < MIN_SECTION_M:
            merged[-1][2] = end  # a sliver joins whatever came before it
        elif merged and merged[-1][0] == kind:
            merged[-1][2] = end
        else:
            merged.append([kind, start, end])

    sections, corners, straights = [], 0, 0
    for kind, start, end in merged:
        if kind == "straight":
            straights += 1
            label = f"S{straights}"
        else:
            corners += 1
            label = f"T{corners}"
        sections.append(
            Section(label=label, kind=kind, dist_start_m=round(float(start), 1),
                    dist_end_m=round(float(end), 1))
        )
    return sections, []


def section_stats(df: pd.DataFrame, sections: list[Section]) -> list[dict]:
    """Per lap × section: speed envelope, time spent, and corner braking point."""
    if not sections:
        return []
    t = ch.numeric(df, ch.TIME)
    dist = ch.numeric(df, ch.DIST)
    laps = ch.lap_numbers(df)
    speed = ch.numeric(df, "total_speed_mps") if "total_speed_mps" in df.columns else None
    brake = ch.numeric(df, "brake_cmd") if "brake_cmd" in df.columns else None

    stats = []
    for lap in sorted(set(int(lap) for lap in laps[~np.isnan(laps)])):
        on_lap = laps == lap
        for section in sections:
            inside = on_lap & (dist >= section.dist_start_m) & (dist <= section.dist_end_m)
            if inside.sum() < 2:
                continue
            row: dict = {
                "lap": lap,
                "section": section.label,
                "kind": section.kind,
                "time_s": round(float(t[inside].max() - t[inside].min()), 3),
            }
            if speed is not None:
                row["min_speed_kmh"] = round(float(np.nanmin(speed[inside])) * 3.6, 1)
                row["mean_speed_kmh"] = round(float(np.nanmean(speed[inside])) * 3.6, 1)
                row["max_speed_kmh"] = round(float(np.nanmax(speed[inside])) * 3.6, 1)
            if brake is not None and section.kind != "straight":
                approach = on_lap & (dist >= section.dist_start_m - APPROACH_M) & (
                    dist <= section.dist_end_m
                )
                hits = np.flatnonzero(approach & (brake >= BRAKE_ON))
                if hits.size:
                    row["brake_point_m"] = round(float(dist[hits[0]]), 1)
            stats.append(row)
    return stats
