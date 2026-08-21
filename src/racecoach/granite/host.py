"""Whether this particular machine can run the coach at all.

Participants install on their own laptops and those vary a lot, so the app has to
answer this before offering a 2.1 GB download. Declining early and saying why is
much better than downloading for twenty minutes and then thrashing: the
deterministic debrief is complete on its own, and a participant who is told the
coach will not run here has lost nothing but the wait.
"""

import ctypes
import os
import sys
from dataclasses import dataclass

from racecoach.granite import model as gm

# The weights are 2.1 GB and a 4096-token context costs a few hundred MB more,
# so the coach needs roughly 3 GB resident. Asking for 6 GB total leaves the
# operating system and the browser the participant left open some room; below
# that the machine would swap rather than fail, which is worse to sit through.
MINIMUM_MEMORY_BYTES = 6 * 1024**3


@dataclass(frozen=True)
class Capability:
    """What this machine can do, and a sentence explaining it either way."""

    can_run: bool
    reason: str
    total_memory_bytes: int = 0
    model_present: bool = False
    download_bytes: int = 0

    @property
    def needs_download(self) -> bool:
        return self.can_run and not self.model_present


def total_memory_bytes() -> int:
    """Physical RAM, or 0 when the platform will not say."""
    if sys.platform == "win32":
        return _windows_memory()
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 0


def _windows_memory() -> int:
    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    try:
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return 0
    except (AttributeError, OSError):  # pragma: no cover - not reachable off Windows
        return 0
    return int(status.ullTotalPhys)


def capability(*, server_present: bool | None = None) -> Capability:
    """Decide, and say why, before anything is downloaded or started."""
    if server_present is None:
        from racecoach.granite.server import server_binary

        server_present = server_binary() is not None

    if not server_present:
        return Capability(
            can_run=False,
            reason=(
                "This copy of Apex was installed without the local model server, "
                "so written coaching is not available. Everything else works."
            ),
        )

    memory = total_memory_bytes()
    if memory and memory < MINIMUM_MEMORY_BYTES:
        return Capability(
            can_run=False,
            reason=(
                f"This computer has {memory / 1024**3:.1f} GB of memory and the "
                f"coach needs about {MINIMUM_MEMORY_BYTES / 1024**3:.0f} GB. Your "
                "lap analysis is unaffected -- it is measured from your telemetry, "
                "not written by a model."
            ),
            total_memory_bytes=memory,
        )

    # Cheap on purpose: this runs on the GUI thread. GraniteServer
    # digests the file properly before it will load it.
    present = gm.looks_present()
    if present:
        reason = "Written coaching is ready to use on this computer."
    else:
        reason = (
            f"Written coaching needs a one-off {gm.MODEL_SIZE / 1e9:.1f} GB download. "
            "After that it runs entirely on this computer, offline."
        )
    return Capability(
        can_run=True,
        reason=reason,
        total_memory_bytes=memory,
        model_present=present,
        download_bytes=0 if present else gm.MODEL_SIZE,
    )
