"""Running llama-server on the participant's behalf.

The study ships to classmates' own laptops, so nobody is going to open a
terminal and start a model server. This owns that process instead: find the
binary, refuse to start against weights that are not the pinned ones, wait until
the endpoint actually answers, and stop it again when the app closes.

The argument list is the one in ``integrations/granite-4.1/start-server.sh``,
kept identical on purpose -- a researcher reproducing a participant's coaching
from the shell must be talking to a server configured the same way.
"""

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from racecoach.granite import model as gm

LLAMA_TAG = "b10549"
SERVER_EXECUTABLE = "llama-server.exe" if sys.platform == "win32" else "llama-server"
DEFAULT_PORT = 8080
DEFAULT_CONTEXT = 4096

# Loading 2.1 GB of weights off a cold disk on a laptop is not quick, and the
# alternative to waiting is telling a participant the coach is broken when it is
# merely still starting.
STARTUP_TIMEOUT_S = 180.0


class ServerError(RuntimeError):
    """The model server could not be located, started, or reached."""


def server_binary() -> Path | None:
    """The llama-server this install should use, or None if there is not one.

    Packaged builds carry it beside the executable, the same way the simulator is
    staged; a source checkout uses whatever bootstrap-llama-server.sh built.
    """
    configured = os.environ.get("LLAMA_SERVER_BIN")
    if configured:
        candidate = Path(configured)
        return candidate if candidate.is_file() else None

    roots: list[Path] = []
    packaged = getattr(sys, "_MEIPASS", None)
    if packaged:
        roots.append(Path(packaged))
    roots.append(Path(sys.executable).resolve().parent)
    roots.append(Path.cwd() / ".tools" / f"llama.cpp-{LLAMA_TAG}")

    for root in roots:
        for candidate in (
            root / "granite-runtime" / SERVER_EXECUTABLE,
            root / "bin" / SERVER_EXECUTABLE,
            root / SERVER_EXECUTABLE,
        ):
            if candidate.is_file():
                return candidate
    return None


def endpoint(port: int = DEFAULT_PORT) -> str:
    return f"http://127.0.0.1:{port}/v1"


def is_responding(port: int = DEFAULT_PORT, timeout_s: float = 1.0) -> bool:
    """Whether something is already serving the API on this port.

    Used before starting anything: a researcher may have their own server up,
    and a second one on the same port would just fail to bind.
    """
    url = f"http://127.0.0.1:{port}/v1/models"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:  # noqa: S310
            return 200 <= (getattr(response, "status", 200) or 200) < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def default_threads() -> int:
    """Leave the machine something to run the simulator and the desktop with."""
    cpus = os.cpu_count() or 4
    return max(1, min(8, cpus - 2))


def server_command(binary: Path, weights: Path, *, port: int, threads: int) -> list[str]:
    command = [
        str(binary),
        "--jinja",
        "-fa",
        "on",
        "-m",
        str(weights),
        "--alias",
        gm.MODEL_REPO.replace("-GGUF", ""),
        "--no-mmproj",
        "--no-ui",
        "--cors-origins",
        "localhost",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--ctx-size",
        str(DEFAULT_CONTEXT),
        "--parallel",
        "1",
        "--threads",
        str(threads),
    ]
    api_key = os.environ.get("GRANITE_API_KEY")
    if api_key:
        command += ["--api-key", api_key]
    return command


class GraniteServer:
    """A llama-server owned by this process, started on demand and stopped on exit."""

    def __init__(
        self,
        *,
        port: int = DEFAULT_PORT,
        threads: int | None = None,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        probe: Callable[[int], bool] = is_responding,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.port = port
        self.threads = threads if threads is not None else default_threads()
        self._popen = popen
        self._probe = probe
        self._sleep = sleep
        self._clock = clock
        self._process: subprocess.Popen | None = None
        self._adopted = False

    @property
    def base_url(self) -> str:
        return endpoint(self.port)

    @property
    def owns_process(self) -> bool:
        """False when we attached to a server someone else was already running."""
        return self._process is not None and not self._adopted

    def start(self, *, timeout_s: float = STARTUP_TIMEOUT_S) -> str:
        """Bring up the endpoint and return its base URL.

        Returns early and touches nothing if a server is already answering: on a
        researcher's machine that is their own, and taking it over or fighting it
        for the port would be worse than using it.
        """
        if self._probe(self.port):
            self._adopted = True
            return self.base_url

        binary = server_binary()
        if binary is None:
            raise ServerError(
                "The local model server is not installed with this copy of Apex. "
                "Coaching needs it; the rest of the app does not."
            )
        weights = gm.model_path()
        if not gm.is_verified(weights):
            raise ServerError(
                f"The Granite weights at {weights} are missing or not the pinned "
                "file, so the coach will not start."
            )

        command = server_command(binary, weights, port=self.port, threads=self.threads)
        try:
            self._process = self._popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(binary.parent),
                **_no_console_window(),
            )
        except OSError as exc:
            raise ServerError(f"Could not start the model server: {exc}") from exc

        deadline = self._clock() + timeout_s
        while self._clock() < deadline:
            if self._process.poll() is not None:
                raise ServerError(
                    f"The model server stopped while starting up (exit "
                    f"{self._process.returncode})."
                )
            if self._probe(self.port):
                return self.base_url
            self._sleep(0.5)

        self.stop()
        raise ServerError(
            f"The model server did not answer within {timeout_s:.0f} seconds."
        )

    def stop(self, *, timeout_s: float = 10.0) -> None:
        """Stop only what we started. A server we adopted is not ours to kill."""
        process, self._process = self._process, None
        if process is None or self._adopted:
            self._adopted = False
            return
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def __enter__(self) -> "GraniteServer":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def _no_console_window() -> dict:
    """Keep a console window from flashing up in the participant's face."""
    if sys.platform != "win32":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
