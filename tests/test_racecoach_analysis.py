"""racecoach analysis: event detectors, sections, and the metrics JSON.

The fixture builds a 2-lap exporter-style run (1500 m track, 10 Hz) with one
deliberately injected incident per detector, so every assertion pins an event
to the lap and distance where it was planted — and nowhere else.
"""

import json

import numpy as np
import pandas as pd
import pytest

from racecoach.analysis.events import detect_events
from racecoach.analysis.metrics import build_run_metrics
from racecoach.analysis.sections import detect_sections, section_stats
from racecoach.telemetry.run_store import LoadedRun, RunMeta

TRACK_LENGTH = 1500.0
WIDTH = 10.0
SAMPLES_PER_LAP = 300  # 5 m per sample
DT = 0.1


def seg_type_for(dist: float) -> int:
    if 400 <= dist < 700:
        return 1  # T1, right
    if 1000 <= dist < 1300:
        return 2  # T2, left
    return 3


def make_run_frame() -> pd.DataFrame:
    n = 2 * SAMPLES_PER_LAP
    i = np.arange(n)
    lap_dist = (i % SAMPLES_PER_LAP) * 5.0
    seg = np.array([seg_type_for(d) for d in lap_dist])
    in_corner = seg != 3
    braking = ((lap_dist >= 350) & (lap_dist < 430)) | ((lap_dist >= 950) & (lap_dist < 1030))

    speed = np.where(in_corner, 25.0, 50.0)
    accel = np.where(braking, 0.0, np.where(in_corner, 0.3, 1.0))
    brake = np.where(braking, 0.7, 0.0)
    steer = np.where(seg == 1, 0.3, np.where(seg == 2, -0.3, 0.0))
    spin = speed / 0.33
    df = pd.DataFrame(
        {
            "sim_time_s": 100.0 + i * DT,
            "dist_from_start_m": lap_dist,
            "race_lap": np.where(i < SAMPLES_PER_LAP, 1, 2),
            "cur_lap_time_s": (i % SAMPLES_PER_LAP) * DT,
            "last_lap_time_s": np.where(i < SAMPLES_PER_LAP, 0.0, 30.0),
            "total_speed_mps": speed,
            "accel_cmd": accel,
            "brake_cmd": brake,
            "steer_cmd": steer,
            "gear": 4,
            "engine_rpm": 8000.0,
            "damage": 0.0,
            "track_to_middle_m": 1.0,
            "track_seg_width_m": WIDTH,
            "track_seg_type": seg,
            "car_name": "test bot",
            "fr_spin_vel_rad_s": spin,
            "fl_spin_vel_rad_s": spin,
            "rr_spin_vel_rad_s": spin,
            "rl_spin_vel_rad_s": spin,
        }
    )

    lap1, lap2 = i < SAMPLES_PER_LAP, i >= SAMPLES_PER_LAP
    # 1) pedal overlap, lap 1 @ 380-395 m (inside the T1 braking zone)
    window = lap1 & (lap_dist >= 380) & (lap_dist <= 395)
    df.loc[window, "accel_cmd"] = 0.8
    # 2) wheel lock-up, lap 1 @ 960-975 m (T2 approach, straight-line speed)
    window = lap1 & (lap_dist >= 960) & (lap_dist <= 975)
    df.loc[window, "brake_cmd"] = 0.9
    for wheel in ("fr", "fl", "rr", "rl"):
        df.loc[window, f"{wheel}_spin_vel_rad_s"] = 0.5
    # 3) steering jerk, lap 2 @ 200 m (one-sample flick on a straight)
    df.loc[lap2 & (lap_dist == 200), "steer_cmd"] = 0.9
    # 4) off track, lap 2 @ 550-575 m (six samples beyond the left edge)
    df.loc[lap2 & (lap_dist >= 550) & (lap_dist <= 575), "track_to_middle_m"] = 6.0
    # 5) collision, lap 2 @ 1100 m (damage jumps once and stays)
    df.loc[lap2 & (lap_dist >= 1100), "damage"] = 150.0
    return df


def loaded_run(df: pd.DataFrame) -> LoadedRun:
    meta = RunMeta(
        run_id="test-run", source_file="test.csv", imported_at="2026-07-12T00:00:00+00:00",
        n_samples=len(df), n_columns=len(df.columns), car_names=("test bot",),
        laps_seen=(1, 2), sim_time_span_s=59.9, cadence_hz=10.0, capture="torcs-exporter",
    )
    return LoadedRun(meta=meta, df=df, path=None)


@pytest.fixture(scope="module")
def frame():
    return make_run_frame()


@pytest.fixture(scope="module")
def events_and_notes(frame):
    return detect_events(frame)


def by_kind(events, kind):
    return [event for event in events if event.kind == kind]


