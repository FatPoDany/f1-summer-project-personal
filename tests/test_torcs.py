"""TORCS adapter: split Lin's run exports into canonical laps.

No real export is in the repo yet, so these tests build a miniature run with
the exporter's column names (see M_Lin/torcs_highfreq_field_cheatsheet.csv):
a rolling grid fragment, three full laps, and a cut-off final fragment.
"""

import numpy as np
import pandas as pd
import pytest

from f1coach_core import (
    import_telemetry,
    is_torcs_export,
    load_sample_session,
    load_session,
    sessions_root,
    split_torcs_run,
)

TRACK_LENGTH = 2050.0
DT = 0.02


def make_run(path, laps=3, samples_per_lap=60):
    """Synthesise a TORCS high-frequency export with realistic structure."""
    dist_chunks = [np.linspace(TRACK_LENGTH - 40, TRACK_LENGTH - 1, 25)]  # grid -> line
    lap_chunks = [np.linspace(0, TRACK_LENGTH, samples_per_lap, endpoint=False)] * laps
    dist_chunks += lap_chunks
    dist_chunks += [np.linspace(0, TRACK_LENGTH / 4, 30)]  # cut-off final fragment
    dist = np.concatenate(dist_chunks)
    n = dist.size
    race_lap = np.concatenate(
        [np.full(25, 1)]
        + [np.full(samples_per_lap, i + 1) for i in range(laps)]
        + [np.full(30, laps + 1)]
    )
    df = pd.DataFrame(
        {
            "sim_time_s": 100.0 + np.arange(n) * DT,
            "dist_from_start_m": dist,
            "total_speed_mps": 50.0 + 10.0 * np.sin(dist / 200.0),
            "accel_cmd": np.clip(np.cos(dist / 150.0), 0, 1),
            "brake_cmd": np.clip(-np.cos(dist / 150.0), 0, 1),
            "steer_cmd": 0.2 * np.sin(dist / 300.0),
            "gear": np.full(n, 5),
            "race_lap": race_lap,
            "car_name": "bt 3",
            "aero_drag_n": np.full(n, 900.0),  # one of the 240 columns we ignore
        }
    )
    df.to_csv(path, index=False)
    return path


def test_signature_detection(tmp_path):
    run = make_run(tmp_path / "bt_3.csv")
    assert is_torcs_export(run)
    canonical = load_sample_session().laps[0].source
    assert not is_torcs_export(canonical)
    assert not is_torcs_export(tmp_path / "missing.csv")


def test_split_marks_only_full_laps_complete(tmp_path):
    laps = split_torcs_run(make_run(tmp_path / "bt_3.csv"))
    assert len(laps) == 5  # grid fragment + 3 laps + trailing fragment
    assert [lap.complete for lap in laps] == [False, True, True, True, False]
    assert [lap.lap_label for lap in laps if lap.complete] == [1, 2, 3]
    assert all(lap.car_name == "bt 3" for lap in laps)


def test_complete_laps_map_to_canonical_schema(tmp_path):
    lap = next(lap for lap in split_torcs_run(make_run(tmp_path / "bt_3.csv")) if lap.complete)
    df = lap.df
    canonical = ["t", "dist", "speed", "throttle", "brake", "steer", "gear", "sector"]
    assert list(df.columns) == canonical
    assert df["t"].iloc[0] == 0.0
    assert df["dist"].is_monotonic_increasing
    assert set(df["sector"].unique()) == {1, 2, 3}
    assert lap.lap_time == pytest.approx(df["t"].iloc[-1])


def test_corrupt_rows_are_dropped_not_fatal(tmp_path):
    run = make_run(tmp_path / "bt_3.csv")
    lines = run.read_text().splitlines()
    parts = lines[40].split(",")  # mid-lap-1 row: unparseable distance
    parts[1] = "garbage"
    lines[40] = ",".join(parts)
    parts = lines[70].split(",")  # another: empty speed cell
    parts[2] = ""
    lines[70] = ",".join(parts)
    run.write_text("\n".join(lines) + "\n")

    laps = split_torcs_run(run)

    assert [lap.complete for lap in laps] == [False, True, True, True, False]
    for lap in laps:
        assert not lap.df.isna().any().any()  # poisoned rows dropped, not written


def test_import_telemetry_splits_runs_into_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    run = make_run(tmp_path / "bt_3.csv")
    summary = import_telemetry(run, "bt-run")
    assert summary == "Imported 3 laps from TORCS run bt_3.csv (2 incomplete fragments skipped)"

    session = load_session(sessions_root() / "bt-run")
    assert [lap.source.name for lap in session.laps] == [
        "bt_3-lap01.csv",
        "bt_3-lap02.csv",
        "bt_3-lap03.csv",
    ]
    assert session.problems == ()  # split output passes the strict canonical loader
    head = (sessions_root() / "bt-run" / "bt_3-lap01.csv").read_text().splitlines()[:3]
    assert head[0] == "# schema_version: 1"
    assert "torcs:bt_3.csv" in head[1] and "car: bt 3" in head[1]
