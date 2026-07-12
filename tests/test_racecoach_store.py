"""racecoach run store and CLI: import, list, analyze, and readable refusals."""

import json

import pytest

from racecoach.cli import main
from racecoach.telemetry.run_store import (
    RunImportError,
    import_run,
    list_runs,
    load_run,
    runs_root,
)
from test_racecoach_analysis import make_run_frame

CANONICAL_LAP = (
    "t,dist,speed,throttle,brake,steer,gear,sector\n"
    "0.0,0,20,1,0,0,3,1\n1.0,20,20,1,0,0,3,2\n"
)


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


@pytest.fixture
def run_csv(tmp_path):
    path = tmp_path / "quali_bot.csv"
    make_run_frame().to_csv(path, index=False)
    return path


def test_import_list_load_roundtrip(run_csv):
    run_dir = import_run(run_csv)
    assert (run_dir / "telemetry.csv").is_file() and (run_dir / "meta.json").is_file()

    (meta,) = list_runs()
    assert meta.run_id == run_dir.name and meta.run_id.startswith("quali_bot-")
    assert meta.n_samples == 600 and meta.laps_seen == (1, 2)
    assert meta.car_names == ("test bot",)
    assert meta.cadence_hz == pytest.approx(10.0)
    assert meta.capture == "torcs-exporter"

    run = load_run(meta.run_id)
    assert len(run.df) == 600 and run.path == run_dir


def test_import_refuses_non_exporter_files(tmp_path):
    lap = tmp_path / "lap.csv"
    lap.write_text(CANONICAL_LAP)
    with pytest.raises(RunImportError, match="Apex session"):
        import_run(lap)
    with pytest.raises(RunImportError, match="No such telemetry file"):
        import_run(tmp_path / "ghost.csv")


def test_unknown_run_id_is_readable():
    with pytest.raises(RunImportError, match="none imported yet"):
        load_run("nope")


def test_cli_import_analyze_report_flow(run_csv, capsys):
    assert main(["import", str(run_csv)]) == 0
    run_id = list_runs()[0].run_id

    assert main(["list"]) == 0
    assert run_id in capsys.readouterr().out

    assert main(["analyze", run_id]) == 0
    out = capsys.readouterr().out
    assert "events: 5" in out and "off_track" in out
    metrics = json.loads((runs_root() / run_id / "metrics.json").read_text("utf-8"))
    assert metrics["run"]["run_id"] == run_id

    assert main(["run"]) == 2  # Path B pending: readable, non-zero, not a crash
    assert "SCR" in capsys.readouterr().err
    assert main(["report", "--run", run_id]) == 2

    assert main(["import", str(run_csv / "missing.csv")]) == 2
    assert "racecoach:" in capsys.readouterr().err


def test_cli_import_rejects_canonical_lap(tmp_path, capsys):
    lap = tmp_path / "lap.csv"
    lap.write_text(CANONICAL_LAP)
    assert main(["import", str(lap)]) == 2
    assert "Apex session" in capsys.readouterr().err