def test_every_injected_incident_is_found_once(events_and_notes):
    events, notes = events_and_notes
    assert notes == []
    kinds = sorted(event.kind for event in events)
    assert kinds == [
        "collision", "off_track", "pedal_overlap", "steering_jerk", "wheel_lockup",
    ]  # exactly one each — nothing invented, nothing missed


def test_off_track_pins_lap_side_and_distance(events_and_notes):
    (event,) = by_kind(events_and_notes[0], "off_track")
    assert event.lap == 2
    assert event.dist_start_m == 550.0 and event.dist_end_m == 575.0
    assert event.evidence["side"] == "left"
    assert event.evidence["max_track_pos"] == pytest.approx(1.2)
    assert "track_pos peaked at 1.20" in event.summary


def test_collision_reports_damage_delta(events_and_notes):
    (event,) = by_kind(events_and_notes[0], "collision")
    assert event.lap == 2 and event.dist_start_m == 1100.0
    assert event.evidence["damage_delta"] == 150.0
    assert "damage +150" in event.summary


def test_pedal_overlap_and_lockup_sit_in_their_braking_zones(events_and_notes):
    (overlap,) = by_kind(events_and_notes[0], "pedal_overlap")
    assert overlap.lap == 1
    assert 380.0 <= overlap.dist_start_m <= overlap.dist_end_m <= 395.0
    (lockup,) = by_kind(events_and_notes[0], "wheel_lockup")
    assert lockup.lap == 1
    assert 960.0 <= lockup.dist_start_m <= lockup.dist_end_m <= 975.0
    assert lockup.evidence["brake_cmd"] == 0.9


def test_steering_jerk_catches_the_flick_not_corner_entry(events_and_notes):
    (jerk,) = by_kind(events_and_notes[0], "steering_jerk")
    assert jerk.lap == 2
    assert jerk.dist_start_m == 200.0
    assert jerk.evidence["peak_rate_per_s"] == pytest.approx(9.0)


def test_missing_channels_skip_detectors_readably(frame):
    degraded = frame.drop(columns=["track_to_middle_m"])
    events, notes = detect_events(degraded)
    assert not by_kind(events, "off_track")
    assert any("off_track" in note and "track_to_middle_m" in note for note in notes)
    assert by_kind(events, "collision")  # the others still run


def test_sections_come_from_track_geometry(frame):
    sections, notes = detect_sections(frame)
    assert notes == []
    assert [(s.label, s.kind) for s in sections] == [
        ("S1", "straight"), ("T1", "corner_right"), ("S2", "straight"),
        ("T2", "corner_left"), ("S3", "straight"),
    ]
    t1 = sections[1]
    assert t1.dist_start_m == 400.0 and t1.dist_end_m == pytest.approx(695.0)


def test_section_stats_carry_speeds_and_brake_points(frame):
    sections, _ = detect_sections(frame)
    stats = section_stats(frame, sections)
    lap1_t1 = next(r for r in stats if r["lap"] == 1 and r["section"] == "T1")
    assert lap1_t1["min_speed_kmh"] == pytest.approx(90.0)
    assert lap1_t1["brake_point_m"] == 350.0
    lap1_s1 = next(r for r in stats if r["lap"] == 1 and r["section"] == "S1")
    assert lap1_s1["max_speed_kmh"] == pytest.approx(180.0)
    assert "brake_point_m" not in lap1_s1  # straights don't get braking points


def test_run_metrics_json_is_complete_and_serializable(frame):
    metrics = build_run_metrics(loaded_run(frame))
    text = json.dumps(metrics)  # would raise on stray numpy types
    assert '"metrics_version": "run-metrics-v1"' in text

    lap1, lap2 = metrics["laps"]
    assert lap1["lap_time_s"] == 30.0  # authoritative: last_lap_time_s at lap-2 start
    assert lap2["lap_time_s"] == pytest.approx(29.9)  # fallback: sampled span
    assert lap1["complete"] and lap2["complete"]
    assert lap1["top_speed_kmh"] == pytest.approx(180.0)
    assert metrics["track"]["length_m"] == 1495.0
    assert len(metrics["events"]) == 5
    assert len(metrics["sections"]) == 5
    assert metrics["analysis_notes"] == []


def test_run_metrics_omits_a_terminal_single_sample_lap(frame):
    terminal = frame.iloc[[-1]].copy()
    terminal["sim_time_s"] = float(frame["sim_time_s"].iloc[-1]) + DT
    terminal["dist_from_start_m"] = 0.2
    terminal["race_lap"] = 3
    terminal["cur_lap_time_s"] = 0.02
    with_terminal = pd.concat([frame, terminal], ignore_index=True)

    metrics = build_run_metrics(loaded_run(with_terminal))

    assert [lap["lap"] for lap in metrics["laps"]] == [1, 2]
