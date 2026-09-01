"""Finding the footage of a coaching stretch inside a session recording.

The debrief speaks in metres along the track. A recording is a span of wall
clock. Joining them is the whole job here, and it is done through the
``wall_clock_s`` the recorder stamps on every telemetry sample rather than by
assuming the simulator kept real time -- because it does not, and every second
of the difference moves the clip a corner further from the one being discussed.

Two things separate the clocks, and the smaller one is the one that looks
likelier. A machine that cannot keep up runs the simulation slow. A participant
who opens the pause menu stops it dead, and that is what the study's captures
actually show: three of the five races carry a pause, the longest of them 828 s
against 405 s of racing. ``stalls`` finds them.

A lap that carries no wall clock produces no clips. That is the honest outcome:
the alternative is a clip that looks right and shows the wrong corner, which
would teach a participant something that never happened.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from f1coach_core.debrief import DebriefPoint
from f1coach_core.lap import Lap

WALL_CLOCK_COLUMN = "wall_clock_s"
# The simulator's own clock, which stops when the race does.
SIM_TIME_COLUMN = "sim_time_s"


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


# How far the wall clock must run ahead of the simulation before the recording
# is treated as showing a pause rather than a slow frame. The recorder writes
# telemetry every simulated step, so on a machine that is merely struggling the
# gaps between consecutive samples stay small and numerous; a gap of seconds
# between two of them means the simulator stopped, which is what the pause menu
# does. Measured against the study captures: 2.0 s finds every pause and no
# stutter, the largest of which was 0.6 s.
STALL_MIN_S = 2.0


def stalls(frame: pd.DataFrame, *, min_s: float = STALL_MIN_S) -> tuple[Window, ...]:
    """The wall-clock spans in which the race stood still and the camera did not.

    TORCS stops its clock when a participant opens the pause menu. The recorder
    outside it knows nothing of that and keeps writing the menu to the file, so
    a paused race leaves a stretch of recording in which nothing was driven --
    two thirds of one study session, in the case that found this.

    The span returned is the whole gap between the two samples that bracket it,
    which also gives away the one simulated step that really did happen inside
    it. That is under a twentieth of a second against pauses measured in
    minutes, and the alternative is to guess where in the gap the clock stopped.
    """
    if not {WALL_CLOCK_COLUMN, SIM_TIME_COLUMN} <= set(frame.columns):
        return ()  # a capture that predates one of the two clocks
    wall = pd.to_numeric(frame[WALL_CLOCK_COLUMN], errors="coerce")
    sim = pd.to_numeric(frame[SIM_TIME_COLUMN], errors="coerce")
    usable = wall.notna() & sim.notna()
    clock = wall[usable].to_numpy(dtype=float)
    time = sim[usable].to_numpy(dtype=float)
    if clock.size < 2:
        return ()
    order = np.argsort(clock, kind="stable")
    clock, time = clock[order], time[order]
    # Dead time, not elapsed time: a machine running at half speed produces many
    # small gaps and no large one, so comparing the two clocks rather than
    # thresholding the wall clock alone keeps a slow race from being cut up.
    dead = np.diff(clock) - np.diff(time)
    return tuple(
        Window(from_wall_clock=float(clock[i]), to_wall_clock=float(clock[i + 1]))
        for i in np.flatnonzero(dead >= min_s)
    )


@dataclass(frozen=True)
class Segment:
    """One stretch of a recording, and where it sits in the file that ships.

    File time and wall clock are the same thing until something is cut out of
    the file, and from the first cut onwards they are not: a frame a third of
    the way through a condensed file belongs to a later instant than a third of
    the way through the race. Carrying the two separately is what lets a frame
    be given back its wall clock afterwards.
    """

    at_file_s: float
    from_wall_clock: float
    seconds: float


def segments_of(
    started_at: float, duration_s: float, dropped: tuple[Window, ...] = ()
) -> tuple[Segment, ...]:
    """The recording as the shipped file holds it, once ``dropped`` is cut out.

    With nothing dropped this is one segment covering the file as recorded,
    which is the same answer as not asking. With spans dropped the segments are
    packed end to end, because that is what cutting them out leaves behind.
    """
    kept: list[tuple[float, float]] = []
    at = started_at
    finish = started_at + duration_s
    for window in sorted(dropped, key=lambda w: w.from_wall_clock):
        start = max(window.from_wall_clock, at)
        if start > at:
            kept.append((at, start))
        at = max(at, window.to_wall_clock)
    if finish > at:
        kept.append((at, finish))

    segments: list[Segment] = []
    cursor = 0.0
    for start, end in kept:
        segments.append(
            Segment(at_file_s=cursor, from_wall_clock=start, seconds=end - start)
        )
        cursor += end - start
    return tuple(segments)
