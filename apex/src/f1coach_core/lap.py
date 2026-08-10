"""The Lap domain object — what loaders return and every front end renders."""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class Lap:
    """One lap of validated telemetry.

    House rule: front ends (app, CLI, notebooks) never compute telemetry
    truth — they render what this object carries.
    """

    df: pd.DataFrame
    source: Path
    schema_version: int
    dist_derived: bool  # True when the loader had to integrate speed to get dist

    @property
    def name(self) -> str:
        return self.source.name

    @property
    def n_samples(self) -> int:
        return len(self.df)

    @property
    def lap_time(self) -> float:
        """Seconds from first to last sample."""
        t = self.df["t"]
        return float(t.iloc[-1] - t.iloc[0])

    @property
    def track_length(self) -> float:
        """Metres covered by the lap."""
        d = self.df["dist"]
        return float(d.iloc[-1] - d.iloc[0])

    @property
    def sample_rate(self) -> float:
        """Mean sampling rate in Hz."""
        return (self.n_samples - 1) / self.lap_time

    @property
    def speed_kmh(self) -> pd.Series:
        return self.df["speed"] * 3.6

    @property
    def top_speed_kmh(self) -> float:
        return float(self.speed_kmh.max())
