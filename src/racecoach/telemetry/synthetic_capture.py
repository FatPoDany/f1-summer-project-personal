"""Validated, evidence-preserving TORCS reference robot capture.

Synthetic sessions exercise the collection pipeline and provide a pinned
reference. They are not participant observations and are never labelled with a
human study phase or capture kind.
"""

import hashlib
import json
import os
import subprocess
import threading
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import pandas as pd

from f1coach_core.torcs import is_torcs_export, split_torcs_run
from f1coach_core.workspace import workspace_root
from racecoach.telemetry.run_store import import_run

SYNTHETIC_CAPTURE_SCHEMA_VERSION = "apex-synthetic-capture-v1"
SYNTHETIC_TELEMETRY_SCHEMA_VERSION = "apex-robot-v1"
SYNTHETIC_CAPTURE_KIND = "synthetic-robot"
REFERENCE_PHASE = "reference-pilot"
SYNTHETIC_TELEMETRY_DIR_ENV = "APEX_SYNTHETIC_TELEMETRY_DIR"
TORCS_LOCAL_DIR_ENV = "APEX_TORCS_LOCAL_DIR"

REFERENCE_ROBOT_MODULE = "berniw"
REFERENCE_ROBOT_INDEX = 9
REFERENCE_CAR_ID = "car7-trb1"
REFERENCE_TRACK_ID = "g-track-1"
REFERENCE_TRACK_CATEGORY = "road"
REFERENCE_LAPS = 3
REFERENCE_PRESET_ID = "apex-robot-study-v1"
_HUMAN_CAPTURE_ENV = (
    "APEX_HUMAN_TELEMETRY_DIR",
    "APEX_HUMAN_PARTICIPANT_ID",
    "APEX_HUMAN_PHASE",
)


class CompletedProcess(Protocol):
    returncode: int


Runner = Callable[..., CompletedProcess]


class SyntheticCaptureError(RuntimeError):
    """A synthetic session that could not be finalized, with raw evidence."""

    def __init__(self, message: str, capture_dir: Path) -> None:
        super().__init__(message)
        self.capture_dir = capture_dir


class SyntheticCaptureCancelled(SyntheticCaptureError):
    """A facilitator deliberately stopped the synthetic session."""


class ManagedSyntheticTorcsRunner:
    """A subprocess runner whose active synthetic TORCS process can be stopped."""

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
class RobotIdentity:
    module: str
    index: int
    car_id: str

    def __post_init__(self) -> None:
        if self.module != REFERENCE_ROBOT_MODULE:
            raise ValueError(f"robot module must be {REFERENCE_ROBOT_MODULE!r}")
        if (
            isinstance(self.index, bool)
            or not isinstance(self.index, int)
            or self.index != REFERENCE_ROBOT_INDEX
        ):
            raise ValueError(f"robot index must be {REFERENCE_ROBOT_INDEX}")
        if self.car_id != REFERENCE_CAR_ID:
            raise ValueError(f"robot car id must be {REFERENCE_CAR_ID!r}")

    def to_dict(self) -> dict:
        return {
            "module": self.module,
            "index": self.index,
            "car_id": self.car_id,
        }


def _reference_robot() -> RobotIdentity:
    return RobotIdentity(
        module=REFERENCE_ROBOT_MODULE,
        index=REFERENCE_ROBOT_INDEX,
        car_id=REFERENCE_CAR_ID,
    )


