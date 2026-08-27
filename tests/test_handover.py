"""Getting data off a participant's laptop without losing any of it on the way."""

import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from f1coach_core.participant import Background, load_background, save_background
from racecoach.telemetry.handover import (
    HandoverError,
    collect,
    package,
    register,
    session_name,
    unpack,
)


@pytest.fixture
def capture(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    directory = tmp_path / "A001-baseline-20260819-104237"
    directory.mkdir()
    (directory / "human-1.csv").write_text("t,speed\n0.0,10\n1.0,20\n", encoding="utf-8")
    (directory / "manifest.json").write_text(
        json.dumps({"participant_id": "A001", "phase": "baseline"}), encoding="utf-8"
    )
    return directory


def test_a_handover_is_one_file_a_participant_can_actually_send(capture, tmp_path):
    result = package(capture, tmp_path / "A001.zip")

    assert result.path.is_file()
    assert result.participant_id == "A001"
    assert result.laps == 1
    with zipfile.ZipFile(result.path) as archive:
        assert "handover.json" in archive.namelist()


def test_the_participant_id_comes_from_the_manifest_not_the_folder_name(tmp_path, monkeypatch):
    """A renamed folder must not be able to relabel somebody's data."""
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    directory = tmp_path / "totally-different-name"
    directory.mkdir()
    (directory / "lap.csv").write_text("t\n0\n", encoding="utf-8")
    (directory / "manifest.json").write_text(
        json.dumps({"participant_id": "B002"}), encoding="utf-8"
    )

    assert package(directory, tmp_path / "out.zip").participant_id == "B002"


def test_the_background_travels_with_the_data(capture, tmp_path):
    """A comparability check months later cannot go back and ask them."""
    save_background(Background(participant_id="A001", racing_games="weekly"))

    package(capture, tmp_path / "A001.zip")

    with zipfile.ZipFile(tmp_path / "A001.zip") as archive:
        assert "participant.json" in archive.namelist()
        stored = json.loads(archive.read("participant.json"))
    assert stored["racing_games"] == "weekly"


def test_a_round_trip_reproduces_every_file(capture, tmp_path):
    package(capture, tmp_path / "A001.zip")
    handover = unpack(tmp_path / "A001.zip", tmp_path / "pooled")

    assert handover.participant_id == "A001"
    landed = handover.path / "human-1.csv"
    assert landed.read_text("utf-8") == (capture / "human-1.csv").read_text("utf-8")


def test_a_truncated_file_stops_the_unpack_rather_than_arriving_quietly(capture, tmp_path):
    """A session that silently loses a lap has a hole no later check can find."""
    package(capture, tmp_path / "A001.zip")
    damaged = tmp_path / "damaged.zip"
    with (
        zipfile.ZipFile(tmp_path / "A001.zip") as source,
        zipfile.ZipFile(damaged, "w") as out,
    ):
        for name in source.namelist():
            data = source.read(name)
            out.writestr(name, data[:-1] if name.endswith(".csv") else data)

    with pytest.raises(HandoverError, match="does not match the digest"):
        unpack(damaged, tmp_path / "pooled")


def test_something_that_is_not_a_handover_is_refused_readably(tmp_path):
    stray = tmp_path / "holiday-photos.zip"
    with zipfile.ZipFile(stray, "w") as archive:
        archive.writestr("beach.jpg", b"not telemetry")

    with pytest.raises(HandoverError, match="not a readable handover"):
        unpack(stray, tmp_path / "pooled")


def test_an_empty_folder_is_refused_before_a_participant_sends_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(HandoverError, match="no files to hand over"):
        package(empty, tmp_path / "out.zip")


def test_pooling_refuses_the_same_session_twice(capture, tmp_path):
    """Otherwise that participant counts twice and nothing looks wrong."""
    package(capture, tmp_path / "first.zip")
    package(capture, tmp_path / "second.zip")

    with pytest.raises(HandoverError, match="second copy"):
        collect([tmp_path / "first.zip", tmp_path / "second.zip"], tmp_path / "pooled")


def test_pooling_keeps_participants_in_separate_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    archives = []
    for participant in ("A001", "B002"):
        directory = tmp_path / f"{participant}-baseline"
        directory.mkdir()
        (directory / "lap.csv").write_text("t\n0\n", encoding="utf-8")
        (directory / "manifest.json").write_text(
            json.dumps({"participant_id": participant}), encoding="utf-8"
        )
        archives.append(package(directory, tmp_path / f"{participant}.zip").path)

    results = collect(archives, tmp_path / "pooled")

    assert {h.participant_id for h in results} == {"A001", "B002"}
    assert len({h.path for h in results}) == 2
    assert all(Path(h.path).is_dir() for h in results)


TRACK_LENGTH = 2050.0


def torcs_run(path: Path, laps: int = 3) -> Path:
    """What a participant's machine actually writes: a whole race, unsplit.

    The tiny two-row CSV the other tests hand over is enough to prove bytes
    survived the journey. It is not enough to prove the data can be analysed
    afterwards, and those are different claims.
    """
    per_lap = 60
    dist = np.concatenate(
        [np.linspace(TRACK_LENGTH - 40, TRACK_LENGTH - 1, 25)]  # grid to the line
        + [np.linspace(0, TRACK_LENGTH, per_lap, endpoint=False)] * laps
    )
    n = dist.size
    race_lap = np.concatenate(
        [np.full(25, 1)] + [np.full(per_lap, i + 1) for i in range(laps)]
    )
    pd.DataFrame(
        {
            "sim_time_s": 100.0 + np.arange(n) * 0.02,
            "dist_from_start_m": dist,
            "total_speed_mps": 50.0 + 10.0 * np.sin(dist / 200.0),
            "accel_cmd": np.clip(np.cos(dist / 150.0), 0, 1),
            "brake_cmd": np.clip(-np.cos(dist / 150.0), 0, 1),
            "steer_cmd": 0.2 * np.sin(dist / 300.0),
            "gear": np.full(n, 5),
            "race_lap": race_lap,
            "car_name": "car7-trb1",
        }
    ).to_csv(path, index=False)
    return path


@pytest.fixture
def driven(tmp_path, monkeypatch):
    """A capture folder as a participant's own machine leaves it."""
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    directory = tmp_path / "A001-baseline-20260826-091209"
    directory.mkdir()
    torcs_run(directory / "human-1-1787735531-9624-1.csv")
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "participant_id": "A001",
                "phase": "baseline",
                "study_preset": {"preset_id": "apex-study-v1"},
            }
        ),
        encoding="utf-8",
    )
    return directory


