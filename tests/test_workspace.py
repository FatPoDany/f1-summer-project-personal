"""Workspace store: ~/Apex relocated via APEX_WORKSPACE (tests always relocate)."""

import os

import pytest

from f1coach_core import (
    SAMPLE_SESSION_NAME,
    create_session,
    delete_session,
    ensure_sample_session,
    import_lap,
    import_telemetry,
    list_sessions,
    load_session,
    sessions_root,
)

CANONICAL = (
    "t,dist,speed,throttle,brake,steer,gear,sector\n"
    "0.0,0,20,1,0,0,3,1\n1.0,20,20,1,0,0,3,2\n2.0,40,20,1,0,0,3,3\n"
)


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    return tmp_path


def test_create_and_list_sessions():
    assert list_sessions() == []
    create_session("fp1")
    create_session("fp2")
    assert [p.name for p in list_sessions()] == ["fp1", "fp2"]


def test_import_lap_never_overwrites(tmp_path):
    src = tmp_path / "lap.csv"
    src.write_text(CANONICAL)
    first = import_lap(src, "fp1")
    second = import_lap(src, "fp1")
    assert first.name == "lap.csv"
    assert second.name == "lap-2.csv"
    assert len(list(sessions_root().joinpath("fp1").glob("*.csv"))) == 2


def test_import_telemetry_copies_canonical_files(tmp_path):
    src = tmp_path / "lap.csv"
    src.write_text(CANONICAL)
    summary = import_telemetry(src, "fp1")
    assert summary == "Imported lap.csv"
    session = load_session(sessions_root() / "fp1")
    assert len(session.laps) == 1


def test_create_session_rejects_unusable_names():
    for bad in ("", "   ", ".", "..", "a/b", "a\\b"):
        with pytest.raises(ValueError, match="folder name"):
            create_session(bad)
    assert list_sessions() == []


def test_ensure_sample_session_is_idempotent():
    target = ensure_sample_session()
    assert target.name == SAMPLE_SESSION_NAME
    files = sorted(p.name for p in target.glob("*.csv"))
    assert files == ["lap_01.csv", "lap_02.csv", "lap_03.csv"]
    assert ensure_sample_session() == target
    assert sorted(p.name for p in target.glob("*.csv")) == files
    session = load_session(target)
    assert len(session.laps) == 3 and session.problems == ()


def test_delete_session_removes_only_the_managed_copy(tmp_path):
    source = tmp_path / "original-lap.csv"
    source.write_text(CANONICAL)
    import_lap(source, "practice")

    deleted = delete_session("practice")

    assert deleted.name == "practice"
    assert not deleted.exists()
    assert source.is_file()
    assert list_sessions() == []


@pytest.mark.parametrize("name", ["../outside", "missing"])
def test_delete_session_rejects_unmanaged_or_missing_targets(name):
    error = ValueError if name.startswith(".") else FileNotFoundError
    with pytest.raises(error):
        delete_session(name)


def test_session_symlinks_are_never_listed_or_deleted(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = sessions_root()
    root.mkdir(parents=True)
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symbolic links require Developer Mode or elevation")
        raise

    assert list_sessions() == []
    with pytest.raises(ValueError, match="symbolic link"):
        delete_session("linked")
    assert outside.is_dir()


def test_a_session_remembers_where_its_footage_is_without_copying_it(tmp_path, monkeypatch):
    """The recording is hundreds of MB and already sits in the capture folder."""
    import json

    from f1coach_core.workspace import session_recording

    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    video = tmp_path / "capture" / "session.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    session = tmp_path / "ws" / "sessions" / "s"
    session.mkdir(parents=True)
    (session / "recording.json").write_text(
        json.dumps({"path": str(video), "started_at": 1000.0, "duration_s": 60.0}),
        encoding="utf-8",
    )

    recording = session_recording(session)
    assert recording is not None and recording.started_at == 1000.0
    assert recording.path.read_bytes() == b"video"


def test_a_pointer_to_footage_that_has_been_deleted_reads_as_none(tmp_path, monkeypatch):
    """A pointer outlives the file it names; failing here beats failing in ffmpeg."""
    import json

    from f1coach_core.workspace import session_recording

    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    session = tmp_path / "ws" / "sessions" / "s"
    session.mkdir(parents=True)
    (session / "recording.json").write_text(
        json.dumps({"path": str(tmp_path / "gone.mp4"), "started_at": 1.0}), encoding="utf-8"
    )

    assert session_recording(session) is None


def test_a_session_that_was_never_recorded_reads_as_none(tmp_path, monkeypatch):
    from f1coach_core.workspace import session_recording

    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    (tmp_path / "s").mkdir()
    assert session_recording(tmp_path / "s") is None
