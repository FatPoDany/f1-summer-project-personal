"""Assemble the structured metrics summary for one run — the JSON that is
both the human-readable ground truth and the only input the LLM feedback
layer is allowed to reason from. Rule of the pipeline: if a number is not
in this document, the coach may not say it."""

import json
from pathlib import Path

import numpy as np

from racecoach.analysis import channels as ch
from racecoach.analysis.events import detect_events
from racecoach.analysis.sections import detect_sections, section_stats
from racecoach.telemetry.run_store import LoadedRun, load_run

METRICS_NAME = "metrics.json"
METRICS_VERSION = "run-metrics-v1"
COMPLETE_LAP_COVERAGE = 0.95  # fraction of track length a lap must cover
MIN_LAP_ANALYSIS_SAMPLES = 2

KEY_CHANNELS = (
    "sim_time_s", "dist_from_start_m", "race_lap", "total_speed_mps",
    "accel_cmd", "brake_cmd", "steer_cmd", "gear", "engine_rpm", "damage",
    "track_to_middle_m", "track_seg_width_m", "track_seg_type",
    "cur_lap_time_s", "last_lap_time_s",
)


def build_run_metrics(run: LoadedRun) -> dict:
    df = run.df
    t = ch.numeric(df, ch.TIME)
    dist = ch.numeric(df, ch.DIST)
    laps = ch.lap_numbers(df)
    track_length = float(np.nanmax(dist))
    speed = ch.numeric(df, "total_speed_mps") if "total_speed_mps" in df.columns else None

    lap_rows = []
    observed = laps[~np.isnan(laps)]
    lap_ids = sorted(
        int(lap)
        for lap in set(observed)
        if int(np.count_nonzero(laps == lap)) >= MIN_LAP_ANALYSIS_SAMPLES
    )
    for lap in lap_ids:
        inside = laps == lap
        covered = float(np.nanmax(dist[inside]) - np.nanmin(dist[inside]))
        row = {
            "lap": lap,
            "samples": int(inside.sum()),
            "lap_time_s": _lap_time(df, laps, t, lap),
            "distance_covered_m": round(covered, 1),
            "complete": covered >= COMPLETE_LAP_COVERAGE * track_length,
        }
        if speed is not None:
            row["top_speed_kmh"] = round(float(np.nanmax(speed[inside])) * 3.6, 1)
            row["mean_speed_kmh"] = round(float(np.nanmean(speed[inside])) * 3.6, 1)
        lap_rows.append(row)

    analysis_df = df[np.isin(laps, lap_ids)]
    events, event_notes = detect_events(analysis_df)
    sections, section_notes = detect_sections(analysis_df)

    run_record = run.meta.to_dict()
    run_record["laps_seen"] = tuple(lap_ids)

    return {
        "metrics_version": METRICS_VERSION,
        "run": run_record,
        "track": {"length_m": round(track_length, 1)},
        "units": {"speed": "km/h", "distance": "m", "time": "s"},
        "laps": lap_rows,
        "sections": [section.to_dict() for section in sections],
        "section_stats": section_stats(df, sections),
        "events": [event.to_dict() for event in events],
        "analysis_notes": event_notes + section_notes,
    }


def analyze_run(run_id: str) -> Path:
    """Load a stored run, compute metrics, persist them beside the telemetry."""
    run = load_run(run_id)
    metrics = build_run_metrics(run)
    destination = run.path / METRICS_NAME
    destination.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return destination


def _lap_time(df, laps, t, lap: int) -> float | None:
    """Authoritative when the exporter carries it: race.lastLapTime as seen at
    the start of the *next* lap. Falls back to the sampled time span."""
    if "last_lap_time_s" in df.columns:
        following = laps == lap + 1
        if following.any():
            reported = ch.numeric(df, "last_lap_time_s")[following][:25]
            reported = reported[~np.isnan(reported)]
            if reported.size and float(reported[0]) > 0:
                return round(float(reported[0]), 3)
    inside = laps == lap
    if inside.sum() < 2:
        return None
    return round(float(np.nanmax(t[inside]) - np.nanmin(t[inside])), 3)
