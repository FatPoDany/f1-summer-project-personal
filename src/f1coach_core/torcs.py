"""Adapter for Lin's TORCS high-frequency exporter (simuv2, stride 10, ~50 Hz).

Field reference: M_Lin/torcs_highfreq_field_cheatsheet.csv (249 columns).
    A run file holds a whole outing — grid start, N flying laps, and sometimes
    a trailing fragment — so this module splits the run at start-line crossings
    (where dist_from_start_m snaps back to zero). A persisted race_finished flag
    or deterministic capture_lap_closed marker can also close the final segment
    when TORCS stops before sending a reset.
    Each complete lap maps onto the canonical v1 schema (see schema.py):

    t        <- sim_time_s, rebased to the lap's first sample
    dist     <- dist_from_start_m
    speed    <- total_speed_mps (falls back to speed_body_x_mps)
    throttle <- accel_cmd
    brake    <- brake_cmd
    steer    <- steer_cmd
    gear     <- gear
    sector   <- derived: thirds of the observed track length (TORCS has none)
"""

from dataclasses import dataclass
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd

from f1coach_core.lap import NO_IDENTITY, StudyIdentity
from f1coach_core.loader import TelemetrySchemaError
from f1coach_core.schema import SCHEMA_VERSION

SIGNATURE_COLUMNS = (
    "sim_time_s",
    "dist_from_start_m",
    "accel_cmd",
    "brake_cmd",
    "steer_cmd",
    "gear",
)
SPEED_CANDIDATES = ("total_speed_mps", "speed_body_x_mps")
MIN_LAP_SAMPLES = 20
MIN_LAP_DISTANCE_COVERAGE = 0.8


@dataclass(frozen=True)
class TorcsLap:
    """One start-line-to-start-line segment of a TORCS run, in canonical columns."""

    df: pd.DataFrame
    lap_label: int  # race_lap counter (segment ordinal when absent)
    lap_time: float
    car_name: str | None
    complete: bool  # started at the line AND ended by crossing it again


