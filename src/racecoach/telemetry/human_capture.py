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

from f1coach_core.lap import StudyIdentity
from f1coach_core.torcs import is_torcs_export
from f1coach_core.workspace import workspace_root
from racecoach.telemetry.run_store import import_run
from racecoach.telemetry.screen_capture import ScreenRecorder
from racecoach.telemetry.torcs_runtime import (
    torcs_data_root,
    torcs_launch_cwd,
    torcs_raceman_dir,
)

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

    def __init__(
        self,
        *,
        popen_factory: Callable = subprocess.Popen,
        stop_grace_s: float = 5.0,
    ) -> None:
        self._popen_factory = popen_factory
        self._stop_grace_s = max(0.0, float(stop_grace_s))
        self._lock = threading.Lock()
        self._stop_requested = threading.Event()
        self._process_done = threading.Event()
        self._stopping_process = None
        self._process = None

    def __call__(
        self,
        command: list[str],
        *,
        env: dict[str, str],
        check: bool = False,
        cwd: str | None = None,
    ) -> subprocess.CompletedProcess:
        process = self._popen_factory(command, env=env, cwd=cwd)
        self._process_done.clear()
        with self._lock:
            self._process = process
            stop_now = self._stop_requested.is_set()
        if stop_now and process.poll() is None:
            self._stop_process(process)
        try:
            returncode = process.wait()
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None
                if self._stopping_process is process:
                    self._stopping_process = None
            self._process_done.set()
        completed = subprocess.CompletedProcess(command, returncode)
        if check and returncode:
            raise subprocess.CalledProcessError(returncode, command)
        return completed

    def request_stop(self) -> None:
        self._stop_requested.set()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            self._stop_process(process)

    def _stop_process(self, process) -> None:
        """Ask TORCS to stop, then force it down without blocking the caller."""
        with self._lock:
            if self._stopping_process is process:
                return
            self._stopping_process = process
        try:
            process.terminate()
        except OSError:
            pass

        def force_after_grace() -> None:
            if self._process_done.wait(self._stop_grace_s):
                return
            try:
                running = process.poll() is None
            except OSError:
                running = False
            if running:
                try:
                    process.kill()
                except OSError:
                    pass

        threading.Thread(target=force_after_grace, daemon=True).start()


