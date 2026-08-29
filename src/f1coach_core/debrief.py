"""Where a driver lost time, and the measured difference that went with it.

The Analysis screen's corner table is built for someone reading traces. A
participant needs the few stretches of track that actually cost them, named in
plain language, with the numbers behind each one.

Nothing here interprets or advises. Each point states a measured time loss and
the largest measured difference over the same stretch; it deliberately stops
short of claiming the second caused the first, because the telemetry does not
show that. A model may later narrate these points, but they stand without one.
"""

from dataclasses import dataclass
from typing import NamedTuple

from f1coach_core.features import (
    BRAKING,
    COASTING,
    CORNER_SPEED,
    NOTABLE_BRAKE_POINT_M,
    NOTABLE_COAST_M,
    NOTABLE_MIN_SPEED_KMH,
    NOTABLE_THRESHOLDS,
    NOTABLE_THROTTLE_POINT_M,
    THROTTLE,
    CornerScatter,
    _corner_facts,
    notable_bar,
)
from f1coach_core.lap import Lap

# The reporting thresholds and the category names moved next to the fact keys
# they are about (features), because the cross-corner patterns measured there
# have to call the same difference notable as the debrief a participant reads
# and as the adherence held against it. They are re-exported here: this is
# still where a reader looks for what "notable" means to a driver.
__all__ = [
    "BRAKING",
    "CATEGORY_FOCUS",
    "COASTING",
    "CORNER_SPEED",
    "MIN_TIME_LOST_S",
    "NOTABLE_BRAKE_POINT_M",
    "NOTABLE_COAST_M",
    "NOTABLE_MIN_SPEED_KMH",
    "NOTABLE_THRESHOLDS",
    "NOTABLE_THROTTLE_POINT_M",
    "CornerScatter",
    "notable_bar",
    "THROTTLE",
    "DebriefPoint",
    "corner_review_points",
    "debrief_points",
    "debrief_summary",
    "lap_debrief",
    "review_points",
]

# Time loss below this is not worth a participant's attention.
MIN_TIME_LOST_S = 0.05


@dataclass(frozen=True)
class DebriefPoint:
    """One stretch of track that cost time, ready to render or replay."""

    corner: str
    apex_m: float
    span_m: tuple[float, float]
    # None when there is no reference to have lost time against. Not 0.0: a lap
    # reviewed on its own has no measured delta, and writing one down would put
    # a number in front of a participant that nothing measured.
    time_lost_s: float | None
    difference: str  # empty when nothing exceeded a reporting threshold
    detail: str  # the two measurements behind `difference`
    # What kind of driving this is about, so advice can be about that rather than
    # about a stretch of graph. Empty when no measurement moved far enough to
    # say -- an unexplained loss must not be given a category it did not earn.
    category: str = ""
    # Which fact key the difference was measured on, and by how much. The strings
    # above are what a participant read; these two are what a later session can
    # be held against, and they are recorded here rather than recomputed so that
    # adherence is measured against the advice actually given.
    metric: str = ""
    gap: float | None = None  # mine - reference, signed, in the metric's own unit
    # The bar this difference had to clear to be said at all: the fixed
    # threshold, raised to the driver's own spread through this corner where
    # that was wider. Carried rather than recomputed for the same reason as
    # `metric` and `gap` -- whatever holds a later run against this advice must
    # use the bar the advice was actually given under, not one derived later
    # from different laps.
    threshold: float | None = None

    @property
    def headline(self) -> str:
        if self.time_lost_s is None:
            return self.corner
        return f"{self.corner} · {self.time_lost_s:.2f} s"


# What each category is about, in the terms a driver would use. Handed to the
# model so its advice is about braking or about throttle, rather than about a
# stretch of graph.
CATEGORY_FOCUS = {
    BRAKING: "where to start braking for the corner",
    CORNER_SPEED: "how much speed to carry through the corner",
    THROTTLE: "when to get back on the throttle on the way out",
    COASTING: "the time spent neither braking nor accelerating",
}


class _Gap(NamedTuple):
    """One measured difference at a corner, ready to rank, render or track.

    ``metric`` and ``delta`` are what make a point comparable with a later run.
    The two rendered strings say what the participant read; these say which
    channel it was about and which way it would have to move. Advice here is
    always "move toward the reference lap", so the direction a participant was
    asked for is ``-sign(delta)`` and never needs storing separately.
    """

    score: float  # how far past its own threshold, so different units compare
    difference: str
    detail: str
    category: str
    metric: str
    delta: float
    bar: float  # the threshold it cleared, which is not the same at every corner


