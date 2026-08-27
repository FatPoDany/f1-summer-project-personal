"""The numbers a comparative user study is actually judged on.

The claim under test is that coaching changes how somebody drives. Lap time on
its own cannot carry that claim: a driver who goes quicker by running wide every
lap has not improved, and one who stops leaving the track has improved even if
the clock barely moves. So the objective measures here are lap time, leaving the
track, and damage, and they are reported per participant per phase because that
is the unit a paired comparison works on.

Everything is measured, never inferred. A lap whose recording lacks a channel
reports that measure as unavailable rather than as zero: counting a missing
signal as "no excursions" would quietly reward the participants whose data was
incomplete.
"""

from dataclasses import dataclass
from statistics import mean, pstdev

import numpy as np

from f1coach_core.lap import Lap
from f1coach_core.participant import BACKGROUND_COLUMNS, background_columns

# One sample beyond the edge is noise or a wheel clipping a kerb. A tenth of a
# second at 50 Hz is the shortest thing worth calling an excursion.
MIN_EXCURSION_SAMPLES = 5


@dataclass(frozen=True)
class LapMetrics:
    """What one lap contributes to the comparison."""

    lap_time_s: float
    off_track_events: int | None
    off_track_seconds: float | None
    damage_events: int | None
    damage_total: float | None

    @property
    def has_track_position(self) -> bool:
        return self.off_track_events is not None

    @property
    def has_damage(self) -> bool:
        return self.damage_events is not None


@dataclass(frozen=True)
class PhaseSummary:
    """One participant in one phase: the row a statistical test consumes."""

    driver: str
    phase: str
    laps: int
    best_lap_s: float
    mean_lap_s: float
    sd_lap_s: float
    off_track_events: int | None
    off_track_seconds: float | None
    damage_events: int | None

    def to_row(self) -> dict:
        return {
            "driver": self.driver,
            "phase": self.phase,
            "laps": self.laps,
            "best_lap_s": round(self.best_lap_s, 3),
            "mean_lap_s": round(self.mean_lap_s, 3),
            "sd_lap_s": round(self.sd_lap_s, 3),
            "off_track_events": _blank(self.off_track_events),
            "off_track_seconds": _blank(
                None if self.off_track_seconds is None else round(self.off_track_seconds, 2)
            ),
            "damage_events": _blank(self.damage_events),
        }


def _blank(value):
    """Missing stays missing. A blank cell is honest; a zero is a claim."""
    return "" if value is None else value


def lap_metrics(lap: Lap) -> LapMetrics:
    off_events, off_seconds = _off_track(lap)
    damage_events, damage_total = _damage(lap)
    return LapMetrics(
        lap_time_s=float(lap.lap_time),
        off_track_events=off_events,
        off_track_seconds=off_seconds,
        damage_events=damage_events,
        damage_total=damage_total,
    )


def _off_track(lap: Lap) -> tuple[int | None, float | None]:
    """Count leaving the track as episodes, not as samples.

    A participant who ran wide once for two seconds and one who wobbled over the
    line ten times have different problems, and a per-sample proportion hides the
    difference. Time off is reported alongside, because a long single excursion
    and a brief one are not equivalent either.
    """
    if "track_pos" not in lap.df.columns:
        return None, None
    outside = np.abs(lap.df["track_pos"].to_numpy(dtype=float)) > 1.0
    t = lap.df["t"].to_numpy(dtype=float)

    events = 0
    seconds = 0.0
    start = None
    for index, is_out in enumerate(outside):
        if is_out and start is None:
            start = index
        elif not is_out and start is not None:
            events, seconds = _close_excursion(events, seconds, t, start, index)
            start = None
    if start is not None:
        events, seconds = _close_excursion(events, seconds, t, start, len(outside))
    return events, round(seconds, 3)


def _close_excursion(events, seconds, t, start, stop):
    if stop - start < MIN_EXCURSION_SAMPLES:
        return events, seconds
    last = min(stop, len(t) - 1)
    return events + 1, seconds + float(t[last] - t[start])