@dataclass(frozen=True)
class RobotStudyPreset:
    preset_id: str
    display_name: str
    track_id: str
    track_category: str
    car_id: str
    laps: int
    race_config: Path

    def __post_init__(self) -> None:
        if self.preset_id != REFERENCE_PRESET_ID:
            raise ValueError(f"robot preset id must be {REFERENCE_PRESET_ID!r}")
        if (
            not isinstance(self.display_name, str)
            or not self.display_name.strip()
            or len(self.display_name) > 128
            or "\x00" in self.display_name
        ):
            raise ValueError("display name must be 1-128 visible characters")
        if self.track_id != REFERENCE_TRACK_ID:
            raise ValueError(f"robot track id must be {REFERENCE_TRACK_ID!r}")
        if self.track_category != REFERENCE_TRACK_CATEGORY:
            raise ValueError(
                f"robot track category must be {REFERENCE_TRACK_CATEGORY!r}"
            )
        if self.car_id != REFERENCE_CAR_ID:
            raise ValueError(f"robot car id must be {REFERENCE_CAR_ID!r}")
        if (
            isinstance(self.laps, bool)
            or not isinstance(self.laps, int)
            or self.laps != REFERENCE_LAPS
        ):
            raise ValueError(f"robot laps must be {REFERENCE_LAPS}")
        race_config = Path(self.race_config)
        if race_config.suffix.lower() != ".xml":
            raise ValueError("robot race configuration must be an XML file")
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
class SyntheticCaptureConfig:
    torcs_binary: Path
    preset: RobotStudyPreset
    count: int = 3
    torcs_args: tuple[str, ...] = ()
    robot: RobotIdentity = field(default_factory=_reference_robot)
    phase: str = field(default=REFERENCE_PHASE, init=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.count, bool)
            or not isinstance(self.count, int)
            or not 1 <= self.count <= 20
        ):
            raise ValueError("synthetic batch count must be an integer from 1 to 20")
        object.__setattr__(self, "torcs_binary", Path(self.torcs_binary))
        object.__setattr__(self, "torcs_args", tuple(self.torcs_args))
        for argument in self.torcs_args:
            if not isinstance(argument, str) or not argument or "\x00" in argument:
                raise ValueError(
                    "TORCS arguments must be non-empty strings without NUL bytes"
                )
            if argument.startswith(("-r", "-R")):
                raise ValueError("the robot preset cannot be overridden by TORCS arguments")


@dataclass(frozen=True)
class SyntheticSessionResult:
    session_id: str
    capture_dir: Path
    run_dirs: tuple[Path, ...]


@dataclass(frozen=True)
class SyntheticProgress:
    current: int
    total: int
    session_id: str
    status: str
    message: str


@dataclass(frozen=True)
class SyntheticSessionOutcome:
    session_id: str
    status: str
    capture_dir: Path | None = None
    run_dirs: tuple[Path, ...] = ()
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "capture_dir": None if self.capture_dir is None else str(self.capture_dir),
            "run_ids": [path.name for path in self.run_dirs],
            "error": self.error,
        }


@dataclass(frozen=True)
class SyntheticBatchResult:
    batch_dir: Path
    outcomes: tuple[SyntheticSessionOutcome, ...]

    @property
    def run_dirs(self) -> tuple[Path, ...]:
        return tuple(path for outcome in self.outcomes for path in outcome.run_dirs)


def synthetic_captures_root() -> Path:
    return workspace_root() / "captures" / "synthetic"


def default_robot_study_preset(torcs_binary: str | Path) -> RobotStudyPreset:
    runtime_root = Path(torcs_binary).resolve().parent.parent
    race_config = (
        runtime_root
        / "share"
        / "games"
        / "torcs"
        / "config"
        / "raceman"
        / "apexrobotstudy.xml"
    )
    return RobotStudyPreset(
        preset_id=REFERENCE_PRESET_ID,
        display_name="Apex Robot Study v1",
        track_id=REFERENCE_TRACK_ID,
        track_category=REFERENCE_TRACK_CATEGORY,
        car_id=REFERENCE_CAR_ID,
        laps=REFERENCE_LAPS,
        race_config=race_config,
    )


def synthetic_session_id(config: SyntheticCaptureConfig, ordinal: int) -> str:
    if (
        isinstance(ordinal, bool)
        or not isinstance(ordinal, int)
        or not 1 <= ordinal <= config.count
    ):
        raise ValueError(f"session ordinal must be from 1 to {config.count}")
    return f"SIM-BERNIW9-{ordinal:03d}"