def _candidates(fact: dict, scatter: CornerScatter | None = None) -> list[_Gap]:
    """Every measured gap at one corner that ran past its reporting threshold.

    That threshold is the fixed one or this driver's own scatter through this
    corner, whichever is wider (``notable_bar``): a corner somebody repeats
    within a metre can raise a small difference, and one they never drive the
    same way twice cannot. It is also the divisor of ``score``, which is what
    holds four measurements in three units comparable when only one of them
    gets said.
    """
    corner = str(fact.get("corner", ""))
    out: list[_Gap] = []

    def gap(key: str) -> float | None:
        mine, ref = fact.get(key), fact.get(f"ref_{key}")
        if mine is None or ref is None:
            return None
        return float(mine) - float(ref)

    brake, limit = gap("brake_point_m"), notable_bar("brake_point_m", corner, scatter)
    if brake is not None and abs(brake) >= limit:
        word = "earlier" if brake < 0 else "later"
        out.append(_Gap(
            abs(brake) / limit,
            f"Braked {abs(brake):.0f} m {word}",
            f"brake point {fact['brake_point_m']:.0f} m vs {fact['ref_brake_point_m']:.0f} m",
            BRAKING,
            "brake_point_m",
            brake,
            limit,
        ))

    speed, limit = gap("min_speed_kmh"), notable_bar("min_speed_kmh", corner, scatter)
    if speed is not None and abs(speed) >= limit:
        word = "slower" if speed < 0 else "faster"
        out.append(_Gap(
            abs(speed) / limit,
            f"{abs(speed):.0f} km/h {word} at the slowest point",
            f"minimum {fact['min_speed_kmh']:.0f} km/h vs {fact['ref_min_speed_kmh']:.0f} km/h",
            CORNER_SPEED,
            "min_speed_kmh",
            speed,
            limit,
        ))

    throttle, limit = (
        gap("throttle_reapply_m"),
        notable_bar("throttle_reapply_m", corner, scatter),
    )
    if throttle is not None and abs(throttle) >= limit:
        word = "later" if throttle > 0 else "earlier"
        out.append(_Gap(
            abs(throttle) / limit,
            f"Back on throttle {abs(throttle):.0f} m {word}",
            f"throttle at {fact['throttle_reapply_m']:.0f} m "
            f"vs {fact['ref_throttle_reapply_m']:.0f} m",
            THROTTLE,
            "throttle_reapply_m",
            throttle,
            limit,
        ))

    coast, limit = gap("coast_distance_m"), notable_bar("coast_distance_m", corner, scatter)
    if coast is not None and abs(coast) >= limit:
        word = "more" if coast > 0 else "less"
        out.append(_Gap(
            abs(coast) / limit,
            f"Coasted {abs(coast):.0f} m {word}",
            f"coasting {fact['coast_distance_m']:.0f} m "
            f"vs {fact['ref_coast_distance_m']:.0f} m",
            COASTING,
            "coast_distance_m",
            coast,
            limit,
        ))
    return out


def _point(fact: dict, best: _Gap | None, lost: float | None) -> DebriefPoint:
    """One corner's DebriefPoint, however the caller decided what to say of it."""
    span = fact["span_m"]
    return DebriefPoint(
        corner=str(fact["corner"]),
        apex_m=float(fact["apex_m"]),
        span_m=(float(span[0]), float(span[1])),
        time_lost_s=None if lost is None else round(float(lost), 3),
        difference="" if best is None else best.difference,
        detail="" if best is None else best.detail,
        category="" if best is None else best.category,
        metric="" if best is None else best.metric,
        gap=None if best is None else round(best.delta, 3),
        threshold=None if best is None else round(best.bar, 3),
    )


def lap_debrief(
    lap: Lap,
    reference: Lap,
    *,
    limit: int = 3,
    min_time_lost_s: float = MIN_TIME_LOST_S,
    scatter: CornerScatter | None = None,
) -> list[DebriefPoint]:
    """The stretches where `lap` lost most time to `reference`, worst first.

    Only losses are reported: a participant reviewing their own drive wants the
    places to look at, and a corner they were quicker through is not one.
    """
    return debrief_points(
        _corner_facts(lap, reference),
        limit=limit,
        min_time_lost_s=min_time_lost_s,
        scatter=scatter,
    )