def _damage(lap: Lap) -> tuple[int | None, float | None]:
    """Incidents are increases in damage, not the level it happens to sit at.

    Damage is cumulative across a race, so lap three of a session starts wherever
    lap two left off. Only the rises within this lap belong to this lap.
    """
    if "damage" not in lap.df.columns:
        return None, None
    values = lap.df["damage"].to_numpy(dtype=float)
    if len(values) < 2:
        return 0, 0.0
    steps = np.diff(values)
    rises = steps > 0
    # Consecutive rising samples are one impact, not several.
    events = int(np.sum(rises & ~np.concatenate([[False], rises[:-1]])))
    return events, float(max(0.0, values[-1] - values[0]))


def summarise(driver: str, phase: str, laps: list[Lap]) -> PhaseSummary | None:
    """Reduce one participant's laps in one phase to a single comparable row."""
    if not laps:
        return None
    metrics = [lap_metrics(lap) for lap in laps]
    times = [m.lap_time_s for m in metrics]

    with_track = [m for m in metrics if m.has_track_position]
    with_damage = [m for m in metrics if m.has_damage]
    return PhaseSummary(
        driver=driver,
        phase=phase,
        laps=len(laps),
        best_lap_s=min(times),
        mean_lap_s=mean(times),
        # Population sd: these laps are the whole of that participant's phase,
        # not a sample drawn from a larger set of laps they also drove.
        sd_lap_s=pstdev(times) if len(times) > 1 else 0.0,
        off_track_events=(
            sum(m.off_track_events for m in with_track) if with_track else None
        ),
        off_track_seconds=(
            sum(m.off_track_seconds for m in with_track) if with_track else None
        ),
        damage_events=(
            sum(m.damage_events for m in with_damage) if with_damage else None
        ),
    )


@dataclass(frozen=True)
class LapRow:
    """One lap of one participant: the row a within-phase model consumes.

    The phase summary answers "was this participant quicker afterwards". It
    cannot answer "were they still getting quicker anyway", and that question is
    the one that separates coaching from practice: three laps of a baseline
    already carry a slope, and an improvement that merely continues it is not
    evidence of anything the coaching did.
    """

    driver: str
    phase: str
    lap: int  # order within the phase -- the axis a learning curve is drawn on
    race_lap: int | None  # what the simulator called it, kept as provenance
    lap_time_s: float
    off_track_events: int | None
    off_track_seconds: float | None
    damage_events: int | None
    damage_total: float | None
    source: str

    def to_row(self) -> dict:
        return {
            "driver": self.driver,
            "phase": self.phase,
            "lap": self.lap,
            "race_lap": _blank(self.race_lap),
            "lap_time_s": round(self.lap_time_s, 3),
            "off_track_events": _blank(self.off_track_events),
            "off_track_seconds": _blank(
                None if self.off_track_seconds is None else round(self.off_track_seconds, 2)
            ),
            "damage_events": _blank(self.damage_events),
            "damage_total": _blank(
                None if self.damage_total is None else round(self.damage_total, 2)
            ),
            "source": self.source,
        }


def summarise_all(laps: list[Lap]) -> list[PhaseSummary]:
    """Group laps by participant and phase, in a stable order.

    Laps with no recorded identity are dropped rather than pooled into an
    anonymous group: a comparative study needs to know whose lap it is looking
    at, and an unattributed lap cannot join either side of the comparison.
    """
    groups: dict[tuple[str, str], list[Lap]] = {}
    for lap in laps:
        driver = lap.identity.driver
        phase = lap.identity.phase
        if not driver or not phase:
            continue
        groups.setdefault((driver, phase), []).append(lap)

    summaries = []
    for (driver, phase), group in sorted(groups.items()):
        summary = summarise(driver, phase, group)
        if summary is not None:
            summaries.append(summary)
    return summaries


PERFORMANCE_COLUMNS = (
    "driver",
    "phase",
    "laps",
    "best_lap_s",
    "mean_lap_s",
    "sd_lap_s",
    "off_track_events",
    "off_track_seconds",
    "damage_events",
)