def synthetic_command(config: SyntheticCaptureConfig) -> tuple[str, ...]:
    binary = config.torcs_binary
    if not binary.is_file():
        raise ValueError(
            f"TORCS executable not found: {binary}. Build it with "
            "integrations/torcs-1.3.9/build.sh install or pass --torcs."
        )
    if not os.access(binary, os.X_OK):
        raise ValueError(f"TORCS executable is not runnable: {binary}")
    _validate_robot_preset_xml(config.preset)
    return (
        str(binary),
        "-r",
        str(config.preset.race_config),
        *config.torcs_args,
    )


def capture_synthetic_session(
    config: SyntheticCaptureConfig,
    *,
    runner: Runner = subprocess.run,
    base_env: dict[str, str] | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> SyntheticSessionResult:
    """Run one pinned reference session and register only complete evidence."""
    command = list(synthetic_command(config))
    batch_dir = _new_batch_dir()
    return _capture_synthetic_session(
        config,
        ordinal=1,
        batch_dir=batch_dir,
        command=command,
        runner=runner,
        base_env=base_env,
        stop_requested=stop_requested,
    )


def _capture_synthetic_session(
    config: SyntheticCaptureConfig,
    *,
    ordinal: int,
    batch_dir: Path,
    command: list[str],
    runner: Runner,
    base_env: dict[str, str] | None,
    stop_requested: Callable[[], bool] | None,
) -> SyntheticSessionResult:
    session_id = synthetic_session_id(config, ordinal)
    capture_dir = batch_dir / session_id
    capture_dir.mkdir()
    manifest: dict = {
        "schema_version": SYNTHETIC_CAPTURE_SCHEMA_VERSION,
        "status": "running",
        "session_id": session_id,
        "phase": config.phase,
        "capture": SYNTHETIC_CAPTURE_KIND,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "robot": config.robot.to_dict(),
        "study_preset": config.preset.to_dict(),
        "torcs_binary": str(config.torcs_binary),
        "torcs_args": list(config.torcs_args),
        "command": command,
        "runs": [],
    }
    _write_manifest(capture_dir, manifest)

    environment = dict(os.environ if base_env is None else base_env)
    for name in _HUMAN_CAPTURE_ENV:
        environment.pop(name, None)
    environment[SYNTHETIC_TELEMETRY_DIR_ENV] = str(capture_dir)
    environment[TORCS_LOCAL_DIR_ENV] = str(
        workspace_root()
        / "torcs-profiles"
        / "synthetic"
        / batch_dir.name
        / session_id
    )
    try:
        completed = runner(command, env=environment, check=False)
    except OSError as exc:
        manifest.update(status="launch_failed", error=str(exc))
        _finish_manifest(capture_dir, manifest)
        raise SyntheticCaptureError(
            f"could not start TORCS: {exc}; raw files remain in {capture_dir}",
            capture_dir,
        ) from exc

    manifest["returncode"] = int(completed.returncode)
    if stop_requested is not None and stop_requested():
        manifest["status"] = "cancelled"
        _finish_manifest(capture_dir, manifest)
        raise SyntheticCaptureCancelled(
            f"synthetic capture stopped by the user; raw files remain in {capture_dir}",
            capture_dir,
        )
    if completed.returncode != 0:
        manifest["status"] = "simulator_failed"
        _finish_manifest(capture_dir, manifest)
        raise SyntheticCaptureError(
            f"TORCS exited with status {completed.returncode}; raw files remain in "
            f"{capture_dir}",
            capture_dir,
        )

    csv_files = sorted(capture_dir.glob("*.csv"))
    try:
        validated = [
            (path, *_validated_robot_frame(path, config)) for path in csv_files
        ]
    except SyntheticCaptureError as exc:
        manifest.update(status="invalid_telemetry", error=str(exc))
        _finish_manifest(capture_dir, manifest)
        raise
    non_empty = [entry for entry in validated if len(entry[1])]
    if not non_empty:
        manifest["status"] = "no_data"
        _finish_manifest(capture_dir, manifest)
        raise SyntheticCaptureError(
            f"TORCS produced no non-empty telemetry CSV in {capture_dir}",
            capture_dir,
        )
    if len(non_empty) != 1:
        message = f"expected one robot telemetry CSV; observed {len(non_empty)}"
        manifest.update(status="invalid_telemetry", error=message)
        _finish_manifest(capture_dir, manifest)
        raise SyntheticCaptureError(f"{message} in {capture_dir}", capture_dir)

    path, frame, complete_laps = non_empty[0]
    digest = _sha256(path)
    try:
        run_dir = import_run(path, capture=SYNTHETIC_CAPTURE_KIND)
    except (OSError, ValueError) as exc:
        message = f"could not register validated telemetry in the run store: {exc}"
        manifest.update(status="registration_failed", error=message)
        _finish_manifest(capture_dir, manifest)
        raise SyntheticCaptureError(
            f"{message}; raw files remain in {capture_dir}", capture_dir
        ) from exc
    manifest.update(
        status="complete",
        runs=[
            {
                "file": path.name,
                "sha256": digest,
                "samples": len(frame),
                "complete_laps": list(complete_laps),
                "run_id": run_dir.name,
            }
        ],
    )
    _finish_manifest(capture_dir, manifest)
    return SyntheticSessionResult(
        session_id=session_id,
        capture_dir=capture_dir,
        run_dirs=(run_dir,),
    )


def capture_synthetic_batch(
    config: SyntheticCaptureConfig,
    *,
    runner: Runner = subprocess.run,
    base_env: dict[str, str] | None = None,
    stop_requested: Callable[[], bool] | None = None,
    on_progress: Callable[[SyntheticProgress], None] | None = None,
) -> SyntheticBatchResult:
    """Run an isolated sequential reference batch and retain every outcome."""
    command = list(synthetic_command(config))
    batch_dir = _new_batch_dir()
    manifest: dict = {
        "schema_version": SYNTHETIC_CAPTURE_SCHEMA_VERSION,
        "status": "running",
        "count": config.count,
        "phase": config.phase,
        "capture": SYNTHETIC_CAPTURE_KIND,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "robot": config.robot.to_dict(),
        "study_preset": config.preset.to_dict(),
        "command": command,
        "sessions": [],
    }
    _write_batch_manifest(batch_dir, manifest)
    outcomes: list[SyntheticSessionOutcome] = []

    for ordinal in range(1, config.count + 1):
        session_id = synthetic_session_id(config, ordinal)
        if stop_requested is not None and stop_requested():
            outcomes.extend(_not_started_outcomes(config, ordinal))
            break
        _emit_progress(
            on_progress,
            SyntheticProgress(
                ordinal,
                config.count,
                session_id,
                "running",
                f"Running {session_id}",
            ),
        )
        try:
            result = _capture_synthetic_session(
                config,
                ordinal=ordinal,
                batch_dir=batch_dir,
                command=command,
                runner=runner,
                base_env=base_env,
                stop_requested=stop_requested,
            )
        except SyntheticCaptureCancelled as exc:
            outcome = SyntheticSessionOutcome(
                session_id=session_id,
                status="cancelled",
                capture_dir=exc.capture_dir,
                error=str(exc),
            )
            outcomes.append(outcome)
            _emit_progress(
                on_progress,
                SyntheticProgress(
                    ordinal,
                    config.count,
                    session_id,
                    "cancelled",
                    str(exc),
                ),
            )
            outcomes.extend(_not_started_outcomes(config, ordinal + 1))
            break
        except SyntheticCaptureError as exc:
            outcome = SyntheticSessionOutcome(
                session_id=session_id,
                status="failed",
                capture_dir=exc.capture_dir,
                error=str(exc),
            )
            outcomes.append(outcome)
            _emit_progress(
                on_progress,
                SyntheticProgress(
                    ordinal,
                    config.count,
                    session_id,
                    "failed",
                    str(exc),
                ),
            )
            continue

        outcome = SyntheticSessionOutcome(
            session_id=session_id,
            status="complete",
            capture_dir=result.capture_dir,
            run_dirs=result.run_dirs,
        )
        outcomes.append(outcome)
        _emit_progress(
            on_progress,
            SyntheticProgress(
                ordinal,
                config.count,
                session_id,
                "complete",
                f"Completed {session_id}",
            ),
        )

    statuses = {outcome.status for outcome in outcomes}
    if "cancelled" in statuses or "not_started" in statuses:
        status = "cancelled"
    elif "failed" in statuses:
        status = "complete_with_failures"
    else:
        status = "complete"
    manifest.update(
        status=status,
        finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
        sessions=[outcome.to_dict() for outcome in outcomes],
    )
    _write_batch_manifest(batch_dir, manifest)
    return SyntheticBatchResult(batch_dir=batch_dir, outcomes=tuple(outcomes))


def _not_started_outcomes(
    config: SyntheticCaptureConfig, first_ordinal: int
) -> list[SyntheticSessionOutcome]:
    return [
        SyntheticSessionOutcome(
            session_id=synthetic_session_id(config, ordinal),
            status="not_started",
            error="batch cancelled before launch",
        )
        for ordinal in range(first_ordinal, config.count + 1)
    ]


def _emit_progress(
    callback: Callable[[SyntheticProgress], None] | None,
    progress: SyntheticProgress,
) -> None:
    if callback is not None:
        callback(progress)


def _validate_robot_preset_xml(preset: RobotStudyPreset) -> None:
    path = preset.race_config
    if not path.is_file():
        raise ValueError(f"TORCS robot race configuration not found: {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"could not read TORCS robot race configuration: {exc}") from exc
    if len(raw) > 1024 * 1024 or b"<!ENTITY" in raw.upper():
        raise ValueError("TORCS robot race configuration is not safe XML")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(
            f"TORCS robot race configuration is not parseable XML: {exc}"
        ) from exc

    if root.tag != "params" or root.get("name") != preset.display_name:
        raise ValueError(
            f"robot preset name must be {preset.display_name!r}"
        )
    tracks = _required_section(root, "Tracks")
    track = _required_section(tracks, "1")
    _require_xml_value(track, "attstr", "name", preset.track_id, "track")
    _require_xml_value(
        track, "attstr", "category", preset.track_category, "track category"
    )
    race = _required_section(root, "Apex Robot Study")
    _require_xml_value(race, "attnum", "laps", str(preset.laps), "laps")
    drivers = _required_section(root, "Drivers")
    _require_xml_value(drivers, "attnum", "maximum number", "1", "driver count")
    _require_xml_value(
        drivers, "attstr", "focused module", REFERENCE_ROBOT_MODULE, "robot module"
    )
    _require_xml_value(
        drivers, "attnum", "focused idx", str(REFERENCE_ROBOT_INDEX), "robot index"
    )
    entries = [child for child in drivers if child.tag == "section"]
    if len(entries) != 1 or entries[0].get("name") != "1":
        raise ValueError("robot preset must contain exactly one driver entry")
    _require_xml_value(
        entries[0], "attnum", "idx", str(REFERENCE_ROBOT_INDEX), "robot index"
    )
    _require_xml_value(
        entries[0], "attstr", "module", REFERENCE_ROBOT_MODULE, "robot module"
    )


def _required_section(parent: ET.Element, name: str) -> ET.Element:
    matches = [
        child
        for child in parent
        if child.tag == "section" and child.get("name") == name
    ]
    if len(matches) != 1:
        raise ValueError(f"robot preset must contain exactly one {name!r} section")
    return matches[0]


def _require_xml_value(
    parent: ET.Element,
    tag: str,
    name: str,
    expected: str,
    label: str,
) -> None:
    matches = [
        child for child in parent if child.tag == tag and child.get("name") == name
    ]
    observed = None if len(matches) != 1 else matches[0].get("val")
    if observed != expected:
        raise ValueError(f"robot preset {label} must be {expected!r}; observed {observed!r}")


def _new_batch_dir() -> Path:
    workspace = workspace_root()
    captures = workspace / "captures"
    if captures.is_symlink():
        raise ValueError("synthetic capture storage cannot be a symbolic link")
    captures.mkdir(parents=True, exist_ok=True)
    workspace_resolved = workspace.resolve(strict=True)
    captures_resolved = captures.resolve(strict=True)
    if captures_resolved.parent != workspace_resolved:
        raise ValueError("synthetic capture storage resolves outside the workspace")

    root = captures / "synthetic"
    if root.is_symlink():
        raise ValueError("synthetic capture storage cannot be a symbolic link")
    root.mkdir(exist_ok=True)
    if root.resolve(strict=True).parent != captures_resolved:
        raise ValueError("synthetic capture storage resolves outside the workspace")
    stem = f"batch-{datetime.now(UTC):%Y%m%d-%H%M%S}"
    destination = root / stem
    counter = 2
    while destination.exists():
        destination = root / f"{stem}-{counter}"
        counter += 1
    destination.mkdir()
    return destination


def _validated_robot_frame(
    path: Path, config: SyntheticCaptureConfig
) -> tuple[pd.DataFrame, tuple[int, ...]]:
    if not is_torcs_export(path):
        raise SyntheticCaptureError(
            f"{path.name} is not a supported TORCS telemetry CSV", path.parent
        )
    try:
        frame = pd.read_csv(path, comment="#", low_memory=False)
    except Exception as exc:
        raise SyntheticCaptureError(
            f"{path.name} is not parseable CSV: {exc}", path.parent
        ) from exc
    if frame.empty:
        return frame, ()

    expected_text = {
        "schema_version": SYNTHETIC_TELEMETRY_SCHEMA_VERSION,
        "driver_module": config.robot.module,
        "car_model": config.robot.car_id,
        "track_internal_name": config.preset.track_id,
    }
    for column, expected in expected_text.items():
        if column not in frame:
            raise SyntheticCaptureError(
                f"{path.name} is missing robot evidence column {column}", path.parent
            )
        observed = {str(value) for value in frame[column].dropna().unique()}
        if observed != {expected}:
            raise SyntheticCaptureError(
                f"{path.name} has {column}={sorted(observed)!r}; expected {expected!r}",
                path.parent,
            )

    _require_numeric_evidence(
        path, frame, "driver_index", float(config.robot.index), all_rows=True
    )
    _require_numeric_evidence(
        path, frame, "remaining_laps", float(config.preset.laps), all_rows=False
    )
    try:
        complete = tuple(lap for lap in split_torcs_run(path) if lap.complete)
    except Exception as exc:
        raise SyntheticCaptureError(
            f"{path.name} cannot be split into complete laps: {exc}", path.parent
        ) from exc
    if len(complete) != config.preset.laps:
        raise SyntheticCaptureError(
            f"{path.name} has {len(complete)} complete laps; expected {config.preset.laps}",
            path.parent,
        )
    lap_labels = tuple(lap.lap_label for lap in complete)
    if len(set(lap_labels)) != config.preset.laps:
        raise SyntheticCaptureError(
            f"{path.name} must contain {config.preset.laps} distinct lap labels; "
            f"observed {list(lap_labels)}",
            path.parent,
        )
    return frame, lap_labels


def _require_numeric_evidence(
    path: Path,
    frame: pd.DataFrame,
    column: str,
    expected: float,
    *,
    all_rows: bool,
) -> None:
    if column not in frame:
        raise SyntheticCaptureError(
            f"{path.name} is missing robot evidence column {column}", path.parent
        )
    observed = pd.to_numeric(frame[column], errors="coerce").dropna()
    if all_rows:
        matches = not observed.empty and bool((observed == expected).all())
    else:
        matches = not observed.empty and float(observed.iloc[0]) == expected
    if not matches:
        value = None if observed.empty else observed.iloc[0]
        raise SyntheticCaptureError(
            f"{path.name} has {column}={value!r}; expected {expected:g}", path.parent
        )


def _finish_manifest(capture_dir: Path, manifest: dict) -> None:
    manifest["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    _write_manifest(capture_dir, manifest)


def _write_manifest(capture_dir: Path, manifest: dict) -> None:
    temporary = capture_dir / ".manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary.replace(capture_dir / "manifest.json")


def _write_batch_manifest(batch_dir: Path, manifest: dict) -> None:
    temporary = batch_dir / ".batch-manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary.replace(batch_dir / "batch-manifest.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
