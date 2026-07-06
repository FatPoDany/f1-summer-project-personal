"""CSV -> validated Lap.

Readable errors are a feature: they surface verbatim in the app's error
dialog and feed M7's reliability log, so every message says which file,
which column, and which row broke the contract.
"""

from pathlib import Path

import numpy as np
import pandas as pd

from f1coach_core.lap import Lap
from f1coach_core.schema import (
    CANONICAL_ORDER,
    REQUIRED_COLUMNS,
    SCHEMA_VERSION,
    SUPPORTED_VERSIONS,
)


class TelemetrySchemaError(ValueError):
    """A telemetry file Apex can read but not trust. Messages are written
    to be shown to a person, verbatim."""


def load_telemetry_csv(path: str | Path) -> Lap:
    path = Path(path)
    if not path.is_file():
        raise TelemetrySchemaError(f"No such telemetry file: {path}")

    version = _read_schema_version(path)
    if version not in SUPPORTED_VERSIONS:
        supported = ", ".join(str(v) for v in SUPPORTED_VERSIONS)
        raise TelemetrySchemaError(
            f"{path.name} declares schema_version {version}; this build understands "
            f"{supported}. Update f1coach-core or re-export the lap."
        )

    try:
        df = pd.read_csv(path, comment="#")
    except Exception as exc:  # pandas raises several parser error types
        raise TelemetrySchemaError(f"{path.name} is not parseable CSV: {exc}") from exc

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise TelemetrySchemaError(
            f"{path.name} is missing required column(s) {missing}; found {list(df.columns)}. "
            f"Expected the v{SCHEMA_VERSION} telemetry schema (see f1coach_core/schema.py)."
        )
    if len(df) < 2:
        raise TelemetrySchemaError(
            f"{path.name} holds {len(df)} sample(s); a lap needs at least 2."
        )

    for col in (c for c in CANONICAL_ORDER if c in df.columns):
        coerced = pd.to_numeric(df[col], errors="coerce")
        if coerced.isna().any():
            row = int(coerced.isna().idxmax())
            raise TelemetrySchemaError(
                f"{path.name}: column '{col}' has a non-numeric or empty value "
                f"at data row {row + 1} (value: {df[col].iloc[row]!r})."
            )
        df[col] = coerced

    t = df["t"].to_numpy(dtype=float)
    steps = np.diff(t)
    if np.any(steps < 0):
        row = int(np.argmax(steps < 0)) + 1
        raise TelemetrySchemaError(
            f"{path.name}: 't' goes backwards at data row {row + 1}; samples must be time-ordered."
        )
    if t[-1] <= t[0]:
        raise TelemetrySchemaError(f"{path.name}: zero-length lap ('t' never advances).")

    dist_derived = "dist" not in df.columns
    if dist_derived:
        speed = df["speed"].to_numpy(dtype=float)
        # the contract's fallback: dist = cumsum(speed * dt), trapezoidal
        step = 0.5 * (speed[1:] + speed[:-1]) * np.diff(t)
        df["dist"] = np.concatenate(([0.0], np.cumsum(step)))

    ordered = [c for c in CANONICAL_ORDER if c in df.columns]
    extra = [c for c in df.columns if c not in ordered]
    df = df[ordered + extra]

    return Lap(df=df, source=path, schema_version=version, dist_derived=dist_derived)


def _read_schema_version(path: Path) -> int:
    """Parse the leading '# key: value' metadata lines; only schema_version so far."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("#"):
                break
            key, _, value = line.lstrip("# ").partition(":")
            if key.strip() == "schema_version":
                try:
                    return int(value.strip())
                except ValueError as exc:
                    raise TelemetrySchemaError(
                        f"{path.name}: unreadable schema_version {value.strip()!r} "
                        "(want an integer)."
                    ) from exc
    return SCHEMA_VERSION
