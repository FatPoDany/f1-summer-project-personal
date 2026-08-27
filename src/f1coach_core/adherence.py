"""Whether the advice a participant was given then showed up in their driving.

The study measures whether coached participants got quicker. It does not
measure whether they did what they were told, and without that a null result
cannot be read at all: advice that does not work and advice that nobody acted
on produce the same lap times. This is the missing middle term, and it is also
the half of the dose-response argument that ``exposure`` cannot carry -- seconds
in front of a screen is a dose and a lap time is an outcome, but nothing yet
says the two are joined by the participant having done anything.

**This is not lap-to-lap feedback.** Participants drive a baseline run, read a
debrief, then drive a second run; nothing is shown to them between one lap and
the next. A change from lap 2 to lap 3 of the same run is familiarity with the
circuit, and calling it compliance would credit the coaching with a learning
curve it had no part in. Every comparison here crosses the two runs, because
that is the only place advice was ever delivered.

Three things make the comparison defensible:

*One anchor lap.* Corner names come from ``detect_corners``, which numbers the
apexes it finds in the order it finds them. Detect corners twice, on two
different laps, and a single extra dip renames everything after it -- the
baseline's T3 silently becomes the second run's T4. So every lap of both runs is
measured on one anchor's corner grid, and that anchor is the reference lap the
debrief itself was written against (``measure_report`` and ``queue_session``
both use the session's fastest lap). Corner identity then holds by construction
instead of by hope.

*Absolute measurements, not gaps.* A debrief speaks in gaps -- "braked 12 m
earlier" -- and a gap is measured against a reference lap that is a different
lap in the second run. Where a participant actually put the brakes is their own
behaviour, and on one circuit it stays comparable across both runs.

*The participant's own scatter.* "Brake point moved 16 m" means nothing alone.
For a driver whose brake point at that corner wanders 20 m lap to lap it is
noise; for one who repeats it within 3 m it is a decision. The baseline run's
own spread is reported beside every shift so the two can be told apart.

``followed`` asks for the larger of two bars: the fixed threshold that made the
advice worth giving, and the participant's own baseline spread. Neither alone
does the job. The fixed thresholds are not merely blunt, they are wrong-scaled
-- ``NOTABLE_MIN_SPEED_KMH`` is 3 km/h, and the first real participant this ran
on wandered 11 km/h through one corner from lap to lap, so a 3.3 km/h drift
scored as compliance on its own noise. Three laps also give a standard
deviation with roughly half its own size in error, which is no basis for a
binary on its own either.

Taking the larger is what makes the pair safe. The spread can only ever raise
the bar, so error in a three-lap sd cannot manufacture a participant who
complied; at worst it costs a real one. That is the asymmetry a manipulation
check wants, because a study that wrongly believes its advice was taken cannot
interpret anything that follows.

``shift_sd`` sits beside the binary for an analyst who wants a continuous
measure -- which, for dose-response, is the one worth having anyway.
"""

from dataclasses import dataclass
from statistics import mean, pstdev

from f1coach_core.debrief import (
    NOTABLE_BRAKE_POINT_M,
    NOTABLE_COAST_M,
    NOTABLE_MIN_SPEED_KMH,
    NOTABLE_THROTTLE_POINT_M,
    DebriefPoint,
    lap_debrief,
)
from f1coach_core.features import _corner_facts
from f1coach_core.lap import Lap
from f1coach_core.session import Session

# The reporting threshold behind each metric, so a shift is judged in the unit
# that decided the advice was worth giving rather than in one chosen later.
METRIC_THRESHOLDS = {
    "brake_point_m": NOTABLE_BRAKE_POINT_M,
    "min_speed_kmh": NOTABLE_MIN_SPEED_KMH,
    "throttle_reapply_m": NOTABLE_THROTTLE_POINT_M,
    "coast_distance_m": NOTABLE_COAST_M,
}


class AdherenceError(ValueError):
    """The anchor lap does not describe the corners the advice was about."""


