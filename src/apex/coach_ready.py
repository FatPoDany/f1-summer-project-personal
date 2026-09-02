"""Whether the local model can be asked right now -- one answer, two callers.

The Session Debrief screen asks this, and so does the queue that fills a
session's debrief in before anybody opens the screen. They have to agree. Two
copies of the rule would eventually differ by one condition, and the way that
shows up is a stored record saying the model was unavailable while the screen
in front of the participant was busy narrating -- or the reverse, which is
worse: a debrief filed as read that nobody was ever shown.
"""

import os

from racecoach.granite import host as gh
from racecoach.granite import model as gm
from racecoach.granite.server import GraniteServer

# Written coaching is offered from Lap Analysis, which owns the download and its
# cancel button. Two screens asking for the same 2.1 GB would be two progress
# bars for one transfer, so this one points at that one rather than growing a
# second copy of it.
DOWNLOAD_ELSEWHERE = (
    "Written coaching needs a one-off download. Open a lap in Lap Analysis to "
    "start it; the measured debrief below is complete without it."
)


def coach_ready() -> bool:
    """Whether written coaching can run right now without downloading first."""
    if os.environ.get("GRANITE_BASE_URL"):
        return True
    capability = gh.capability()
    return capability.can_run and not capability.needs_download


def coach_blocked_reason() -> str:
    """Why the model cannot be asked, in a sentence, or empty when it can."""
    if os.environ.get("GRANITE_BASE_URL"):
        return ""
    capability = gh.capability()
    if not capability.can_run:
        return capability.reason
    if capability.needs_download:
        return DOWNLOAD_ELSEWHERE
    return ""


def model_name() -> str:
    return os.environ.get("GRANITE_MODEL") or gm.MODEL_REPO.replace("-GGUF", "")


def endpoint(server: GraniteServer) -> str:
    """The model endpoint to use, started if it is ours to start.

    A researcher who pointed Apex at their own server keeps it: starting a
    second one on top would be presumptuous and would fight for the port.
    """
    return os.environ.get("GRANITE_BASE_URL") or server.start()