@dataclass(frozen=True)
class TorcsStudyPreset:
    preset_id: str
    display_name: str
    track_id: str
    track_category: str
    car_id: str
    laps: int
    race_config: Path
    # Every module on the grid besides the participant, in the order the race
    # configuration lists them. Recorded because a race driven in traffic is not
    # the same race as one driven alone -- finish position stops being a
    # constant, and the lap-to-lap spread widens for a reason that has nothing to
    # do with the driver. A capture that does not say which of the two it was
    # cannot be pooled with either.
    opponents: tuple[str, ...] = ()
    # TORCS ships a 640x480 window. That is too small to place a car accurately,
    # and enlarging it by hand does not work: the renderer keeps its viewport at
    # the configured view size and simply re-centres it, so a maximised window
    # shows the race in a small box with black around it. Fixing the size here
    # gives every participant the same usable window and removes the reason to
    # touch the window at all. Frozen into the manifest with the rest of the
    # assignment, because a different render size is a different condition.
    window_width: int = 1280
    window_height: int = 720

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
        object.__setattr__(self, "opponents", tuple(self.opponents))
        for module in self.opponents:
            _validate_slug(module, "opponent module")
        for name, value in (
            ("window width", self.window_width),
            ("window height", self.window_height),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 320 <= value <= 7680:
                raise ValueError(f"{name} must be an integer from 320 to 7680")
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
            "opponents": list(self.opponents),
            "window_width": self.window_width,
            "window_height": self.window_height,
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
            raise ValueError(
                "a study preset cannot be combined with -r or -R arguments; "
                "pass --no-preset to drive a race of your own"
            )


@dataclass(frozen=True)
class HumanCaptureResult:
    capture_dir: Path
    run_dirs: tuple[Path, ...]


# TORCS does not take the car from the race configuration. It reads it from the
# human driver's own profile, one file for the whole install, so a preset that
# only *names* a car names one it is not driving. `_write_driver_car` writes that
# profile immediately before launch, the way screen.xml and graph.xml are
# written, which is what makes this field decide rather than describe.
STUDY_CAR_ID = "car7-trb1"

# The car the other half of this project drives. Same TRB1 class, different
# vehicle: pooling absolute speeds or brake points across two cars measures the
# cars.
TEAM_CAR_ID = "car1-trb1"

# The three robots their practice grid carries. Module names only; which index
# of each module -- and so which setup it runs -- lives in the race
# configuration, because that file is the thing that is shipped, verified and
# reproducible. Naming the indices here as well would give two places to state
# one fact and no way to tell which one the race actually used.
TEAM_OPPONENTS = ("berniw", "bt", "olethros")


def _study_preset(
    torcs_binary: str | Path,
    *,
    preset_id: str,
    display_name: str,
    track_id: str,
    race_config_name: str,
    car_id: str = STUDY_CAR_ID,
    laps: int = 3,
    opponents: tuple[str, ...] = (),
) -> TorcsStudyPreset:
    return TorcsStudyPreset(
        preset_id=preset_id,
        display_name=display_name,
        track_id=track_id,
        track_category="road",
        car_id=car_id,
        laps=laps,
        race_config=torcs_raceman_dir(torcs_binary) / race_config_name,
        opponents=opponents,
    )


def study_presets(torcs_binary: str | Path) -> tuple[TorcsStudyPreset, ...]:
    """Every assignment a facilitator may pick, the default first.

    The default is the one that matches the other half of this project. Its kit
    is frozen on ``ibmf1-practice-v5`` and every race it has stored was driven
    there, so a race driven under anything else can only ever be compared with
    Apex's own -- which is a study of five people, not of eighty. Matching it is
    what lets a participant collected here sit in the same analysis as theirs.

    Matching means all four conditions, not just the circuit. The track alone
    was tried and is not enough: their grid carries three robots to Apex's none,
    which is the difference between a finish position that varies and one that
    is 1 in every stored session; two laps to three, which is the difference
    between one flying lap and two; ``car1-trb1`` to ``car7-trb1``, which moves
    every absolute speed; and tire factor 0 to 1. Each one on its own is enough
    to stop absolute figures pooling, so a preset that fixes three of them buys
    nothing.

    The two Apex-native assignments stay, because the data already collected
    under them has to remain reproducible and because they measure something
    their circuit cannot:

    * ``apex-study-v1`` -- aalborg. A comparative study is judged on more than
      lap time, and CG Speedway 1 has enough asphalt run-off that a participant
      measured 14 m outside the track edge collected no damage at all. A
      narrower circuit with the barrier closer makes a mistake register as
      something, which is what gives the incident count any power to
      discriminate.
    * ``apex-study-speedway-v1`` -- CG Speedway, and otherwise identical to it,
      so choosing between those two changes exactly one thing. It is the right
      pick for asking what the circuit alone did.

    Choosing between the team-matched preset and either of those changes four
    things at once, deliberately. That is the price of pooling, and it is why
    they are three separate assignments rather than one with options.
    """
    return (
        _study_preset(
            torcs_binary,
            preset_id="ibmf1-practice-v5",
            display_name="Team practice (matches IBMF1)",
            track_id="g-track-1",
            race_config_name="apexibmf1.xml",
            car_id=TEAM_CAR_ID,
            laps=2,
            opponents=TEAM_OPPONENTS,
        ),
        _study_preset(
            torcs_binary,
            preset_id="apex-study-v1",
            display_name="Apex Study v1",
            track_id="aalborg",
            race_config_name="apexstudy.xml",
        ),
        _study_preset(
            torcs_binary,
            preset_id="apex-study-speedway-v1",
            display_name="Apex Study v1 on CG Speedway",
            track_id="g-track-1",
            race_config_name="apexstudyspeedway.xml",
        ),
    )


def default_study_preset(torcs_binary: str | Path) -> TorcsStudyPreset:
    """The assignment a participant drives unless a facilitator picks another."""
    return study_presets(torcs_binary)[0]


def study_preset_by_id(torcs_binary: str | Path, preset_id: str) -> TorcsStudyPreset:
    """One assignment by the id a capture recorded.

    An unknown id is an error rather than a fall back to the default: a manifest
    naming a preset this build does not have describes a race driven under
    conditions this build cannot reproduce, and silently substituting another
    would relabel it.
    """
    for preset in study_presets(torcs_binary):
        if preset.preset_id == preset_id:
            return preset
    known = ", ".join(preset.preset_id for preset in study_presets(torcs_binary))
    raise ValueError(f"unknown study preset {preset_id!r}; this build has {known}")


def human_captures_root() -> Path:
    return workspace_root() / "captures" / "human"


def capture_human_runs(
    config: HumanCaptureConfig,
    *,
    runner: Runner = subprocess.run,
    base_env: dict[str, str] | None = None,
    record: bool = True,
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
        profile_dir = workspace_root() / "torcs-profiles" / config.preset.preset_id
        environment[TORCS_LOCAL_DIR_ENV] = str(profile_dir)
        _write_screen_config(profile_dir, binary, config.preset)
        _reset_display_mode(profile_dir, binary)
        _reset_driver_profile(profile_dir, binary, config.preset)
    # Recording is layered on top and never decides anything. If it will not
    # start, the session runs unrecorded and says so in the manifest rather than
    # denying somebody the drive they came for.
    recorder = None
    if record:
        recorder = ScreenRecorder(capture_dir / "session.mp4")
        # Waits for the simulator window before recording anything: gdigrab
        # resolves the window title once and fails outright if nothing matches,
        # so starting alongside TORCS meant recording a window that did not
        # exist yet -- which produced no video at all, every time.
        recorder.start_when_window_appears()

    try:
        completed = runner(
            command,
            env=environment,
            check=False,
            cwd=str(torcs_launch_cwd(config.torcs_binary)),
        )
    except OSError as exc:
        manifest.update(status="launch_failed", error=str(exc))
        _write_manifest(capture_dir, manifest)
        raise HumanCaptureError(f"could not start TORCS: {exc}", capture_dir) from exc

    if recorder is not None:
        recording = recorder.stop()
        if recording is None:
            manifest["recording_error"] = recorder.error
        else:
            # The clips are cut long after the session, from the debrief that
            # has not been computed yet, so where the footage starts on the wall
            # clock has to survive in writing.
            manifest["recording"] = recording.to_dict()

    manifest["returncode"] = int(completed.returncode)
    if stop_requested is not None and stop_requested():
        manifest["status"] = "cancelled"
        manifest["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        _write_manifest(capture_dir, manifest)
        raise HumanCaptureCancelled(
            f"capture stopped by the user; raw files remain in {capture_dir}",
            capture_dir,
        )
    # A non-zero exit is recorded but does not decide the outcome on its own.
    # TORCS can die while closing its windows -- 0xC0000005 on Windows -- long
    # after the participant has driven the whole assignment, and the evidence of
    # a good session is the telemetry on disk, not the code the simulator
    # happened to return. Judging on the exit code first threw away complete,
    # validated laps. A crash that actually cost data still fails, because the
    # validation below sees short or malformed telemetry.
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
        # The recorder creates its file when a race starts, so no file at all
        # means the simulator never got that far -- a failure to launch, not a
        # failure to drive. Those need opposite things from the person sitting
        # there: one is "press start again", the other is "you have to actually
        # drive a session". Telling a participant only the exit code leaves them
        # stuck in front of a red line with nothing to act on.
        if not csv_files and completed.returncode:
            reason = (
                "The simulator closed before it recorded anything. Nothing was "
                "lost and nothing needs saving -- start the session again. If "
                "it keeps happening, tell the study team and pass on "
                f"{capture_dir} (exit status {completed.returncode})."
            )
        elif completed.returncode:
            reason = (
                f"TORCS exited with status {completed.returncode} and produced no "
                f"usable telemetry in {capture_dir}."
            )
        else:
            reason = (
                f"TORCS produced no non-empty telemetry CSV in {capture_dir}. "
                "Select a human driver and complete a Practice or Quick Race session."
            )
        manifest.update(
            # A clean simulator that recorded no rows is a driving/data outcome.
            # A non-zero process result is a simulator failure, even though the
            # same missing evidence means neither outcome can be registered.
            status="simulator_failed" if completed.returncode else "no_data",
            error=reason,
            finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        _write_manifest(capture_dir, manifest)
        raise HumanCaptureError(reason, capture_dir)

    run_dirs = []
    run_records = []
    for path, frame in non_empty:
        run_dir = import_run(
            path,
            capture="human-driver",
            identity=StudyIdentity(
                driver=config.participant_id,
                phase=config.phase,
                setup=None if config.preset is None else config.preset.preset_id,
            ),
            recording=manifest.get("recording"),
        )
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
        # The distinction is kept in the audit trail: the laps are complete and
        # validated either way, but a session whose simulator crashed on the way
        # out is not the same event as a clean one.
        status="complete" if completed.returncode == 0 else "complete_after_abnormal_exit",
        finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
        runs=run_records,
    )
    _write_manifest(capture_dir, manifest)
    return HumanCaptureResult(capture_dir=capture_dir, run_dirs=tuple(run_dirs))


def finish_capture(capture_dir: str | Path) -> HumanCaptureResult:
    """Register the laps in a capture directory that was never finished.

    A session whose simulator crashed before this build treated the exit code as
    the verdict and left validated telemetry on disk unregistered. The manifest
    beside it still holds who drove and under which assignment, so the run can be
    completed from the evidence rather than asking a participant to drive again.

    Re-running on an already finished directory is safe: the same laps import as
    fresh runs only if they are not already in the store, and the manifest is
    rewritten from what is actually there.
    """
    capture_dir = Path(capture_dir)
    manifest_path = capture_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HumanCaptureError(
            f"{capture_dir} has no readable capture manifest: {exc}", capture_dir
        ) from exc

    preset = manifest.get("study_preset") or {}
    identity = StudyIdentity(
        driver=manifest.get("participant_id"),
        phase=manifest.get("phase"),
        setup=preset.get("preset_id"),
    )
    csv_files = sorted(capture_dir.glob("*.csv"))
    frames = [_validated_frame(path) for path in csv_files]
    non_empty = [(path, frame) for path, frame in zip(csv_files, frames, strict=True) if len(frame)]
    if not non_empty:
        raise HumanCaptureError(
            f"{capture_dir} holds no usable telemetry to register.", capture_dir
        )

    run_dirs, run_records = [], []
    for path, frame in non_empty:
        run_dir = import_run(
            path,
            capture="human-driver",
            identity=identity,
            recording=manifest.get("recording"),
        )
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
        status="complete_after_recovery",
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


_SCREEN_ATTR = re.compile(
    r'(<attnum\s+name="(?P<name>x|y)"\s+val=")(?P<value>[^"]*)(")'
)


def _write_screen_config(
    profile_dir: Path, torcs_binary: Path, preset: TorcsStudyPreset
) -> None:
    """Fix the render size in the session's own TORCS profile, before launch.

    TORCS reads ``config/screen.xml`` from its local directory, which for a
    study session is the per-preset profile Apex owns. Writing it here rather
    than patching the simulator survives both launchers, though for different
    reasons, and both depend on this running immediately before launch:

    * ``src/windows/main.cpp`` seeds a profile with ``copyFileIfNotExists``, so
      a file already present is left alone.
    * ``setup_linux.sh`` re-copies its own screen.xml when the installed one is
      *newer* than the profile's. Ours is written seconds before launch, so it
      always wins.

    A failure here must not cost a participant their session: the window would
    merely open at the stock 640x480, so the capture still runs.
    """
    destination = profile_dir / "config" / "screen.xml"
    source = torcs_raceman_dir(torcs_binary).parent / "screen.xml"
    try:
        if destination.exists():
            text = destination.read_text(encoding="utf-8")
        else:
            text = source.read_text(encoding="utf-8")
    except OSError:
        return

    sizes = {"x": preset.window_width, "y": preset.window_height}
    updated, count = _SCREEN_ATTR.subn(
        lambda m: f"{m.group(1)}{sizes[m.group('name')]}{m.group(4)}", text, count=2
    )
    if count != 2:  # an unfamiliar screen.xml: leave TORCS's own defaults alone
        return
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(updated, encoding="utf-8")
    except OSError:
        return


# The in-race overlays, restored to what TORCS itself ships with. These are the
# defaults read in grboard.cpp, not chosen values: the study wants every
# participant to see the same thing, and "the same" is least arguable when it is
# also "unmodified". TRACK_MAP_NORMAL_WITH_OPPONENTS is 1<<2.
#
# Restored every session because TORCS cycles these with the number keys and
# writes the result straight back into the profile (grboard.cpp: selectBoard).
# The driver board cycles 2 -> 0 -> 1, and 0 draws nothing, so a single stray
# press of "1" while driving hides the lap and time panel for that session and
# every session after it. A comparison where some participants could see their
# lap time and others could not is not comparing what it thinks it is.
_DISPLAY_DEFAULTS = {
    "driver board": 2,
    "driver counter": 1,
    "G graph": 1,
    "arcade": 0,
    "map mode": 1 << 2,
}
_SECTION_TAG = re.compile(r"<section\b|</section>")
_DISPLAY_ATTR = re.compile(
    r'(<attnum\s+name="(?P<name>[^"]+)"[^>]*?\bval=")(?P<value>[^"]*)(")'
)


def _display_mode_span(text: str) -> tuple[int, int] | None:
    """The bounds of the Display Mode section, counting nesting rather than
    guessing at it.

    A regex cannot bracket nested XML, and trying cost a real bug: a lazy match
    to the first ``</section>`` stopped inside the first *sub*section, so the
    driver board setting -- which lives in a later sibling -- was never reached
    and the hidden panel stayed hidden.
    """
    start = text.find('<section name="Display Mode">')
    if start == -1:
        return None
    depth = 0
    for match in _SECTION_TAG.finditer(text, start):
        depth += 1 if match.group(0) == "<section" else -1
        if depth == 0:
            return start, match.end()
    return None  # unbalanced: leave the file alone rather than half-edit it


def _reset_display_mode(profile_dir: Path, torcs_binary: Path) -> None:
    """Put the in-race overlays back to stock before launch.

    A profile that has never had a key pressed carries no Display Mode section
    at all, so there is nothing to reset and TORCS uses its own defaults --
    which is the same outcome. Nothing here is worth failing a session over.
    """
    destination = profile_dir / "config" / "graph.xml"
    source = torcs_raceman_dir(torcs_binary).parent / "graph.xml"
    try:
        text = (destination if destination.exists() else source).read_text(encoding="utf-8")
    except OSError:
        return

    span = _display_mode_span(text)
    if span is None:
        return

    def fix_attr(attr: re.Match) -> str:
        wanted = _DISPLAY_DEFAULTS.get(attr.group("name"))
        if wanted is None:
            return attr.group(0)
        return f"{attr.group(1)}{wanted}{attr.group(4)}"

    start, end = span
    updated = text[:start] + _DISPLAY_ATTR.sub(fix_attr, text[start:end]) + text[end:]
    if updated == text:
        return
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(updated, encoding="utf-8")
    except OSError:
        return


_SKILL_ATTR = re.compile(r'(<attstr\s+name="skill level"[^>]*?\bval=")([^"]*)(")')
_CAR_ATTR = re.compile(r'(<attstr\s+name="car name"[^>]*?\bval=")([^"]*)(")')


def _reset_driver_profile(
    profile_dir: Path, torcs_binary: Path, preset: TorcsStudyPreset | None = None
) -> None:
    """Re-seed the human driver settings from the ones this build ships.

    TORCS seeds a profile once and then leaves it alone, so a change to the
    assignment reaches only people who have never run Apex before. That is how
    the damage setting went unnoticed: the shipped human.xml moved to `amateur`,
    which is what makes impacts register at all, and every existing profile
    quietly stayed on `rookie` and kept multiplying damage by zero.

    Only the skill level and the car are rewritten, not the whole file: the
    profile also carries the participant's own control bindings, and replacing
    those between sessions would change what they are driving with.

    A profile that does not exist yet is seeded here rather than left to TORCS.
    Both would write the same bytes, but TORCS writes them after this runs,
    which would leave the car unset on exactly the machine where it matters
    most: a fresh install, used for the first time, by a participant about to
    drive an assignment that names a different car from the shipped default.
    """
    destination = profile_dir / "drivers" / "human" / "human.xml"
    source = torcs_data_root(torcs_binary) / "drivers" / "human" / "human.xml"
    try:
        shipped = source.read_text(encoding="latin-1")
    except OSError:
        return
    wanted = _SKILL_ATTR.search(shipped)
    if wanted is None:
        return
    try:
        current = destination.read_text(encoding="latin-1")
    except OSError:
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(shipped, encoding="latin-1")
        except OSError:
            return
        current = shipped

    updated, count = _SKILL_ATTR.subn(
        lambda m: f"{m.group(1)}{wanted.group(2)}{m.group(3)}", current
    )
    if not count:
        return
    if preset is not None:
        # Only the first match, which is driver index 1 -- the one every study
        # race configuration focuses. A profile may carry further indices the
        # participant made for themselves, and rewriting the car under those
        # would edit settings this session never uses.
        updated = _CAR_ATTR.sub(
            lambda m: f"{m.group(1)}{preset.car_id}{m.group(3)}", updated, count=1
        )
    if updated == current:
        return
    try:
        destination.write_text(updated, encoding="latin-1")
    except OSError:
        return


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
