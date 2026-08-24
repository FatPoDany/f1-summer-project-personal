"""What to call a lap on screen, in the one form every view uses.

One comparison names two laps in four places at once -- the picker they were
chosen in, the legend on the track map, the readout under it, and the caption
over each video -- and a driver who reads a different name in each of them has
to work out for themselves that they are all the same lap. So there is one
form: the file the lap came from, and the time it ran.

Nothing here decides which lap is which; it only spells out the one it is
handed. Whichever lap is being compared against is the picker's answer, not
this module's assumption.
"""

from f1coach_core import Lap


def lap_caption(lap: Lap) -> str:
    """A lap named as the reference picker names it: source, then lap time."""
    return f"{lap.source.stem} · {lap.lap_time:.3f} s"
