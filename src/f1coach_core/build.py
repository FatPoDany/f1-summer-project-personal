"""Which build of Apex is running, for the records that outlive it.

A study whose instrument changes mid-collection has to be able to say which
participants met which version of it. Nothing did: ``exposure.SCHEMA_VERSION``
names the shape of a log line and is not even written into one, the capture
manifest records the track and the car, and the audit records the model. None
of them changes when the advice does.

So this is stamped onto every recorded view. Two participants whose debriefs
were worded differently, or gated differently, are then separable afterwards
instead of pooled into one condition that never existed.

Resolved in the order the answer can be trusted:

*The frozen bundle's own stamp.* ``apex.spec`` writes the commit it was built
from into the bundle, so a packaged build carries its identity rather than
deriving one on a machine that has no repository on it.

*The checkout it is running from.* Useful and honest during development, and
marked ``+`` when the tree had uncommitted changes, because a build made from a
dirty tree is not the commit it names.

*Nothing.* An empty string, never a guess. A wrong build id is worse than an
absent one: it would silently merge two conditions.
"""

import subprocess
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

BUILD_ID_NAME = "build_id.txt"

# Long enough to be unambiguous in this repository, short enough to read in a
# spreadsheet cell beside a participant id.
_HASH_LENGTH = 7
_GIT_TIMEOUT_S = 5.0


def _stamped() -> str:
    """What the packaging step recorded, if this is a packaged build."""
    try:
        resource = files("f1coach_core") / "data" / BUILD_ID_NAME
        if not resource.is_file():
            return ""
        return resource.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError, ModuleNotFoundError, TypeError):
        return ""


def _from_checkout() -> str:
    """The commit this source tree is on, plus a mark if it has been edited."""
    root = Path(__file__).resolve().parent.parent.parent
    if not (root / ".git").exists():
        return ""
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
        if head.returncode != 0:
            return ""
        commit = head.stdout.strip()[:_HASH_LENGTH]
        if not commit:
            return ""
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
        return f"{commit}+" if dirty.stdout.strip() else commit
    except (OSError, subprocess.SubprocessError):
        return ""


@lru_cache(maxsize=1)
def app_build() -> str:
    """This build's identity, or an empty string when there is nothing to say.

    Cached: it cannot change while the process runs, and it is asked for once
    per recorded view.
    """
    return _stamped() or _from_checkout()
