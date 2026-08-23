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

from f1coach_core.features import _corner_facts
from f1coach_core.lap import Lap

# How far a measured difference must move before it is worth showing. Set to sit
# above ordinary lap-to-lap scatter rather than at a level that means anything
# on its own; they are reporting thresholds, not targets to drive to.
NOTABLE_BRAKE_POINT_M = 10.0
NOTABLE_MIN_SPEED_KMH = 3.0
NOTABLE_THROTTLE_POINT_M = 10.0
NOTABLE_COAST_M = 15.0

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

    @property
    def headline(self) -> str:
        if self.time_lost_s is None:
            return self.corner
        return f"{self.corner} · {self.time_lost_s:.2f} s"


BRAKING = "braking"
CORNER_SPEED = "corner speed"
THROTTLE = "throttle"
COASTING = "coasting"

# What each category is about, in the terms a driver would use. Handed to the
# model so its advice is about braking or about throttle, rather than about a
# stretch of graph.
CATEGORY_FOCUS = {
    BRAKING: "where to start braking for the corner",
    CORNER_SPEED: "how much speed to carry through the corner",
    THROTTLE: "when to get back on the throttle on the way out",
    COASTING: "the time spent neither braking nor accelerating",
}


def _candidates(fact: dict) -> list[tuple[float, str, str, str]]:
    """(how far past its threshold, difference, detail, category) per measured gap."""
    out: list[tuple[float, str, str, str]] = []

    def gap(key: str) -> float | None:
        mine, ref = fact.get(key), fact.get(f"ref_{key}")
        if mine is None or ref is None:
            return None
        return float(mine) - float(ref)

    brake = gap("brake_point_m")
    if brake is not None and abs(brake) >= NOTABLE_BRAKE_POINT_M:
        word = "earlier" if brake < 0 else "later"
        out.append((
            abs(brake) / NOTABLE_BRAKE_POINT_M,
            f"Braked {abs(brake):.0f} m {word}",
            f"brake point {fact['brake_point_m']:.0f} m vs {fact['ref_brake_point_m']:.0f} m",
            BRAKING,
        ))

    speed = gap("min_speed_kmh")
    if speed is not None and abs(speed) >= NOTABLE_MIN_SPEED_KMH:
        word = "slower" if speed < 0 else "faster"
        out.append((
            abs(speed) / NOTABLE_MIN_SPEED_KMH,
            f"{abs(speed):.0f} km/h {word} at the slowest point",
            f"minimum {fact['min_speed_kmh']:.0f} km/h vs {fact['ref_min_speed_kmh']:.0f} km/h",
            CORNER_SPEED,
        ))

    throttle = gap("throttle_reapply_m")
    if throttle is not None and abs(throttle) >= NOTABLE_THROTTLE_POINT_M:
        word = "later" if throttle > 0 else "earlier"
        out.append((
            abs(throttle) / NOTABLE_THROTTLE_POINT_M,
            f"Back on throttle {abs(throttle):.0f} m {word}",
            f"throttle at {fact['throttle_reapply_m']:.0f} m "
            f"vs {fact['ref_throttle_reapply_m']:.0f} m",
            THROTTLE,
        ))

    coast = gap("coast_distance_m")
    if coast is not None and abs(coast) >= NOTABLE_COAST_M:
        word = "more" if coast > 0 else "less"
        out.append((
            abs(coast) / NOTABLE_COAST_M,
            f"Coasted {abs(coast):.0f} m {word}",
            f"coasting {fact['coast_distance_m']:.0f} m "
            f"vs {fact['ref_coast_distance_m']:.0f} m",
            COASTING,
        ))
    return out


def lap_debrief(
    lap: Lap,
    reference: Lap,
    *,
    limit: int = 3,
    min_time_lost_s: float = MIN_TIME_LOST_S,
) -> list[DebriefPoint]:
    """The stretches where `lap` lost most time to `reference`, worst first.

    Only losses are reported: a participant reviewing their own drive wants the
    places to look at, and a corner they were quicker through is not one.
    """
    points = []
    for fact in _corner_facts(lap, reference):
        lost = fact.get("time_lost_s")
        if lost is None or lost < min_time_lost_s:
            continue
        candidates = _candidates(fact)
        # Rank by how far each gap ran past its own reporting threshold, so
        # measurements in different units stay comparable. Ties keep the order
        # above, which runs in the order a corner is driven.
        best = max(candidates, key=lambda item: item[0], default=None)
        span = fact["span_m"]
        points.append(
            DebriefPoint(
                corner=str(fact["corner"]),
                apex_m=float(fact["apex_m"]),
                span_m=(float(span[0]), float(span[1])),
                time_lost_s=round(float(lost), 3),
                difference="" if best is None else best[1],
                detail="" if best is None else best[2],
                category="" if best is None else best[3],
            )
        )
    points.sort(key=lambda point: point.time_lost_s, reverse=True)
    return points[:limit]


def corner_review_points(lap: Lap, reference: Lap | None = None) -> list[DebriefPoint]:
    """Every corner as a reviewable stretch, in the order it is driven.

    ``lap_debrief`` answers "what cost the most time", worst first, and drops the
    rest. That is right for a summary somebody reads and wrong for a table where
    they pick a corner by name and expect it to open: a corner they were quick
    through still has footage of them being quick through it.

    ``time_lost_s`` stays None without a reference, and the difference, detail
    and category are left empty rather than compared against the lap itself.
    """
    against = lap if reference is None else reference
    points = []
    for fact in _corner_facts(lap, against):
        best = None
        lost = None
        if reference is not None:
            lost = fact.get("time_lost_s")
            best = max(_candidates(fact), key=lambda item: item[0], default=None)
        span = fact["span_m"]
        points.append(
            DebriefPoint(
                corner=str(fact["corner"]),
                apex_m=float(fact["apex_m"]),
                span_m=(float(span[0]), float(span[1])),
                time_lost_s=None if lost is None else round(float(lost), 3),
                difference="" if best is None else best[1],
                detail="" if best is None else best[2],
                category="" if best is None else best[3],
            )
        )
    return points


def debrief_summary(lap: Lap, reference: Lap, points: list[DebriefPoint]) -> str:
    """One sentence a participant can act on, or an honest nothing-to-report."""
    total = lap.lap_time - reference.lap_time
    if not points:
        if total <= 0:
            return "This was your quickest lap of the session."
        return f"{total:.2f} s off your best lap, spread evenly rather than at any one corner."
    accounted = sum(point.time_lost_s for point in points)
    corners = ", ".join(point.corner for point in points)
    if accounted > total:
        # Real driving is not uniformly worse than a quicker lap: these corners
        # can cost more than the lap did, because the rest of it gave time back.
        # "16.68 s of it" against a 13.22 s deficit is arithmetic the reader can
        # see is impossible, and it discredits every other number on the screen.
        return (
            f"{total:.2f} s off your best lap. {corners} cost {accounted:.2f} s "
            "between them, and the rest of the lap gave some of that back."
        )
    return (
        f"{total:.2f} s off your best lap. {accounted:.2f} s of it went at {corners}."
    )
