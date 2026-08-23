"""Names for the two sample laps the suite keeps comparing.

Not a test module: it exists so nothing reaches for a fixed index again. The
suite used to spell this pair ``session.laps[2], session.best_lap``, which was
the slow lap and the quick one only for as long as the bundled sample happened
to be three synthetic laps whose second was the fastest. When the sample became
five recorded laps whose *third* is the fastest, every one of those call sites
quietly started comparing a lap with itself: same numbers on both sides, no
opportunities in the evidence, and a wall of failures that said nothing about
what had actually changed.
"""

from dataclasses import replace

from f1coach_core import Lap, Session, load_sample_session


def slow_and_best(session: Session | None = None) -> tuple[Lap, Lap]:
    """The sample's slowest lap and its best — the pair with something to explain.

    0822's first lap is 40.4 s off their third. That gap is what makes the pair
    useful to a test: real evidence, real opportunities, and a comparison whose
    result cannot be mistaken for a rounding error.
    """
    session = session or load_sample_session()
    best = session.best_lap
    assert best is not None, "bundled sample session must contain laps"
    slow = min(session.laps, key=lambda lap: -lap.lap_time)
    return slow, best


def slow_lap(session: Session | None = None) -> Lap:
    """The sample's slowest lap, for the single-lap analysis paths."""
    return slow_and_best(session)[0]


def lap_without_position(session: Session | None = None) -> Lap:
    """A sample lap with its world position taken away.

    Laps from a source that never carried a position channel are complete and
    analysable; they simply cannot be drawn on a circuit. The recorded sample
    does carry it, so the honest way to exercise that path is to remove it here
    rather than to keep a positionless lap around and call it representative.
    """
    lap = slow_lap(session)
    return replace(lap, df=lap.df.drop(columns=["x", "y"]))
