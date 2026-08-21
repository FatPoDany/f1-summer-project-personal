"""Getting data off a participant's laptop without losing any of it on the way."""

import json
import zipfile
from pathlib import Path

import pytest

from f1coach_core.participant import Background, save_background
from racecoach.telemetry.handover import HandoverError, collect, package, unpack


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