def is_torcs_export(path: str | Path) -> bool:
    """Cheap signature check on the header row only — no full parse."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                header = {c.strip().strip('"') for c in line.split(",")}
                return all(col in header for col in SIGNATURE_COLUMNS)
    except OSError:
        return False
    return False


def split_torcs_run(path: str | Path) -> list[TorcsLap]:
    """Split a run export into laps. Raises TelemetrySchemaError with readable text."""
    path = Path(path)
    try:
        df = pd.read_csv(path, comment="#", low_memory=False)
    except Exception as exc:
        raise TelemetrySchemaError(f"{path.name} is not parseable CSV: {exc}") from exc

    missing = [c for c in SIGNATURE_COLUMNS if c not in df.columns]
    if missing:
        raise TelemetrySchemaError(
            f"{path.name} looks like a TORCS export but is missing {missing}."
        )
    speed_col = next((c for c in SPEED_CANDIDATES if c in df.columns), None)
    if speed_col is None:
        raise TelemetrySchemaError(
            f"{path.name} has none of the speed channels {list(SPEED_CANDIDATES)}."
        )

    dist = pd.to_numeric(df["dist_from_start_m"], errors="coerce").to_numpy(dtype=float)
    track_length = float(np.nanmax(dist))
    if not np.isfinite(track_length) or track_length <= 0:
        raise TelemetrySchemaError(f"{path.name}: dist_from_start_m carries no usable values.")

    # a start-line crossing shows as dist snapping back toward zero
    resets = (np.flatnonzero(np.diff(dist) < -0.5 * track_length) + 1).tolist()
    bounds = [0, *resets, len(df)]

    laps: list[TorcsLap] = []
    for i in range(len(bounds) - 1):
        seg = df.iloc[bounds[i] : bounds[i + 1]]
        if len(seg) < MIN_LAP_SAMPLES:
            continue
        canonical = _to_canonical(seg, speed_col, track_length)
        if len(canonical) < MIN_LAP_SAMPLES:  # corrupt rows got dropped in conversion
            continue
        starts_at_line = float(canonical["dist"].iloc[0]) < 0.02 * track_length
        covered_track = (
            float(canonical["dist"].max() - canonical["dist"].min())
            >= MIN_LAP_DISTANCE_COVERAGE * track_length
        )
        # The final segment has no following start-line crossing to close it, so
        # it needs separate evidence that the lap was actually driven, and
        # distance coverage is that evidence. It must not be gated on a finish
        # marker: the human recorder emits neither `race_finished` nor
        # `capture_lap_closed`, because it runs inside the driver callback and
        # the race engine stops calling that at the finish line. Requiring a
        # marker silently discarded the last lap of every human capture -- a
        # participant driving the assigned five laps had four imported. Those
        # columns remain in the raw CSVs as provenance.
        #
        # Coverage stays necessary rather than merely one option among markers,
        # so a session abandoned part way round is still an incomplete fragment.
        closed_by_reset = i < len(bounds) - 2
        final_segment = i == len(bounds) - 2
        ends_at_line = closed_by_reset or (final_segment and covered_track)
        if "race_lap" in seg.columns:
            lap_label = int(seg["race_lap"].iloc[len(seg) // 2])
        else:
            lap_label = i
        car_name = str(seg["car_name"].iloc[0]) if "car_name" in seg.columns else None
        laps.append(
            TorcsLap(
                df=canonical,
                lap_label=lap_label,
                lap_time=float(canonical["t"].iloc[-1]),
                car_name=car_name,
                complete=starts_at_line and ends_at_line,
            )
        )
    return laps


def write_canonical_lap(
    lap: TorcsLap,
    dest: str | Path,
    source_name: str,
    identity: StudyIdentity = NO_IDENTITY,
) -> None:
    """Write one split lap as a canonical v1 CSV with provenance comments."""
    Path(dest).write_text(
        canonical_lap_text(lap, source_name, identity), encoding="utf-8"
    )


def canonical_lap_text(
    lap: TorcsLap, source_name: str, identity: StudyIdentity = NO_IDENTITY
) -> str:
    """Render one split lap deterministically for writing or import de-duplication.

    The study fields go in as their own ``# key: value`` lines so the loader can
    read them back: a lap file then states who drove it without depending on any
    index that could be separated from it.
    """
    provenance = f"source: torcs:{source_name}"
    if lap.car_name:
        provenance += f" | car: {lap.car_name}"
    provenance += f" | race_lap: {lap.lap_label} | measured_lap_time: {lap.lap_time:.3f}"
    output = StringIO(newline="")
    output.write(f"# schema_version: {SCHEMA_VERSION}\n")
    output.write(f"# lap: {lap.lap_label}\n")
    for key, value in (
        ("driver", identity.driver),
        ("phase", identity.phase),
        ("setup", identity.setup),
    ):
        if value:
            output.write(f"# {key}: {value}\n")
    output.write(f"# {provenance}\n")
    output.write("# sector: derived as thirds of track length (TORCS exports no sectors)\n")
    lap.df.to_csv(output, index=False)
    return output.getvalue()


def _to_canonical(seg: pd.DataFrame, speed_col: str, track_length: float) -> pd.DataFrame:
    sources = {
        "t": seg["sim_time_s"],
        "dist": seg["dist_from_start_m"],
        "speed": seg[speed_col],
        "throttle": seg["accel_cmd"],
        "brake": seg["brake_cmd"],
        "steer": seg["steer_cmd"],
        "gear": seg["gear"],
    }
    frame = pd.DataFrame(
        {name: pd.to_numeric(column, errors="coerce") for name, column in sources.items()}
    ).dropna()  # one corrupt row must not poison the whole lap
    if frame.empty:
        return frame.reindex(columns=[*frame.columns, "sector"])
    sector = 1 + np.minimum((frame["dist"] / (track_length / 3.0)).astype(int), 2)
    return pd.DataFrame(
        {
            "t": (frame["t"] - frame["t"].iloc[0]).round(4).to_numpy(),
            "dist": frame["dist"].round(3).to_numpy(),
            "speed": frame["speed"].round(4).to_numpy(),
            "throttle": frame["throttle"].round(4).to_numpy(),
            "brake": frame["brake"].round(4).to_numpy(),
            "steer": frame["steer"].round(4).to_numpy(),
            "gear": frame["gear"].to_numpy(),
            "sector": sector.to_numpy(),
        }
    )