def debrief_points(
    facts: list[dict],
    *,
    limit: int = 3,
    min_time_lost_s: float = MIN_TIME_LOST_S,
    scatter: CornerScatter | None = None,
) -> list[DebriefPoint]:
    """The same debrief, from corner facts somebody else measured.

    Split out so a comparison against per-corner bests reads exactly as a
    comparison against one lap does. The rows have the same shape either way,
    and a participant should not be able to tell which produced their debrief
    from how it is worded.
    """
    points = []
    for fact in facts:
        lost = fact.get("time_lost_s")
        if lost is None or lost < min_time_lost_s:
            continue
        candidates = _candidates(fact, scatter)
        # Rank by how far each gap ran past its own reporting threshold, so
        # measurements in different units stay comparable. Ties keep the order
        # above, which runs in the order a corner is driven.
        best = max(candidates, key=lambda item: item.score, default=None)
        points.append(_point(fact, best, lost))
    points.sort(key=lambda point: point.time_lost_s, reverse=True)
    return points[:limit]


def corner_review_points(
    lap: Lap,
    reference: Lap | None = None,
    *,
    scatter: CornerScatter | None = None,
) -> list[DebriefPoint]:
    """Every corner as a reviewable stretch, in the order it is driven.

    ``lap_debrief`` answers "what cost the most time", worst first, and drops the
    rest. That is right for a summary somebody reads and wrong for a table where
    they pick a corner by name and expect it to open: a corner they were quick
    through still has footage of them being quick through it.

    ``time_lost_s`` stays None without a reference, and the difference, detail
    and category are left empty rather than compared against the lap itself.
    """
    against = lap if reference is None else reference
    return review_points(
        _corner_facts(lap, against), compared=reference is not None, scatter=scatter
    )


def review_points(
    facts: list[dict], *, compared: bool, scatter: CornerScatter | None = None
) -> list[DebriefPoint]:
    """Every corner as a reviewable stretch, from facts somebody else measured.

    ``compared`` says whether the ``ref_`` values came from other driving or
    from the lap itself; without it a lap compared with itself would be handed
    back a set of differences of exactly zero, dressed as findings.
    """
    points = []
    for fact in facts:
        best = None
        lost = None
        if compared:
            lost = fact.get("time_lost_s")
            best = max(
                _candidates(fact, scatter), key=lambda item: item.score, default=None
            )
        points.append(_point(fact, best, lost))
    return points


def debrief_summary(lap: Lap, reference: Lap, points: list[DebriefPoint]) -> str:
    """One sentence a participant can act on, or an honest nothing-to-report.

    Said of the lap actually compared against, which is whichever lap the driver
    picked. It read "off your best lap" from the days when the session best was
    the only reference on offer, and a sentence that calls a mid-session lap
    their best one is wrong about the one fact it asserts.

    Naming it also makes the other direction sayable: a lap can now be compared
    with a slower one, and "-1.20 s off" is not something to hand a driver.
    """
    against = reference.source.stem
    total = lap.lap_time - reference.lap_time
    if total > 0:
        standing = f"{total:.2f} s off {against}"
    elif total < 0:
        standing = f"{-total:.2f} s quicker than {against}"
    else:
        standing = f"The same lap time as {against}"
    if not points:
        if total > 0:
            return f"{standing}, spread evenly rather than at any one corner."
        return f"{standing}, with no corner to pick out."
    accounted = sum(point.time_lost_s for point in points)
    corners = ", ".join(point.corner for point in points)
    if accounted > total:
        # Real driving is not uniformly worse than a quicker lap: these corners
        # can cost more than the lap did, because the rest of it gave time back.
        # "16.68 s of it" against a 13.22 s deficit is arithmetic the reader can
        # see is impossible, and it discredits every other number on the screen.
        # The same sentence covers a lap that was quicker overall and still lost
        # time somewhere, which is what comparing with a slower lap looks like.
        return (
            f"{standing}. {corners} cost {accounted:.2f} s "
            "between them, and the rest of the lap gave some of that back."
        )
    return f"{standing}. {accounted:.2f} s of it went at {corners}."
