"""Starting the model server for someone who will never open a terminal."""

import subprocess
from pathlib import Path

import pytest

from racecoach.granite import model as gm
from racecoach.granite import server as gs


class FakeProcess:
    """A llama-server that answers after `answers_after` probes, or dies."""

    def __init__(self, exits_with: int | None = None) -> None:
        self.exits_with = exits_with
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.exits_with

    def terminate(self):
        self.terminated = True
        self.exits_with = 0

    def kill(self):  # pragma: no cover - only for the hung case
        self.killed = True
        self.exits_with = -9

    def wait(self, timeout=None):
        return self.exits_with

    @property
    def returncode(self):
        return self.exits_with


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """A binary and verified weights, so start() has everything it needs."""
    binary = tmp_path / "granite-runtime" / gs.SERVER_EXECUTABLE
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setenv("LLAMA_SERVER_BIN", str(binary))

    weights = tmp_path / "granite.gguf"
    weights.write_bytes(b"weights")
    monkeypatch.setenv("GRANITE_MODEL_PATH", str(weights))
    monkeypatch.setattr(gm, "MODEL_SIZE", 7)
    monkeypatch.setattr(
        gm,
        "MODEL_SHA256",
        __import__("hashlib").sha256(b"weights").hexdigest(),
    )
    monkeypatch.delenv("GRANITE_API_KEY", raising=False)
    return binary, weights


def test_a_server_someone_else_is_running_is_used_not_replaced(monkeypatch):
    """A researcher's own server on that port is theirs; do not fight it."""

    def refuse(*_a, **_k):  # pragma: no cover - proves nothing is launched
        raise AssertionError("started a second server on an occupied port")

    server = gs.GraniteServer(popen=refuse, probe=lambda _p: True)
    assert server.start() == "http://127.0.0.1:8080/v1"
    assert not server.owns_process

    server.stop()  # must not try to kill what it did not start


def test_starting_waits_until_the_endpoint_actually_answers(installed):
    """Loading 2.1 GB off a laptop disk is slow; "not yet" is not "broken"."""
    launched = []
    # First probe is start()'s "is one already running" check; the rest are the
    # wait loop, so two Falses here mean exactly one sleep.
    probes = iter([False, False, True])
    slept = []

    server = gs.GraniteServer(
        popen=lambda cmd, **kw: launched.append((cmd, kw)) or FakeProcess(),
        probe=lambda _p: next(probes),
        sleep=slept.append,
        clock=lambda: 0.0,
    )
    assert server.start() == "http://127.0.0.1:8080/v1"
    assert server.owns_process
    assert slept == [0.5]

    command, kwargs = launched[0]
    binary, weights = installed
    assert command[0] == str(binary)
    assert command[command.index("-m") + 1] == str(weights)
    assert kwargs["stdout"] is subprocess.DEVNULL


def test_a_server_that_dies_while_loading_is_reported_not_waited_on(installed):
    server = gs.GraniteServer(
        popen=lambda *_a, **_k: FakeProcess(exits_with=1),
        probe=lambda _p: False,
        clock=lambda: 0.0,
    )
    with pytest.raises(gs.ServerError, match="stopped while starting up"):
        server.start()


def test_a_server_that_never_answers_times_out_and_is_stopped(installed):
    process = FakeProcess()
    times = iter([0.0, 0.0, 1000.0, 1000.0])
    server = gs.GraniteServer(
        popen=lambda *_a, **_k: process,
        probe=lambda _p: False,
        sleep=lambda _s: None,
        clock=lambda: next(times),
    )
    with pytest.raises(gs.ServerError, match="did not answer"):
        server.start()
    assert process.terminated


def test_missing_weights_stop_the_coach_without_stopping_the_app(installed, monkeypatch):
    _binary, weights = installed
    weights.unlink()

    server = gs.GraniteServer(probe=lambda _p: False)
    with pytest.raises(gs.ServerError, match="missing or not the pinned"):
        server.start()


def test_a_build_without_the_runtime_says_so_plainly(tmp_path, monkeypatch):
    monkeypatch.setenv("LLAMA_SERVER_BIN", str(tmp_path / "absent"))
    server = gs.GraniteServer(probe=lambda _p: False)
    with pytest.raises(gs.ServerError, match="not installed with this copy"):
        server.start()


def test_stopping_terminates_the_process_we_started(installed):
    process = FakeProcess()
    server = gs.GraniteServer(
        popen=lambda *_a, **_k: process, probe=lambda _p: True if process else False
    )
    probes = iter([False, True])
    server._probe = lambda _p: next(probes)
    server.start()
    server.stop()
    assert process.terminated


def test_the_command_matches_the_shell_script_the_researchers_use(installed):
    """Both paths must configure the server identically to be comparable."""
    binary, weights = installed
    command = gs.server_command(binary, weights, port=8080, threads=4)
    script = Path("integrations/granite-4.1/start-server.sh").read_text("utf-8")

    for flag in ("--jinja", "--no-mmproj", "--no-ui", "--cors-origins", "--parallel"):
        assert flag in command, flag
        assert flag in script, flag
    assert command[command.index("--ctx-size") + 1] == "4096"
    assert '--ctx-size 4096' in script
    assert command[command.index("--host") + 1] == "127.0.0.1"


def test_an_api_key_is_passed_through_when_one_is_set(installed, monkeypatch):
    monkeypatch.setenv("GRANITE_API_KEY", "secret")
    binary, weights = installed
    command = gs.server_command(binary, weights, port=8080, threads=4)
    assert command[command.index("--api-key") + 1] == "secret"


def test_threads_leave_room_for_the_simulator(monkeypatch):
    monkeypatch.setattr(gs.os, "cpu_count", lambda: 16)
    assert gs.default_threads() == 8
    monkeypatch.setattr(gs.os, "cpu_count", lambda: 4)
    assert gs.default_threads() == 2
    monkeypatch.setattr(gs.os, "cpu_count", lambda: 1)
    assert gs.default_threads() == 1


def test_the_windows_payload_pin_matches_the_source_pin():
    """One llama.cpp version across both platforms, or the two coaching paths
    are not configured the same and their output is not comparable."""
    from racecoach.granite import server as gs

    build = Path("integrations/torcs-1.3.9/build-windows.ps1").read_text("utf-8")
    bootstrap = Path("integrations/granite-4.1/bootstrap-llama-server.sh").read_text("utf-8")

    assert f"$LlamaTag = '{gs.LLAMA_TAG}'" in build
    assert f'LLAMA_TAG="{gs.LLAMA_TAG}"' in bootstrap
    # The archive is pinned by size and digest, like the TORCS one.
    assert "$LlamaSize = 18581129" in build
    assert "11d38f2ed878489b2c3d02b3d1a67683c02fbfb3d265876b9ede749a8dff5f1c" in build


def test_the_installer_refuses_a_payload_without_the_model_server():
    nsi = Path("integrations/torcs-1.3.9/installer/apexstudy.nsi").read_text("utf-8")
    assert "granite-runtime\\llama-server.exe" in nsi
