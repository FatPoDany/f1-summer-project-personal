"""Launch opt-in TORCS human telemetry capture and register completed runs.

The native human driver writes only when ``APEX_HUMAN_TELEMETRY_DIR`` is set.
This module owns that directory, pseudonymous study metadata, process launch,
validation, hashing, and import into the normal run store. It never sends or
changes an actuator value.
"""

import hashlib
import json
import os
import re
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import pandas as pd

from f1coach_core.torcs import is_torcs_export
from f1coach_core.workspace import workspace_root
from racecoach.telemetry.run_store import import_run
from racecoach.telemetry.torcs_runtime import torcs_raceman_dir

CAPTURE_SCHEMA_VERSION = "apex-human-capture-v1"
TELEMETRY_DIR_ENV = "APEX_HUMAN_TELEMETRY_DIR"
PARTICIPANT_ENV = "APEX_HUMAN_PARTICIPANT_ID"
PHASE_ENV = "APEX_HUMAN_PHASE"
TORCS_LOCAL_DIR_ENV = "APEX_TORCS_LOCAL_DIR"
_SAFE_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class CompletedProcess(Protocol):
    returncode: int


Runner = Callable[..., CompletedProcess]


class HumanCaptureError(RuntimeError):
    """A capture that could not be finalized, with its evidence directory."""

    def __init__(self, message: str, capture_dir: Path) -> None:
        super().__init__(message)
        self.capture_dir = capture_dir


class HumanCaptureCancelled(HumanCaptureError):
    """The participant or facilitator deliberately stopped a capture."""


class ManagedTorcsRunner:
    """A subprocess runner whose active TORCS process can be stopped by the UI."""

    def __init__(self, *, popen_factory: Callable = subprocess.Popen) -> None:
        self._popen_factory = popen_factory
        self._lock = threading.Lock()
        self._stop_requested = threading.Event()
        self._process = None

    def __call__(
        self, command: list[str], *, env: dict[str, str], check: bool = False
    ) -> subprocess.CompletedProcess:
        process = self._popen_factory(command, env=env)
        with self._lock:
            self._process = process
            stop_now = self._stop_requested.is_set()
        if stop_now and process.poll() is None:
            process.terminate()
        returncode = process.wait()
        with self._lock:
            if self._process is process:
                self._process = None
        completed = subprocess.CompletedProcess(command, returncode)
        if check and returncode:
            raise subprocess.CalledProcessError(returncode, command)
        return completed

    def request_stop(self) -> None:
        self._stop_requested.set()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()


@dataclass(frozen=True)
class TorcsStudyPreset:
    preset_id: str
    display_name: str
    track_id: str
    track_category: str
    car_id: str
    laps: int
    race_config: Path

    def __post_init__(self) -> None:
        _validate_slug(self.preset_id, "preset id")
        _validate_slug(self.track_id, "track id")
        _validate_slug(self.track_category, "track category")
        _validate_slug(self.car_id, "car id")
        if (
            not isinstance(self.display_name, str)
            or not self.display_name.strip()
            or len(self.display_name) > 128
            or "\x00" in self.display_name
        ):
            raise ValueError("display name must be 1-128 visible characters")
        if isinstance(self.laps, bool) or not isinstance(self.laps, int) or self.laps < 1:
            raise ValueError("laps must be a positive integer")
        race_config = Path(self.race_config)
        if race_config.suffix.lower() != ".xml":
            raise ValueError("race configuration must be an XML file")
        object.__setattr__(self, "race_config", race_config)

    def to_dict(self) -> dict:
        return {
            "preset_id": self.preset_id,
            "display_name": self.display_name,
            "track_id": self.track_id,
            "track_category": self.track_category,
            "car_id": self.car_id,
            "laps": self.laps,
            "race_config": str(self.race_config),
        }


