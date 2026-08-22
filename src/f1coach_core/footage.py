"""Finding the footage of a coaching stretch inside a session recording.

The debrief speaks in metres along the track. A recording is a span of wall
clock. Joining them is the whole job here, and it is done through the
``wall_clock_s`` the recorder stamps on every telemetry sample rather than by
assuming the simulator kept real time -- because when a laptop cannot keep up it
does not, and every second of drift moves the clip a corner further from the one
being discussed.

A lap that carries no wall clock produces no clips. That is the honest outcome:
the alternative is a clip that looks right and shows the wrong corner, which
would teach a participant something that never happened.
"""

from dataclasses import dataclass

from f1coach_core.debrief import DebriefPoint
from f1coach_core.lap import Lap

WALL_CLOCK_COLUMN = "wall_clock_s"


@dataclass(frozen=True)
class Window:
    """The wall-clock span of one stretch of track."""

    from_wall_clock: float
    to_wall_clock: float

    @property
    def seconds(self) -> float:
        return self.to_wall_clock - self.from_wall_clock


def has_wall_clock(lap: Lap) -> bool:
    """Whether this lap can be lined up with anything recorded outside TORCS."""
    return WALL_CLOCK_COLUMN in lap.df.columns


def window_for(lap: Lap, point: DebriefPoint) -> Window | None:
    """When, in wall-clock terms, the driver was on this stretch of track.

    None when the lap predates the wall clock, or when the stretch falls outside
    the samples that were kept. Both mean there is no footage to show, and
    saying so is better than guessing at an offset.
    """
    if not has_wall_clock(lap):
        return None
    dist = lap.df["dist"]
    start, end = point.span_m
    first = int(dist.searchsorted(start, side="left"))
    # side="right" lands one past the stretch; step back to the last sample
    # actually inside it, so the clip does not run past what is being discussed.
    last = int(dist.searchsorted(end, side="right")) - 1
    if first >= len(dist) or last < first:
        return None

    clock = lap.df[WALL_CLOCK_COLUMN]
    from_clock = float(clock.iloc[first])
    to_clock = float(clock.iloc[min(last, len(clock) - 1)])
    if not (from_clock > 0 and to_clock >= from_clock):
        return None  # a lap recorded before the clock existed, or a corrupt row
    return Window(from_wall_clock=from_clock, to_wall_clock=to_clock)


def windows_for(lap: Lap, points: list[DebriefPoint]) -> dict[int, Window]:
    """Every stretch that can be located in a recording, by its index.

    Indexed rather than listed so a caller can tell which stretches have footage
    and which do not without re-deriving the order.
    """
    found: dict[int, Window] = {}
    for index, point in enumerate(points):
        window = window_for(lap, point)
        if window is not None:
            found[index] = window
    return found
