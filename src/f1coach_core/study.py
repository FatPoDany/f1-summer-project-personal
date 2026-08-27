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

from f1coach_core.adherence import (
    ADHERENCE_COLUMNS,
    ADHERENCE_ROW_COLUMNS,
    AdherenceReport,
    adherence_columns,
)
from f1coach_core.exposure import (
    EXPOSURE_COLUMNS,
    VIEW_COLUMNS,
    ReviewView,
    exposure_columns,
)
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
    """What was driven, what was taken in, what was done about it, then who they are.

    Exposure and adherence sit beside the outcomes rather than out with the
    background columns because both are measurements of this row's phase, not
    standing facts about the participant: they change between a person's
    baseline row and their second row, and the background columns deliberately
    do not.

    The three are one argument read left to right -- how much coaching they
    looked at, whether the corners it named then moved, and what the lap times
    did. Split across files, an analyst has to join them before that argument
    can be made at all.
    """
    if not with_background:
        return PERFORMANCE_COLUMNS
    background: list[str] = []
    for name in BACKGROUND_COLUMNS:
        background += [name, f"{name}_rank"]
    return PERFORMANCE_COLUMNS + EXPOSURE_COLUMNS + ADHERENCE_COLUMNS + tuple(background)


SUMMARY_COLUMNS = summary_columns()


def summary_csv(
    summaries: list[PhaseSummary],
    *,
    backgrounds: dict | None = None,
    exposure: dict | None = None,
    adherence: dict | None = None,
) -> str:
    """One row per participant per phase, ready for a paired test.

    Prior experience rides along on the same rows. Whether the groups were
    comparable to begin with is not a separate question from whether they
    differed afterwards -- it is the question that decides what the difference
    means -- so an analyst should not have to join two files to ask it.

    So does how much coaching they looked at. The between-arm test needs both
    arms to be alike; the dose-response test needs neither, and it is the one a
    study this small can actually carry. It asks a different question of the
    same rows, so it belongs on the same rows.

    So does what they then did about it. Seconds in front of a screen is a dose
    and a lap time is an outcome, and on their own the two are a correlation
    with nothing in between; whether the corners the advice named actually moved
    is the term that makes it a chain. It is also the only thing that separates
    advice that did not work from advice nobody acted on, which otherwise leave
    identical lap times behind.

    ``exposure`` maps a participant to every view recorded of them. ``adherence``
    maps (participant, phase) to that run's comparison with their baseline.
    Left None, either set of columns is blank throughout -- the honest reading
    for a caller that did not look, and not the same claim as zero.
    """
    columns = summary_columns()
    lines = [",".join(columns)]
    for summary in summaries:
        row = summary.to_row()
        found = (backgrounds or {}).get(summary.driver)
        row.update(background_columns(found))
        row.update(
            exposure_columns(
                None if exposure is None else exposure.get(summary.driver),
                phase=summary.phase,
            )
        )
        row.update(
            adherence_columns(
                None
                if adherence is None
                else adherence.get((summary.driver, summary.phase))
            )
        )
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
    return LAP_COLUMNS + EXPOSURE_COLUMNS + tuple(background)


def lap_csv(
    laps: list[Lap],
    *,
    backgrounds: dict | None = None,
    exposure: dict | None = None,
) -> str:
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
        # Per phase, so it repeats down a participant's laps exactly as their
        # background does: the dose was taken before the phase, not lap by lap,
        # and spreading it over the laps would invent a within-phase predictor.
        row.update(
            exposure_columns(
                None if exposure is None else exposure.get(entry.driver),
                phase=entry.phase,
            )
        )
        lines.append(",".join(_csv_cell(row.get(column, "")) for column in columns))
    return "\n".join(lines) + "\n"


def exposure_csv(logs: dict[str, list[ReviewView]]) -> str:
    """One row per view: what each participant looked at, and for how long.

    The summary columns give a dose per phase, which is what a regression takes.
    This is what lets somebody defend that number. A dose is only a dose if the
    window was genuinely being read, and the aggregate cannot show a review left
    open through a coffee break, a corner opened forty times in a minute, or a
    participant who read every corner but one. Those are the checks an analyst
    has to be able to make before a dose-response claim means anything, and the
    same argument that put every lap in its own row puts every view in one.

    Ordered by participant and then by when the view ended, so a session reads
    down the file the way it happened.
    """
    lines = [",".join(VIEW_COLUMNS)]
    for driver in sorted(logs):
        for view in sorted(logs[driver], key=lambda v: (v.at, v.corner)):
            row = view.to_dict()
            row["advice"] = 1 if view.advice else 0
            row["findings"] = _blank(view.findings)
            lines.append(",".join(_csv_cell(row.get(name, "")) for name in VIEW_COLUMNS))
    return "\n".join(lines) + "\n"


def adherence_csv(reports: dict[tuple[str, str], AdherenceReport]) -> str:
    """One row per thing a participant was told, and what became of it.

    The summary columns give a rate per run, which is what a regression takes.
    This is what lets somebody defend that number. A rate out of three or four
    hides everything that matters about it: whether the corners it counted were
    the ones the advice cared about, whether a participant who scored zero moved
    everything a little or nothing at all, and how much of the bar each ask had
    to clear -- which is not a constant, because it is the participant's own
    lap-to-lap spread whenever that is the larger of the two.

    Ordered by participant, then by the run being judged, then by the order the
    debrief raised each point.
    """
    lines = [",".join(ADHERENCE_ROW_COLUMNS)]
    for driver, phase in sorted(reports):
        report = reports[(driver, phase)]
        for shift in report.shifts:
            ask = shift.prescription
            row = {
                "driver": report.driver or driver,
                "before_phase": report.before_phase,
                "after_phase": report.after_phase or phase,
                "corner": ask.corner,
                "apex_m": round(ask.apex_m, 1),
                "metric": ask.metric,
                "category": ask.category,
                "said": ask.said,
                "said_in": ask.said_in,
                "direction": ask.direction,
                "gap": ask.gap,
                "before_mean": _blank(shift.before_mean),
                "before_sd": _blank(shift.before_sd),
                "before_n": shift.before_n,
                "after_mean": _blank(shift.after_mean),
                "after_n": shift.after_n,
                "shift": _blank(shift.shift),
                "toward": _blank(shift.toward),
                "required": round(shift.required, 3),
                "shift_sd": _blank(shift.shift_sd),
                # Blank and not 0 when nothing measured it: a row nobody could
                # check is not a row where somebody ignored the advice.
                "followed": "" if shift.followed is None else int(shift.followed),
            }
            lines.append(
                ",".join(_csv_cell(row.get(name, "")) for name in ADHERENCE_ROW_COLUMNS)
            )
    return "\n".join(lines) + "\n"


def _csv_cell(value: object) -> str:
    """Quote anything containing a comma. The bands are prose, not identifiers."""
    text = str(value)
    return f'"{text}"' if "," in text else text
