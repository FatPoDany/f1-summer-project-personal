"""Workspace store: ~/Apex relocated via APEX_WORKSPACE (tests always relocate)."""

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
    (root / "linked").symlink_to(outside, target_is_directory=True)

    assert list_sessions() == []
    with pytest.raises(ValueError, match="symbolic link"):
        delete_session("linked")
    assert outside.is_dir()
