"""SCR (Simulated Car Racing) wire protocol — pure functions, no sockets.

Every format here is verified against the torcs-1.3.7 scr_server sources,
not folklore (see docs/DATA_AVAILABILITY.md, source S4):

- state/action tuples are `(tag value value …)` — SimpleParser.cpp
- the client identifies with a line containing `(init a0 … a18)`, the 19
  rangefinder angles in degrees; the server answers ``***identified***``
  and defaults the angles to -90…+90 in 10° steps when unparseable
  (scr_server.cpp:287-295)
- actions are `(accel f)(brake f)(gear d)(steer f)(clutch f)(focus d)(meta d)`
  in exactly that order — CarControl.cpp:53-59
- race control literals: ``***shutdown***`` (:699), ``***restart***`` (:579)
- the server listens on UDP 3001 (+bot index) — scr_server.cpp:60
"""

IDENTIFIED = "***identified***"
SHUTDOWN = "***shutdown***"
RESTART = "***restart***"
DEFAULT_PORT = 3001

RANGEFINDER_ANGLES_DEG = tuple(-90.0 + 10.0 * i for i in range(19))


def init_string(bot_id: str = "SCR", angles: tuple[float, ...] = RANGEFINDER_ANGLES_DEG) -> str:
    """The identification line, e.g. ``SCR(init -90 -80 … 90)``."""
    if len(angles) != 19:
        raise ValueError(f"SCR wants exactly 19 rangefinder angles, got {len(angles)}")
    joined = " ".join(f"{angle:g}" for angle in angles)
    return f"{bot_id}(init {joined})"


def parse_state(message: str) -> dict[str, float | list[float]]:
    """`(tag v …)` tuples -> dict; single values unwrapped, arrays kept as lists.

    Mirrors SimpleParser::parse: scan '(' to ')', first token is the tag.
    Malformed tuples are skipped — a UDP packet is not worth crashing over.
    """
    state: dict[str, float | list[float]] = {}
    open_at = message.find("(")
    while open_at != -1:
        close_at = message.find(")", open_at)
        if close_at == -1:
            break
        parts = message[open_at + 1 : close_at].split()
        if len(parts) >= 2:
            try:
                values = [float(part) for part in parts[1:]]
            except ValueError:
                values = []
            if values:
                state[parts[0]] = values[0] if len(values) == 1 else values
        open_at = message.find("(", close_at + 1)
    return state


def format_actions(
    accel: float,
    brake: float,
    steer: float,
    gear: int,
    clutch: float = 0.0,
    focus: int = 0,
    meta: int = 0,
) -> str:
    """One action message, field order exactly as CarControl::toString."""
    return (
        f"(accel {accel:g})(brake {brake:g})(gear {int(gear)})"
        f"(steer {steer:g})(clutch {clutch:g})(focus {int(focus)})(meta {int(meta)})"
    )
