"""A Session is a directory of lap CSVs — the unit the Garage screen lists."""

from dataclasses import dataclass
from pathlib import Path

from f1coach_core.analysis import sector_times
from f1coach_core.lap import Lap
from f1coach_core.loader import TelemetrySchemaError, load_telemetry_csv


@dataclass(frozen=True)
class Session:
    name: str
    path: Path
    laps: tuple[Lap, ...]
    problems: tuple[tuple[str, str], ...]  # (filename, readable error) — M7's reliability log

    @property
    def best_lap(self) -> Lap | None:
        return min(self.laps, key=lambda lap: lap.lap_time) if self.laps else None

    @property
    def best_sector_times(self) -> dict[int, float]:
        """Fastest time seen for each sector across the whole session."""
        best: dict[int, float] = {}
        for lap in self.laps:
            for sector, seconds in sector_times(lap).items():
                if sector not in best or seconds < best[sector]:
                    best[sector] = seconds
        return best

    def delta_to_best(self, lap: Lap) -> float:
        best = self.best_lap
        return 0.0 if best is None else lap.lap_time - best.lap_time


def load_session(path: str | Path) -> Session:
    """Load every .csv in the directory; unreadable files become problems, not crashes."""
    path = Path(path)
    laps: list[Lap] = []
    problems: list[tuple[str, str]] = []
    for csv in sorted(path.glob("*.csv")):
        try:
            laps.append(load_telemetry_csv(csv))
        except TelemetrySchemaError as exc:
            problems.append((csv.name, str(exc)))
    return Session(name=path.name, path=path, laps=tuple(laps), problems=tuple(problems))