@dataclass(frozen=True)
class HumanCaptureConfig:
    participant_id: str
    phase: str
    torcs_binary: Path
    torcs_args: tuple[str, ...] = ()
    preset: TorcsStudyPreset | None = None

    def __post_init__(self) -> None:
        _validate_slug(self.participant_id, "participant id")
        _validate_slug(self.phase, "phase")
        object.__setattr__(self, "torcs_binary", Path(self.torcs_binary))
        object.__setattr__(self, "torcs_args", tuple(self.torcs_args))
        for argument in self.torcs_args:
            if not isinstance(argument, str) or not argument or "\x00" in argument:
                raise ValueError("TORCS arguments must be non-empty strings without NUL bytes")
        if self.preset is not None and any(
            argument.startswith(("-r", "-R")) for argument in self.torcs_args
        ):
            raise ValueError("a study preset cannot be combined with -r or -R arguments")


@dataclass(frozen=True)
class HumanCaptureResult:
    capture_dir: Path
    run_dirs: tuple[Path, ...]


def default_study_preset(torcs_binary: str | Path) -> TorcsStudyPreset:
    race_config = torcs_raceman_dir(torcs_binary) / "apexstudy.xml"
    return TorcsStudyPreset(
        preset_id="apex-study-v1",
        display_name="Apex Study v1",
        track_id="g-track-1",
        track_category="road",
        car_id="car7-trb1",
        laps=5,
        race_config=race_config,
    )


def human_captures_root() -> Path:
    return workspace_root() / "captures" / "human"