def test_a_collected_handover_becomes_laps_the_study_can_actually_read(driven, tmp_path):
    """Verified and readable are different promises, and only one was being kept.

    What travels is a whole race in one file. Every reader on the study path
    takes canonical single laps, so a pool of intact handovers summarised to
    nothing at all -- which looks exactly like having collected nothing.
    """
    from f1coach_core.study import summarise_all
    from f1coach_core.workspace import list_sessions
    from racecoach.granite.report import session_laps

    package(driven, tmp_path / "A001.zip")
    (handover,) = collect([tmp_path / "A001.zip"], tmp_path / "pool")

    registered = register(handover)

    assert registered.session == driven.name  # not "A001-A001-baseline-..."
    assert registered.laps == 3
    (session,) = list_sessions()
    laps = session_laps(session)
    assert [lap.identity.driver for lap in laps] == ["A001"] * 3
    assert [lap.identity.phase for lap in laps] == ["baseline"] * 3
    (summary,) = summarise_all(laps)
    assert summary.driver == "A001" and summary.laps == 3


def test_the_questionnaire_is_adopted_here_not_left_in_the_pool(driven, tmp_path, monkeypatch):
    """A background that arrives and stays where it landed exports as blank."""
    save_background(Background(participant_id="A001", racing_games="weekly"))
    package(driven, tmp_path / "A001.zip")
    # A different analyst's machine: it has never met this participant.
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "analyst"))
    assert load_background("A001") is None

    (handover,) = collect([tmp_path / "A001.zip"], tmp_path / "pool")
    registered = register(handover)

    assert registered.background is True
    kept = load_background("A001")
    assert kept is not None and kept.racing_games == "weekly"


def test_collecting_the_same_handover_twice_does_not_count_it_twice(driven, tmp_path):
    """Re-running a pool must not double a participant's weight in the comparison."""
    package(driven, tmp_path / "A001.zip")

    (first,) = collect([tmp_path / "A001.zip"], tmp_path / "pool-a")
    assert register(first).laps == 3
    (again,) = collect([tmp_path / "A001.zip"], tmp_path / "pool-b")

    assert register(again).laps == 3


def test_a_handover_with_no_manifest_still_registers_what_it_carries(tmp_path, monkeypatch):
    """Identity missing is not data missing; the laps still exist to be looked at."""
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    directory = tmp_path / "loose-run"
    directory.mkdir()
    torcs_run(directory / "run.csv")
    (directory / "manifest.json").write_text("not json at all", encoding="utf-8")

    package(directory, tmp_path / "loose.zip")
    (handover,) = collect([tmp_path / "loose.zip"], tmp_path / "pool")
    registered = register(handover)

    assert registered.laps == 3
    assert registered.background is False
    assert session_name(handover) == "unknown-loose-run"


def test_collect_then_summarise_is_a_path_that_runs_end_to_end(driven, tmp_path, monkeypatch):
    """The command `collect` names as the next step has to be one that works."""
    from racecoach import cli

    save_background(Background(participant_id="A001", racing_games="weekly"))
    package(driven, tmp_path / "A001.zip")
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "analyst"))

    assert cli.main(["collect", str(tmp_path / "A001.zip"), "--into", str(tmp_path / "pool")]) == 0
    summary = tmp_path / "summary.csv"
    assert cli.main(["study-summary", "--out", str(summary)]) == 0
    laps = tmp_path / "laps.csv"
    assert cli.main(["study-laps", "--out", str(laps)]) == 0

    rows = summary.read_text("utf-8").strip().splitlines()
    assert len(rows) == 2 and rows[1].startswith("A001,baseline,3,")
    # The comparability columns travelled with the laps and are on the row.
    assert rows[1].endswith("weekly,3,,,,,,")
    assert len(laps.read_text("utf-8").strip().splitlines()) == 4
