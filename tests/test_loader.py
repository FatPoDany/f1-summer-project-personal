"""Contract tests for the CSV loader — the readable errors are part of the contract."""

import numpy as np
import pytest

from f1coach_core import TelemetrySchemaError, load_sample_lap, load_telemetry_csv

CANONICAL = ["t", "dist", "speed", "throttle", "brake", "steer", "gear", "sector"]


def write(tmp_path, text, name="lap.csv"):
    path = tmp_path / name
    path.write_text(text)
    return path


def test_sample_lap_loads_and_is_plausible():
    lap = load_sample_lap()  # the sample session's best lap
    assert lap.source.stem == "0822-coached-lap03"
    assert lap.schema_version == 1
    assert not lap.dist_derived
    # A real capture carries position and health channels too; the canonical
    # ones must all be present and the extras must survive the loader.
    assert CANONICAL == list(lap.df.columns)[: len(CANONICAL)]
    assert {"x", "y"} <= set(lap.df.columns)
    assert lap.n_samples > 1000
    assert 60 < lap.lap_time < 120  # a lap, not a stint
    assert 2000 < lap.track_length < 3000  # metres — Aalborg is ~2.6 km
    assert 150 < lap.top_speed_kmh < 250
    # Not monotonic, and correctly so: the recorded distance channel jitters by
    # centimetres at low speed. Asserting the shape of the lap rather than a
    # cleanliness the simulator never promised.
    assert lap.df["dist"].iloc[-1] > lap.df["dist"].iloc[0]
    assert lap.df["dist"].diff().min() > -1.0
    assert set(lap.df["sector"].unique()) == {1, 2, 3}


def test_dist_is_derived_when_missing(tmp_path):
    path = write(
        tmp_path,
        "t,speed,throttle,brake,steer,gear\n"
        "0.0,10.0,1,0,0,3\n"
        "1.0,20.0,1,0,0,3\n"
        "2.0,20.0,1,0,0,4\n",
    )
    lap = load_telemetry_csv(path)
    assert lap.dist_derived
    assert np.allclose(lap.df["dist"], [0.0, 15.0, 35.0])  # trapezoidal cumsum(speed*dt)


def test_missing_column_error_names_it(tmp_path):
    path = write(tmp_path, "t,speed\n0,1\n1,2\n")
    with pytest.raises(TelemetrySchemaError) as err:
        load_telemetry_csv(path)
    assert "throttle" in str(err.value)
    assert "lap.csv" in str(err.value)


def test_non_numeric_value_is_located(tmp_path):
    path = write(
        tmp_path,
        "t,speed,throttle,brake,steer,gear\n0.0,10.0,1,0,0,3\n1.0,fast,1,0,0,3\n",
    )
    with pytest.raises(TelemetrySchemaError, match="'speed'.*row 2"):
        load_telemetry_csv(path)


def test_unsupported_schema_version(tmp_path):
    path = write(
        tmp_path,
        "# schema_version: 99\nt,speed,throttle,brake,steer,gear\n0,1,1,0,0,3\n1,2,1,0,0,3\n",
    )
    with pytest.raises(TelemetrySchemaError, match="99"):
        load_telemetry_csv(path)


def test_time_going_backwards_is_rejected(tmp_path):
    path = write(
        tmp_path,
        "t,speed,throttle,brake,steer,gear\n0,1,1,0,0,3\n2,2,1,0,0,3\n1,3,1,0,0,3\n",
    )
    with pytest.raises(TelemetrySchemaError, match="backwards"):
        load_telemetry_csv(path)


def test_missing_file_is_a_readable_error():
    with pytest.raises(TelemetrySchemaError, match="No such"):
        load_telemetry_csv("/nowhere/lap.csv")


def test_extra_columns_are_kept(tmp_path):
    path = write(
        tmp_path,
        "t,speed,throttle,brake,steer,gear,tyre_temp\n0,1,1,0,0,3,80\n1,2,1,0,0,3,81\n",
    )
    lap = load_telemetry_csv(path)
    assert "tyre_temp" in lap.df.columns
