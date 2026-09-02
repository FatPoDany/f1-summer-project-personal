"""Runtime-layout resolution for the autotools and Visual Studio TORCS builds."""

import sys
from pathlib import Path

import pytest

from racecoach.telemetry import torcs_runtime
from racecoach.telemetry.human_capture import default_study_preset, study_presets
from racecoach.telemetry.synthetic_capture import default_robot_study_preset
from racecoach.telemetry.torcs_runtime import (
    default_torcs_binary,
    graphical_session_issue,
    torcs_data_root,
    torcs_executable,
    torcs_raceman_dir,
    torcs_runtime_root,
)


@pytest.fixture
def posix_layout(monkeypatch):
    monkeypatch.setattr(torcs_runtime, "WINDOWS", False)
    monkeypatch.setattr(torcs_runtime, "TORCS_EXECUTABLE_NAME", "torcs")


@pytest.fixture
def windows_layout(monkeypatch):
    monkeypatch.setattr(torcs_runtime, "WINDOWS", True)
    monkeypatch.setattr(torcs_runtime, "TORCS_EXECUTABLE_NAME", "wtorcs.exe")


def test_posix_runtime_splits_binary_and_data(posix_layout, tmp_path):
    root = tmp_path / "torcs-runtime"
    binary = torcs_executable(root)

    assert binary == root / "bin" / "torcs"
    assert torcs_runtime_root(binary) == root.resolve()
    assert torcs_data_root(binary) == root.resolve() / "share" / "games" / "torcs"
    assert torcs_raceman_dir(binary).name == "raceman"


def test_windows_runtime_keeps_binary_and_data_together(windows_layout, tmp_path):
    """src/windows/main.cpp derives DataDir from the folder holding wtorcs.exe."""
    root = tmp_path / "torcs-runtime"
    binary = torcs_executable(root)

    assert binary == root / "wtorcs.exe"
    assert torcs_runtime_root(binary) == root.resolve()
    assert torcs_data_root(binary) == root.resolve()
    assert torcs_raceman_dir(binary) == root.resolve() / "config" / "raceman"


def test_windows_presets_resolve_beside_the_executable(windows_layout, tmp_path):
    binary = tmp_path / "torcs-runtime" / "wtorcs.exe"
    raceman = binary.parent.resolve() / "config" / "raceman"

    assert [preset.race_config for preset in study_presets(binary)] == [
        raceman / name
        for name in ("apexibmf1.xml", "apexstudy.xml", "apexstudyspeedway.xml")
    ]
    assert default_study_preset(binary).race_config.parent == raceman
    assert (
        default_robot_study_preset(binary).race_config == raceman / "apexrobotstudy.xml"
    )


def test_posix_presets_resolve_under_the_share_prefix(posix_layout, tmp_path):
    binary = tmp_path / "torcs-runtime" / "bin" / "torcs"
    raceman = (
        binary.parent.parent.resolve() / "share" / "games" / "torcs" / "config" / "raceman"
    )

    assert [preset.race_config for preset in study_presets(binary)] == [
        raceman / name
        for name in ("apexibmf1.xml", "apexstudy.xml", "apexstudyspeedway.xml")
    ]
    assert default_study_preset(binary).race_config.parent == raceman
    assert (
        default_robot_study_preset(binary).race_config == raceman / "apexrobotstudy.xml"
    )


def test_configured_prefix_wins_over_discovery(posix_layout, tmp_path, monkeypatch):
    monkeypatch.setenv("TORCS_PREFIX", str(tmp_path / "prefix"))

    assert default_torcs_binary() == tmp_path / "prefix" / "bin" / "torcs"


@pytest.mark.parametrize(
    ("layout", "relative"),
    [("posix_layout", Path("bin") / "torcs"), ("windows_layout", Path("wtorcs.exe"))],
)
def test_default_binary_prefers_runtime_bundled_with_the_app(
    request, tmp_path, monkeypatch, layout, relative
):
    request.getfixturevalue(layout)
    bundle = tmp_path / "apex-bundle"
    binary = bundle / "torcs-runtime" / relative
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"bundled simulator")
    binary.chmod(0o700)
    monkeypatch.delenv("TORCS_PREFIX", raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    assert default_torcs_binary() == binary


def test_windows_reports_the_packaged_path_when_nothing_is_installed(
    windows_layout, tmp_path, monkeypatch
):
    """There is no /tmp build convention on Windows to fall back to."""
    monkeypatch.delenv("TORCS_PREFIX", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Apex.exe"))

    assert default_torcs_binary() == tmp_path.resolve() / "torcs-runtime" / "wtorcs.exe"


@pytest.mark.parametrize("name", ["RDP-Tcp#4", "rdp-sxs2208211527"])
def test_windows_remote_desktop_session_has_an_actionable_graphics_issue(
    windows_layout, name
):
    issue = graphical_session_issue({"SESSIONNAME": name})

    assert issue is not None
    assert "Remote Desktop" in issue
    assert "locally" in issue


def test_local_windows_and_non_windows_sessions_are_supported(
    windows_layout, monkeypatch
):
    assert graphical_session_issue({"SESSIONNAME": "Console"}) is None

    monkeypatch.setattr(torcs_runtime, "WINDOWS", False)
    assert graphical_session_issue({"SESSIONNAME": "RDP-Tcp#4"}) is None