@dataclass(frozen=True)
class Prescription:
    """One measurement a participant was asked to move, and which way."""

    corner: str
    apex_m: float
    metric: str
    category: str
    direction: int  # +1 the measurement has to rise, -1 fall
    gap: float  # how far off the reference it was: the size of the ask
    threshold: float
    said: str  # the strongest sentence they read about it, kept for the record
    said_in: int = 1  # how many of the run's laps raised this same point

    @property
    def target(self) -> float:
        """How far it would have to move to close the gap completely."""
        return round(-self.gap, 3)


def fastest(laps: list[Lap]) -> Lap | None:
    """The quickest of these laps -- the reference every debrief is built on."""
    return min(laps, key=lambda lap: lap.lap_time) if laps else None


def prescriptions(points: list[DebriefPoint]) -> list[Prescription]:
    """The measurable asks inside one run's debriefs, in the order presented.

    Takes every point a participant was shown across the run, not one lap's:
    three laps each naming up to three corners is not nine things they were
    told, and the same corner raised on all three is one thing said three times.

    A point whose loss no measurement explained is dropped. It told them time
    went somewhere, which is not something anybody can be held to.

    Repeats of one (corner, metric) collapse to the mean of their gaps, and that
    mean has to clear the same threshold that admitted them individually. This
    is what settles advice that disagreed with itself: a driver told on one lap
    they braked 12 m early and on another 11 m late was never given a direction
    to move in, and the mean of the two does not survive the threshold. No
    special case is needed -- the arithmetic drops it.
    """
    groups: dict[tuple[str, str], list[DebriefPoint]] = {}
    for point in points:
        if not point.metric or not point.gap:
            continue
        if point.metric not in METRIC_THRESHOLDS:
            continue
        groups.setdefault((point.corner, point.metric), []).append(point)

    out: list[Prescription] = []
    for (corner, metric), group in groups.items():
        threshold = METRIC_THRESHOLDS[metric]
        gap = mean(point.gap for point in group)
        if abs(gap) < threshold:
            continue
        loudest = max(group, key=lambda point: abs(point.gap))
        out.append(
            Prescription(
                corner=corner,
                apex_m=loudest.apex_m,
                metric=metric,
                category=loudest.category,
                # Advice at a corner is always "move toward the reference lap",
                # so the direction asked for is the opposite of the gap's sign
                # and never has to be written down by whoever said the sentence.
                direction=-1 if gap > 0 else 1,
                gap=round(gap, 3),
                threshold=threshold,
                said=loudest.difference,
                said_in=len(group),
            )
        )
    return out


def run_prescriptions(laps: list[Lap], *, limit: int = 3) -> list[Prescription]:
    """What a whole run's debrief asked of the participant.

    Mirrors ``measure_report``: every lap against the run's fastest, the fastest
    lap having nothing to lose to itself. Recomputed rather than read back out
    of an audit file so this works on any run, including runs driven before the
    audit trail recorded whose they were.
    """
    anchor = fastest(laps)
    if anchor is None:
        return []
    points: list[DebriefPoint] = []
    for lap in laps:
        if lap is anchor:
            continue
        points += lap_debrief(lap, anchor, limit=limit)
    return prescriptions(points)


@dataclass(frozen=True)
class Shift:
    """What one prescribed measurement did between the two runs."""

    prescription: Prescription
    before_mean: float | None
    before_sd: float | None
    before_n: int
    after_mean: float | None
    after_n: int

    @property
    def shift(self) -> float | None:
        if self.before_mean is None or self.after_mean is None:
            return None
        return round(self.after_mean - self.before_mean, 3)

    @property
    def toward(self) -> float | None:
        """The shift in the direction asked for. Positive is compliance."""
        shift = self.shift
        if shift is None:
            return None
        return round(shift * self.prescription.direction, 3)

    @property
    def shift_sd(self) -> float | None:
        """The shift in units of this participant's own baseline scatter.

        None rather than a large number when the baseline never moved: a driver
        who repeated the corner identically leaves no scale to divide by, and
        inventing one would put the largest effect in the study on the row with
        the least evidence under it.
        """
        toward = self.toward
        if toward is None or self.before_n < 2 or not self.before_sd:
            return None
        return round(toward / self.before_sd, 2)

    @property
    def required(self) -> float:
        """How far this participant had to move for it to count as a change.

        The reporting threshold or their own baseline spread, whichever is
        larger. A driver who repeats a corner within 2 km/h and one who wanders
        11 km/h through it have not done the same thing by arriving 4 km/h
        higher, and one fixed number cannot say so. Falls back to the threshold
        alone when a single lap left no spread to measure.
        """
        return max(self.prescription.threshold, self.before_sd or 0.0)

    @property
    def followed(self) -> bool | None:
        """None when nothing measured it, which is not "they did not"."""
        toward = self.toward
        if toward is None:
            return None
        return toward >= self.required


