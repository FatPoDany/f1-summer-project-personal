"""Granite/SCR (Simulated Car Racing) wire protocol — pure functions, no sockets.

The standard format is verified against the TORCS 1.3.7 ``scr_server``
sources. This repository's non-blocking TORCS 1.3.9 Granite Bridge preserves
that wire contract and appends tyre wear, temperature, pressure, graining,
and the simulator clock. Older clients ignore those additional tags.

- state/action tuples are `(tag value value …)` — SimpleParser.cpp
- the client identifies with a line containing `(init a0 … a18)`, the 19
  rangefinder angles in degrees; the server answers ``***identified***``
  and defaults the angles to -90…+90 in 10° steps when unparseable
  (scr_server.cpp:287-295)
- actions are `(accel f)(brake f)(gear d)(steer f)(clutch f)(focus d)(meta d)`
  in exactly that order — CarControl.cpp:53-59
- Granite Bridge optionally accepts `(coach BASE64URL)` after those standard
  fields. It is display-only and decodes to one printable 31-byte HUD line.
  Omitting it produces the byte-for-byte standard action message.
- race control literals: ``***shutdown***`` (:699), ``***restart***`` (:579)
- the server listens on UDP 3001 (+bot index) — scr_server.cpp:60
"""

import base64

IDENTIFIED = "***identified***"
SHUTDOWN = "***shutdown***"
RESTART = "***restart***"
DEFAULT_PORT = 3001
MAX_DATAGRAM_BYTES = 8192  # Granite bridge adds TORCS 1.3.9 tyre channels.
HUD_LINE_BYTES = 31  # tCarCtrl.msg has two 32-byte, NUL-terminated driver lines.

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
    hud_advice: str | None = None,
) -> str:
    """One compatible action message plus an optional display-only HUD tuple."""
    message = (
        f"(accel {accel:g})(brake {brake:g})(gear {int(gear)})"
        f"(steer {steer:g})(clutch {clutch:g})(focus {int(focus)})(meta {int(meta)})"
    )
    if hud_advice is None:
        return message
    return message + f"(coach {_encode_hud_advice(hud_advice)})"


def sanitize_hud_advice(message: str) -> str:
    """Bound model prose to one printable ASCII TORCS HUD line.

    This is deliberately lossy: control characters become spaces and
    non-ASCII characters become ``?``. The strict encoder below remains the
    final protocol boundary and rejects anything outside its contract.
    """
    if not isinstance(message, str):
        raise TypeError("HUD advice must be text")
    printable = "".join(
        character
        if 0x20 <= ord(character) <= 0x7E
        else (" " if character.isspace() else "?")
        for character in message
    )
    compact = " ".join(printable.split())
    return (compact[:HUD_LINE_BYTES].rstrip() or "Advice available")


def _encode_hud_advice(message: str) -> str:
    if not isinstance(message, str):
        raise TypeError("HUD advice must be text")
    try:
        raw = message.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("HUD advice must contain printable ASCII only") from exc
    if not 1 <= len(raw) <= HUD_LINE_BYTES:
        raise ValueError(f"HUD advice must contain 1-{HUD_LINE_BYTES} ASCII bytes")
    if any(byte < 0x20 or byte > 0x7E for byte in raw):
        raise ValueError("HUD advice must contain printable ASCII only")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