def summary_columns(with_background: bool = True) -> tuple[str, ...]:
    if not with_background:
        return PERFORMANCE_COLUMNS
    background: list[str] = []
    for name in BACKGROUND_COLUMNS:
        background += [name, f"{name}_rank"]
    return PERFORMANCE_COLUMNS + tuple(background)


SUMMARY_COLUMNS = summary_columns()


def summary_csv(summaries: list[PhaseSummary], *, backgrounds: dict | None = None) -> str:
    """One row per participant per phase, ready for a paired test.

    Prior experience rides along on the same rows. Whether the groups were
    comparable to begin with is not a separate question from whether they
    differed afterwards -- it is the question that decides what the difference
    means -- so an analyst should not have to join two files to ask it.
    """
    columns = summary_columns()
    lines = [",".join(columns)]
    for summary in summaries:
        row = summary.to_row()
        found = (backgrounds or {}).get(summary.driver)
        row.update(background_columns(found))
        lines.append(",".join(_csv_cell(row.get(column, "")) for column in columns))
    return "\n".join(lines) + "\n"


def lap_rows(laps: list[Lap]) -> list[LapRow]:
    """Every identified lap, numbered within its own phase, in the order driven.

    Ordered by file name rather than by the simulator's lap counter. The counter
    restarts at 1 in every run, so a phase that took two runs to record would
    otherwise interleave them; the exporter's names carry the run's start time
    and a zero-padded lap, which puts them in the order somebody actually drove.
    """
    groups: dict[tuple[str, str], list[Lap]] = {}
    for lap in laps:
        driver = lap.identity.driver
        phase = lap.identity.phase
        if not driver or not phase:
            continue
        groups.setdefault((driver, phase), []).append(lap)

    rows: list[LapRow] = []
    for (driver, phase), group in sorted(groups.items()):
        for index, lap in enumerate(sorted(group, key=lambda lap: lap.source.name), start=1):
            metrics = lap_metrics(lap)
            rows.append(
                LapRow(
                    driver=driver,
                    phase=phase,
                    lap=index,
                    race_lap=lap.lap_number,
                    lap_time_s=metrics.lap_time_s,
                    off_track_events=metrics.off_track_events,
                    off_track_seconds=metrics.off_track_seconds,
                    damage_events=metrics.damage_events,
                    damage_total=metrics.damage_total,
                    source=lap.source.name,
                )
            )
    return rows


LAP_COLUMNS = (
    "driver",
    "phase",
    "lap",
    "race_lap",
    "lap_time_s",
    "off_track_events",
    "off_track_seconds",
    "damage_events",
    "damage_total",
    "source",
)


def lap_columns(with_background: bool = True) -> tuple[str, ...]:
    if not with_background:
        return LAP_COLUMNS
    background: list[str] = []
    for name in BACKGROUND_COLUMNS:
        background += [name, f"{name}_rank"]
    return LAP_COLUMNS + tuple(background)


def lap_csv(laps: list[Lap], *, backgrounds: dict | None = None) -> str:
    """One row per lap, for the models a per-phase row cannot support.

    Three laps to a phase is a small sample summarised into one number and a
    smaller one still. The same laps as rows keep every observation: a mixed
    model with laps nested in participants uses all of them, and the lap index
    is what lets practice be estimated instead of assumed away.

    Prior experience repeats on every one of a participant's rows. It is
    redundant and deliberately so -- a regression that adjusts for it should not
    require the analyst to join a second file first.
    """
    columns = lap_columns()
    lines = [",".join(columns)]
    for entry in lap_rows(laps):
        row = entry.to_row()
        found = (backgrounds or {}).get(entry.driver)
        row.update(background_columns(found))
        lines.append(",".join(_csv_cell(row.get(column, "")) for column in columns))
    return "\n".join(lines) + "\n"


def _csv_cell(value: object) -> str:
    """Quote anything containing a comma. The bands are prose, not identifiers."""
    text = str(value)
    return f'"{text}"' if "," in text else text