@dataclass(frozen=True)
class AdherenceReport:
    """One participant's second run held against what their first was told."""

    shifts: tuple[Shift, ...]
    driver: str = ""
    before_phase: str = ""
    after_phase: str = ""

    @property
    def prescribed(self) -> int:
        return len(self.shifts)

    @property
    def measured(self) -> int:
        """Asks both runs recorded, so a shift could be computed at all."""
        return sum(1 for shift in self.shifts if shift.followed is not None)

    @property
    def followed(self) -> int:
        return sum(1 for shift in self.shifts if shift.followed)

    @property
    def rate(self) -> float | None:
        """Of what could be measured, how much moved the way it was asked to."""
        if not self.measured:
            return None
        return round(self.followed / self.measured, 3)

    @property
    def mean_shift_sd(self) -> float | None:
        """The continuous measure: mean movement in the participant's own sd.

        The one to put on the x-axis of a dose-response plot. ``rate`` throws
        magnitude away, and with a handful of asks per person a proportion out
        of three or four is a very coarse number.
        """
        values = [shift.shift_sd for shift in self.shifts if shift.shift_sd is not None]
        return round(mean(values), 2) if values else None


def _facts_by_corner(laps: list[Lap], anchor: Lap) -> list[dict[str, dict]]:
    """Each lap's own corner measurements, all on the anchor's corner grid."""
    return [
        {str(fact["corner"]): fact for fact in _corner_facts(lap, anchor)}
        for lap in laps
    ]


def _series(per_lap: list[dict[str, dict]], corner: str, metric: str) -> list[float]:
    """One measurement at one corner, once per lap that recorded it.

    Laps that recorded nothing are absent rather than zero, the same stance
    ``study._blank`` takes: a corner where no braking was detected is not a
    corner braked at the start line.
    """
    values = []
    for facts in per_lap:
        fact = facts.get(corner)
        if fact is None:
            continue
        value = fact.get(metric)
        if value is None:
            continue
        values.append(float(value))
    return values


def adherence(
    told: list[Prescription],
    *,
    before: list[Lap],
    after: list[Lap],
    anchor: Lap,
    driver: str = "",
    before_phase: str = "",
    after_phase: str = "",
) -> AdherenceReport:
    """Hold a second run against what the first run's debrief asked for.

    ``anchor`` must be the lap the advice was written against. Any other lap is
    refused rather than tolerated: corner names would then come from a second
    detection, T3 in the advice and T3 here would be two different corners, and
    every number produced would be wrong in a way nothing downstream could see.
    """
    grid = {str(fact["corner"]) for fact in _corner_facts(anchor, anchor)}
    missing = sorted({item.corner for item in told} - grid)
    if missing:
        raise AdherenceError(
            f"anchor lap {anchor.source.stem} has no {', '.join(missing)}: the "
            "advice was written against a different reference lap, and matching "
            "corners by name across two detections is the mistake the anchor "
            "exists to prevent"
        )

    before_facts = _facts_by_corner(before, anchor)
    after_facts = _facts_by_corner(after, anchor)
    shifts = []
    for item in told:
        was = _series(before_facts, item.corner, item.metric)
        now = _series(after_facts, item.corner, item.metric)
        shifts.append(
            Shift(
                prescription=item,
                before_mean=round(mean(was), 3) if was else None,
                # Population sd: these laps are the whole of that participant's
                # run, not a sample drawn from other laps they also drove. The
                # same reading study.summarise takes of sd_lap_s.
                before_sd=round(pstdev(was), 3) if len(was) > 1 else None,
                before_n=len(was),
                after_mean=round(mean(now), 3) if now else None,
                after_n=len(now),
            )
        )
    return AdherenceReport(
        shifts=tuple(shifts),
        driver=driver,
        before_phase=before_phase,
        after_phase=after_phase,
    )


