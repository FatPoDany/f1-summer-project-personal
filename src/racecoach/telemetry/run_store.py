"""The run store: <workspace>/runs/<run_id>/telemetry.csv + meta.json.

A *run* is one whole outing as the exporter wrote it — multi-lap, raw
columns, untouched. Storing the original file is the point: every analysis
is reproducible offline without re-running the simulator. (Apex sessions
hold the *split canonical laps*; the run store holds the source of truth.)

Readable errors are a feature here exactly as in f1coach_core.loader.
"""

import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from f1coach_core.lap import NO_IDENTITY, StudyIdentity
from f1coach_core.torcs import (
    MIN_LAP_DISTANCE_COVERAGE,
    SIGNATURE_COLUMNS,
    is_torcs_export,
)
from f1coach_core.workspace import workspace_root

TELEMETRY_NAME = "telemetry.csv"
META_NAME = "meta.json"


class RunImportError(ValueError):
    """A file the run store refuses. Messages are written to be shown to a person."""


@dataclass(frozen=True)
class RunMeta:
    run_id: str
    source_file: str
    imported_at: str  # ISO 8601, UTC
    n_samples: int
    n_columns: int
    car_names: tuple[str, ...]
    laps_seen: tuple[int, ...]  # labels whose samples cover a full track distance
    sim_time_span_s: float
    cadence_hz: float | None  # None when the time channel is degenerate
    capture: str  # "torcs-exporter" | "scr-client" | "human-driver" | "synthetic-robot"
    # Study identity, absent on runs imported outside the participant workflow.
    driver: str | None = None  # pseudonymous participant id, never a name
    phase: str | None = None
    setup: str | None = None  # assigned preset id
    # Where the screen recording of this run lives, when there was one. Carried
    # here because the run is a copy of the capture CSV with no way back to the
    # folder it came from, and the review window needs to find the footage long
    # after the session that produced it.
    recording: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RunMeta":
        return cls(
            run_id=data["run_id"],
            source_file=data["source_file"],
            imported_at=data["imported_at"],
            n_samples=int(data["n_samples"]),
            n_columns=int(data["n_columns"]),
            car_names=tuple(data["car_names"]),
            laps_seen=tuple(int(lap) for lap in data["laps_seen"]),
            recording=data.get("recording"),
            sim_time_span_s=float(data["sim_time_span_s"]),
            cadence_hz=None if data["cadence_hz"] is None else float(data["cadence_hz"]),
            capture=data["capture"],
            # Runs written before study identity existed simply have none.
            driver=data.get("driver"),
            phase=data.get("phase"),
            setup=data.get("setup"),
        )


@dataclass(frozen=True)
class LoadedRun:
    meta: RunMeta
    df: pd.DataFrame
    path: Path


def runs_root() -> Path:
    return workspace_root() / "runs"


def import_run(
    src: str | Path,
    *,
    capture: str = "torcs-exporter",
    identity: StudyIdentity = NO_IDENTITY,
    recording: dict | None = None,
) -> Path:
    """Copy an exporter CSV into the store; returns the new run directory."""
    src = Path(src)
    if not src.is_file():
        raise RunImportError(f"No such telemetry file: {src}")
    if not is_torcs_export(src):
        raise RunImportError(
            f"{src.name} doesn't look like a TORCS exporter run (expected the "
            f"columns {list(SIGNATURE_COLUMNS)} in its header). Canonical "
            "single-lap CSVs belong in an Apex session instead."
        )
    df = _read_frame(src)
    run_dir = new_run_dir(src.stem)
    meta = _build_meta(
        df,
        run_id=run_dir.name,
        source_file=src.name,
        capture=capture,
        identity=identity,
        recording=recording,
    )
    shutil.copy2(src, run_dir / TELEMETRY_NAME)
    (run_dir / META_NAME).write_text(
        json.dumps(meta.to_dict(), indent=2), encoding="utf-8"
    )
    return run_dir


