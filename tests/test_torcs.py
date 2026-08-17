"""TORCS adapter: split Lin's run exports into canonical laps.

No real export is in the repo yet, so these tests build a miniature run with
the exporter's column names (see M_Lin/torcs_highfreq_field_cheatsheet.csv):
a rolling grid fragment, three full laps, and a cut-off final fragment.
"""

import numpy as np
import pandas as pd
import pytest

from f1coach_core import (
    StudyIdentity,
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


def make_human_run(path, laps=5, samples_per_lap=60):
    """A human capture that ends exactly at the final start-line crossing.

    This is what the participant recorder actually produces. It lives in the
    driver callback, which the race engine stops calling once the car finishes,
    so the last lap has neither a following distance reset to close it nor any
    of the finish columns the robot and SCR paths carry.
    """
    dist_chunks = [np.linspace(TRACK_LENGTH - 40, TRACK_LENGTH - 1, 25)]  # grid -> line
    dist_chunks += [np.linspace(0, TRACK_LENGTH, samples_per_lap, endpoint=False)] * laps
    dist = np.concatenate(dist_chunks)
    n = dist.size
    race_lap = np.concatenate(
        [np.full(25, 1)] + [np.full(samples_per_lap, i + 1) for i in range(laps)]
    )
    pd.DataFrame(
        {
            "sim_time_s": 100.0 + np.arange(n) * DT,
            "dist_from_start_m": dist,
            "total_speed_mps": np.full(n, 45.0),
            "accel_cmd": np.full(n, 0.6),
            "brake_cmd": np.zeros(n),
            "steer_cmd": np.zeros(n),
            "gear": np.full(n, 4),
            "race_lap": race_lap,
            "car_name": "Human, Driver",
        }
    ).to_csv(path, index=False)
    return path


def test_final_human_lap_is_complete_without_any_finish_column(tmp_path):
    """Five assigned laps must import as five, not four."""
    run = make_human_run(tmp_path / "human-1.csv", laps=5)
    laps = split_torcs_run(run)

    assert "race_finished" not in pd.read_csv(run).columns
    assert [lap.lap_label for lap in laps if lap.complete] == [1, 2, 3, 4, 5]


def test_a_part_driven_final_lap_is_still_incomplete(tmp_path):
    """The fix must not promote a session abandoned mid-lap."""
    dist = np.concatenate(
        [
            np.linspace(TRACK_LENGTH - 40, TRACK_LENGTH - 1, 25),
            np.linspace(0, TRACK_LENGTH, 60, endpoint=False),
            np.linspace(0, TRACK_LENGTH * 0.4, 40),  # quit part way round
        ]
    )
    n = dist.size
    pd.DataFrame(
        {
            "sim_time_s": 100.0 + np.arange(n) * DT,
            "dist_from_start_m": dist,
            "total_speed_mps": np.full(n, 45.0),
            "accel_cmd": np.full(n, 0.6),
            "brake_cmd": np.zeros(n),
            "steer_cmd": np.zeros(n),
            "gear": np.full(n, 4),
            "race_lap": np.concatenate([np.full(25, 1), np.full(60, 1), np.full(40, 2)]),
            "car_name": "Human, Driver",
        }
    ).to_csv(tmp_path / "partial.csv", index=False)

    laps = split_torcs_run(tmp_path / "partial.csv")
    assert [lap.complete for lap in laps] == [False, True, False]


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


def test_finish_flag_does_not_promote_a_trailing_fragment_to_a_lap(tmp_path):
    run = make_run(tmp_path / "bt_3.csv")
    frame = pd.read_csv(run)
    frame["race_finished"] = 0
    frame.loc[frame.index[-1], "race_finished"] = 1
    frame.to_csv(run, index=False)

    complete = [lap for lap in split_torcs_run(run) if lap.complete]

    assert [lap.lap_label for lap in complete] == [1, 2, 3]


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
    written = (sessions_root() / "bt-run" / "bt_3-lap01.csv").read_text()
    header = dict(
        line.lstrip("# ").split(": ", 1)
        for line in written.splitlines()
        if line.startswith("#") and ": " in line
    )
    assert header["schema_version"] == "1"
    assert header["lap"] == "1"
    assert "torcs:bt_3.csv" in header["source"] and "car: bt 3" in header["source"]
    # A loose import carries no study identity, and must not invent one.
    assert "driver" not in header and "phase" not in header


def test_reimporting_the_same_torcs_run_does_not_duplicate_laps(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    run = make_run(tmp_path / "bt_3.csv")
    import_telemetry(run, "bt-run")

    summary = import_telemetry(run, "bt-run")

    assert summary == (
        "Imported 0 new laps from TORCS run bt_3.csv "
        "(3 already present, 2 incomplete fragments skipped)"
    )
    session = load_session(sessions_root() / "bt-run")
    assert [lap.source.name for lap in session.laps] == [
        "bt_3-lap01.csv",
        "bt_3-lap02.csv",
        "bt_3-lap03.csv",
    ]


def test_study_identity_round_trips_through_an_imported_lap(tmp_path, monkeypatch):
    """A lap file must keep saying who drove it, on its own."""
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    run = make_human_run(tmp_path / "human-1.csv", laps=2)

    import_telemetry(
        run,
        "P001-baseline",
        StudyIdentity(driver="P001", phase="baseline", setup="apex-study-v1"),
    )

    session = load_session(sessions_root() / "P001-baseline")
    assert [lap.lap_number for lap in session.laps] == [1, 2]
    assert [lap.label for lap in session.laps] == ["1", "2"]
    for lap in session.laps:
        assert lap.identity.driver == "P001"
        assert lap.identity.phase == "baseline"
        assert lap.identity.setup == "apex-study-v1"