def run_adherence(
    before: list[Lap],
    after: list[Lap],
    *,
    limit: int = 3,
) -> AdherenceReport | None:
    """The whole comparison from two runs, the way the study ran them.

    None when the baseline has no laps to anchor on, or asked nothing any
    measurement explained. An empty report and "they were told nothing" are
    different facts, and returning None keeps a caller from averaging the two.
    """
    anchor = fastest(before)
    if anchor is None:
        return None
    told = run_prescriptions(before, limit=limit)
    if not told:
        return None
    identity = anchor.identity
    after_identity = after[0].identity if after else identity
    return adherence(
        told,
        before=before,
        after=after,
        anchor=anchor,
        driver=identity.driver or "",
        before_phase=identity.phase or "",
        after_phase=after_identity.phase or "",
    )


def session_adherence(
    before: Session,
    after: Session,
    *,
    limit: int = 3,
) -> AdherenceReport | None:
    """``run_adherence`` for two loaded sessions."""
    return run_adherence(list(before.laps), list(after.laps), limit=limit)


# The phase every other run is compared against. Named here rather than asked of
# the caller because an adherence number computed against anything else is not
# the one this study means: advice is given once, after the baseline, and a
# second run has nothing earlier than that to have been told.
BASELINE_PHASE = "baseline"


def adherence_all(laps: list[Lap]) -> dict[tuple[str, str], AdherenceReport]:
    """Every later run held against what that participant's baseline was told.

    Keyed by the row the number belongs on -- (driver, the later phase) -- and
    a participant's baseline row deliberately gets none: nobody had told them
    anything yet, which is not the same as their having ignored it.

    Laps carrying no identity are dropped for the reason ``summarise_all`` drops
    them: a comparison has to know whose run it is looking at.
    """
    runs: dict[tuple[str, str], list[Lap]] = {}
    for lap in laps:
        driver, phase = lap.identity.driver, lap.identity.phase
        if not driver or not phase:
            continue
        runs.setdefault((driver, phase), []).append(lap)

    out: dict[tuple[str, str], AdherenceReport] = {}
    for (driver, phase), group in sorted(runs.items()):
        if phase == BASELINE_PHASE:
            continue
        baseline = runs.get((driver, BASELINE_PHASE))
        if not baseline:
            continue
        report = run_adherence(baseline, group)
        if report is not None:
            out[(driver, phase)] = report
    return out


ADHERENCE_COLUMNS = (
    "advice_prescribed",
    "advice_measured",
    "advice_followed",
    "advice_followed_rate",
    "advice_shift_sd",
)

# The detail behind those five, one row per thing a participant was told. Both
# ``gap`` and ``required`` are here on purpose: the first is how far off they
# were, the second how far they had to move for anyone to call it a change, and
# neither can be recovered from the other because ``required`` depends on that
# participant's own spread.
ADHERENCE_ROW_COLUMNS = (
    "driver",
    "before_phase",
    "after_phase",
    "corner",
    "apex_m",
    "metric",
    "category",
    "said",
    "said_in",
    "direction",
    "gap",
    "before_mean",
    "before_sd",
    "before_n",
    "after_mean",
    "after_n",
    "shift",
    "toward",
    "required",
    "shift_sd",
    "followed",
)


def adherence_columns(report: AdherenceReport | None) -> dict:
    """The export cells for one row, blank throughout when nothing was computed.

    Blank and not zero, for the reason the rest of the study export gives: a row
    nobody could measure is not a row where nobody followed anything.
    """
    if report is None:
        return dict.fromkeys(ADHERENCE_COLUMNS, "")
    return {
        "advice_prescribed": report.prescribed,
        "advice_measured": report.measured,
        "advice_followed": report.followed,
        "advice_followed_rate": "" if report.rate is None else report.rate,
        "advice_shift_sd": "" if report.mean_shift_sd is None else report.mean_shift_sd,
    }