def new_run_dir(slug: str) -> Path:
    """Claim a fresh runs/<run_id>/ directory (used by import and live capture)."""
    run_dir = runs_root() / _unique_run_id(_slug(slug))
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def finalize_run(run_dir: Path, source_file: str, capture: str) -> RunMeta:
    """Build meta.json from whatever telemetry.csv now holds (live capture's
    last step — after this the run is indistinguishable from an import)."""
    df = _read_frame(run_dir / TELEMETRY_NAME)
    meta = _build_meta(df, run_id=run_dir.name, source_file=source_file, capture=capture)
    (run_dir / META_NAME).write_text(
        json.dumps(meta.to_dict(), indent=2), encoding="utf-8"
    )
    return meta


def list_runs() -> list[RunMeta]:
    root = runs_root()
    if not root.is_dir():
        return []
    metas = []
    for run_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        meta_path = run_dir / META_NAME
        if meta_path.is_file():  # half-imported dirs are invisible, not fatal
            metas.append(RunMeta.from_dict(json.loads(meta_path.read_text("utf-8"))))
    return metas


def load_run(run_id: str) -> LoadedRun:
    run_dir = runs_root() / run_id
    meta_path = run_dir / META_NAME
    if not meta_path.is_file():
        known = ", ".join(m.run_id for m in list_runs()) or "(none imported yet)"
        raise RunImportError(f"No run named '{run_id}' in {runs_root()}. Known runs: {known}")
    meta = RunMeta.from_dict(json.loads(meta_path.read_text("utf-8")))
    return LoadedRun(meta=meta, df=_read_frame(run_dir / TELEMETRY_NAME), path=run_dir)


def _read_frame(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, comment="#", low_memory=False)
    except Exception as exc:
        raise RunImportError(f"{path.name} is not parseable CSV: {exc}") from exc


def _build_meta(
    df: pd.DataFrame,
    *,
    run_id: str,
    source_file: str,
    capture: str,
    identity: StudyIdentity = NO_IDENTITY,
    recording: dict | None = None,
) -> RunMeta:
    sim_time = pd.to_numeric(df.get("sim_time_s"), errors="coerce").dropna()
    span = float(sim_time.max() - sim_time.min()) if len(sim_time) else 0.0
    steps = sim_time.diff().dropna()
    steps = steps[steps > 0]
    cadence = round(1.0 / float(steps.median()), 1) if len(steps) else None
    cars: tuple[str, ...] = ()
    if "car_name" in df.columns:
        cars = tuple(sorted(str(name) for name in df["car_name"].dropna().unique()))
    laps = _full_track_lap_labels(df)
    return RunMeta(
        run_id=run_id,
        source_file=source_file,
        imported_at=datetime.now(UTC).isoformat(timespec="seconds"),
        n_samples=len(df),
        n_columns=len(df.columns),
        car_names=cars,
        laps_seen=laps,
        recording=recording,
        sim_time_span_s=round(span, 3),
        cadence_hz=cadence,
        capture=capture,
        driver=identity.driver,
        phase=identity.phase,
        setup=identity.setup,
    )


def _full_track_lap_labels(df: pd.DataFrame) -> tuple[int, ...]:
    if "race_lap" not in df.columns or "dist_from_start_m" not in df.columns:
        return ()
    evidence = pd.DataFrame(
        {
            "lap": pd.to_numeric(df["race_lap"], errors="coerce"),
            "dist": pd.to_numeric(df["dist_from_start_m"], errors="coerce"),
        }
    ).dropna()
    if evidence.empty:
        return ()
    track_length = float(evidence["dist"].max())
    if track_length <= 0:
        return ()
    labels = []
    for label, group in evidence.groupby("lap"):
        numeric_label = float(label)
        distance_span = float(group["dist"].max() - group["dist"].min())
        if (
            len(group) >= 2
            and numeric_label.is_integer()
            and distance_span >= MIN_LAP_DISTANCE_COVERAGE * track_length
        ):
            labels.append(int(numeric_label))
    return tuple(sorted(labels))


def _slug(stem: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.")
    return slug or "run"


def _unique_run_id(slug: str) -> str:
    base = f"{slug}-{datetime.now(UTC):%Y%m%d-%H%M%S}"
    run_id, counter = base, 2
    while (runs_root() / run_id).exists():
        run_id = f"{base}-{counter}"
        counter += 1
    return run_id
