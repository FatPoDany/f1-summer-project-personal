"""What to call a lap on screen, in the one form every view uses.

One comparison names two laps in four places at once -- the picker they were
chosen in, the legend on the track map, the readout under it, and the caption
over each video -- and a driver who reads a different name in each of them has
to work out for themselves that they are all the same lap. So there is one
form: the file the lap came from, and the time it ran.

Nothing here decides which lap is which; it only spells out the one it is
handed. Whichever lap is being compared against is the picker's answer, not
this module's assumption.

The picker alone carries one mark the other three do not: which of the laps on
offer is the session's best. That is a fact about choosing a reference rather
than about the lap itself, so it goes after the name instead of into it.
"""

from f1coach_core import CompositeReference, Lap


def lap_caption(lap: Lap) -> str:
    """A lap named as the reference picker names it: source, then lap time."""
    return f"{lap.source.stem} · {lap.lap_time:.3f} s"


def reference_caption(lap: Lap, best: Lap | None) -> str:
    """A lap in the reference picker, marked if it is the session's best.

    The same name with a note after it, parenthesised and worded the way the
    Garage already words it, so the two screens agree about what a session best
    looks like.

    Marked by identity against the session's own best lap, never by taking the
    quickest lap on offer: a lap is absent from its own picker, so the quickest
    lap remaining would collect the mark exactly when the driver is already
    looking at the real one.
    """
    caption = lap_caption(lap)
    return f"{caption} (session best)" if lap is best else caption


def composite_caption(composite: CompositeReference) -> str:
    """The per-corner reference in the picker, named for what it is.

    Deliberately not shaped like a lap caption. It has no file name, because it
    came from several, and no lap time, because it never ran -- and a caption
    with those two slots filled in would be read as a lap, which is the one
    thing a driver must not conclude about it.
    """
    laps = len(composite.sources)
    return f"Best at each corner ({laps} lap{'' if laps == 1 else 's'})"
