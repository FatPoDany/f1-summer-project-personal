"""The bundled sample session — demos must never depend on the network or credentials.

Three synthetic laps of the same circuit (banker, best, ragged) so the Garage
has deltas to show and coaching has a story to tell. Regenerate with
scripts/make_sample_session.py.
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
