"""The bundled sample session — demos must never depend on the network or credentials.

Five recorded laps of Aalborg driven by study participant 0822 in the coached
phase, split from the handover they sent
(``win_collect_data/0822-coached-20260822-102500.zip``) and carrying that
identity in their own headers.

Real laps, deliberately. The sample is the first thing anybody opens, and a
synthetic stand-in taught every reader a track, a spread of lap times and a set
of mistakes that never happened. These carry world position too, so the track
map is populated on first launch rather than reporting that the lap cannot be
drawn.
"""

from importlib.resources import as_file, files

from f1coach_core.lap import Lap
from f1coach_core.session import Session, load_session


def load_sample_session() -> Session:
    resource = files("f1coach_core") / "data" / "sample_session"
    with as_file(resource) as path:
        return load_session(path)


def load_sample_lap() -> Lap:
    """The sample session's best lap — the default thing to show on first paint."""
    best = load_sample_session().best_lap
    assert best is not None, "bundled sample session must contain laps"
    return best