def capture_human_runs(
    config: HumanCaptureConfig,
    *,
    runner: Runner = subprocess.run,
    base_env: dict[str, str] | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> HumanCaptureResult:
    """Run TORCS and import every validated human-driver CSV it produces."""
    binary = config.torcs_binary
    if not binary.is_file():
        raise ValueError(
            f"TORCS executable not found: {binary}. Build it with "
            "integrations/torcs-1.3.9/build.sh install or pass --torcs."
        )
    if not os.access(binary, os.X_OK):
        raise ValueError(f"TORCS executable is not runnable: {binary}")
    if config.preset is not None and not config.preset.race_config.is_file():
        raise ValueError(
            f"TORCS study race configuration not found: {config.preset.race_config}"
        )

    if config.preset is None:
        command = [str(binary), *config.torcs_args]
    else:
        command = [
            str(binary),
            "-R",
            str(config.preset.race_config),
            *config.torcs_args,
        ]

    capture_dir = _new_capture_dir(config)
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    manifest: dict = {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "status": "running",
        "participant_id": config.participant_id,
        "phase": config.phase,
        "started_at": started_at,
        "torcs_binary": str(binary),
        "torcs_args": list(config.torcs_args),
        "command": command,
        "study_preset": None if config.preset is None else config.preset.to_dict(),
        "runs": [],
    }
    _write_manifest(capture_dir, manifest)

    environment = dict(os.environ if base_env is None else base_env)
    environment[TELEMETRY_DIR_ENV] = str(capture_dir)
    environment[PARTICIPANT_ENV] = config.participant_id
    environment[PHASE_ENV] = config.phase
    if config.preset is not None:
        environment[TORCS_LOCAL_DIR_ENV] = str(
            workspace_root() / "torcs-profiles" / config.preset.preset_id
        )
    try:
        completed = runner(command, env=environment, check=False)
    except OSError as exc:
        manifest.update(status="launch_failed", error=str(exc))
        _write_manifest(capture_dir, manifest)
        raise HumanCaptureError(f"could not start TORCS: {exc}", capture_dir) from exc

    manifest["returncode"] = int(completed.returncode)
    if stop_requested is not None and stop_requested():
        manifest["status"] = "cancelled"
        manifest["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        _write_manifest(capture_dir, manifest)
        raise HumanCaptureCancelled(
            f"capture stopped by the user; raw files remain in {capture_dir}",
            capture_dir,
        )
    if completed.returncode != 0:
        manifest["status"] = "simulator_failed"
        manifest["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        _write_manifest(capture_dir, manifest)
        raise HumanCaptureError(
            f"TORCS exited with status {completed.returncode}; raw files remain in {capture_dir}",
            capture_dir,
        )

    csv_files = sorted(capture_dir.glob("*.csv"))
    try:
        frames = [_validated_frame(path) for path in csv_files]
        if config.preset is not None:
            for path, frame in zip(csv_files, frames, strict=True):
                _validate_preset_frame(path, frame, config.preset)
    except HumanCaptureError as exc:
        manifest.update(
            status="invalid_telemetry",
            error=str(exc),
            finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        _write_manifest(capture_dir, manifest)
        raise
    non_empty = [(path, frame) for path, frame in zip(csv_files, frames, strict=True) if len(frame)]
    if not non_empty:
        manifest.update(
            status="no_data",
            finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        _write_manifest(capture_dir, manifest)
        raise HumanCaptureError(
            f"TORCS produced no non-empty telemetry CSV in {capture_dir}. "
            "Select a human driver and complete a Practice or Quick Race session.",
            capture_dir,
        )

    run_dirs = []
    run_records = []
    for path, frame in non_empty:
        run_dir = import_run(path, capture="human-driver")
        run_dirs.append(run_dir)
        run_records.append(
            {
                "file": path.name,
                "sha256": _sha256(path),
                "samples": len(frame),
                "run_id": run_dir.name,
            }
        )
    manifest.update(
        status="complete",
        finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
        runs=run_records,
    )
    _write_manifest(capture_dir, manifest)
    return HumanCaptureResult(capture_dir=capture_dir, run_dirs=tuple(run_dirs))


def _validate_slug(value: str, label: str) -> None:
    if not isinstance(value, str) or _SAFE_SLUG.fullmatch(value) is None:
        raise ValueError(
            f"{label} must be a 1-64 character pseudonymous id using only "
            "letters, numbers, '.', '_' or '-'"
        )


def _new_capture_dir(config: HumanCaptureConfig) -> Path:
    root = human_captures_root()
    root.mkdir(parents=True, exist_ok=True)
    stem = f"{config.participant_id}-{config.phase}-{datetime.now(UTC):%Y%m%d-%H%M%S}"
    destination = root / stem
    counter = 2
    while destination.exists():
        destination = root / f"{stem}-{counter}"
        counter += 1
    destination.mkdir()
    return destination


def _validated_frame(path: Path) -> pd.DataFrame:
    if not is_torcs_export(path):
        raise HumanCaptureError(
            f"{path.name} is not a supported TORCS telemetry CSV",
            path.parent,
        )
    try:
        return pd.read_csv(path, comment="#", low_memory=False)
    except Exception as exc:
        raise HumanCaptureError(
            f"{path.name} is not parseable CSV: {exc}", path.parent
        ) from exc


def _validate_preset_frame(
    path: Path, frame: pd.DataFrame, preset: TorcsStudyPreset
) -> None:
    """Reject evidence whose observed study conditions differ from the assignment."""
    if frame.empty:
        return

    expected_text = {
        "track_internal_name": preset.track_id,
        "car_model": preset.car_id,
        "driver_module": "human",
    }
    for column, expected in expected_text.items():
        if column not in frame:
            raise HumanCaptureError(
                f"{path.name} is missing preset evidence column {column}", path.parent
            )
        observed = {str(value) for value in frame[column].dropna().unique()}
        if observed != {expected}:
            raise HumanCaptureError(
                f"{path.name} has {column}={sorted(observed)!r}; expected {expected!r}",
                path.parent,
            )

    laps_column = "remaining_laps"
    if laps_column not in frame:
        raise HumanCaptureError(
            f"{path.name} is missing preset evidence column {laps_column}", path.parent
        )
    observed_laps = pd.to_numeric(frame[laps_column], errors="coerce").dropna()
    if observed_laps.empty or float(observed_laps.iloc[0]) != float(preset.laps):
        observed = None if observed_laps.empty else observed_laps.iloc[0]
        raise HumanCaptureError(
            f"{path.name} has initial {laps_column}={observed!r}; expected {preset.laps}",
            path.parent,
        )


def _write_manifest(capture_dir: Path, manifest: dict) -> None:
    temporary = capture_dir / ".manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary.replace(capture_dir / "manifest.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
