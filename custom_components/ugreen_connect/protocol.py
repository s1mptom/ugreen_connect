"""The charger's own protocol: frames in, readings out.

Kept clear of Home Assistant and of aiohttp on purpose, exactly as
``session.py`` is. Every byte offset in here was established against a live
charger -- and that is precisely the kind of knowledge a test suite has to be
able to reach without an installation standing in the way.

The frame::

    TYPE(1) CMD(1) LEN(2, big endian) PAYLOAD(LEN) CRC16(2, MODBUS, low byte first)
"""

from __future__ import annotations

import logging
from typing import Any, Final, NamedTuple

_LOGGER = logging.getLogger(__name__)

FRAME_QUERY = 0xAA
FRAME_NOTIFY = 0xEE
FRAME_SETTING = 0x11

QUERY_GET_DEVICE_STATE = 1
QUERY_GET_SN = 5
QUERY_GET_POWER_INFO = 6
QUERY_GET_UPGRADE_STATUS = 7
QUERY_GET_WIFI_SSID = 8
QUERY_GET_PRODUCT_VERSION = 10

SETTING_SET_BRIGHTNESS = 1
SETTING_SET_SLEEP_TIME = 2
SETTING_SET_CHARGING_MODE = 4
SETTING_SET_SCREENSAVER = 5

# Which frames a diagnostics download may carry, as an allowlist rather than a
# list of the ones to leave out.
#
# The direction matters more than the contents. That file is written to be
# posted publicly, and a denylist publishes anything added here later by
# default -- while two of the queries above answer with the household's own
# details: the Wi-Fi network name in plain ASCII, and the serial number the
# redaction elsewhere goes to some trouble to remove. Forgetting to add a frame
# here costs a reader some bytes; forgetting to exclude one costs somebody
# their network name.
PUBLISHABLE_FRAMES: Final[frozenset[str]] = frozenset(
    f"{FRAME_QUERY:02X}/{cmd}"
    for cmd in (
        QUERY_GET_DEVICE_STATE,
        QUERY_GET_POWER_INFO,
        QUERY_GET_PRODUCT_VERSION,
    )
)

# One 7-byte record per port, then up to one handshake-protocol byte per port.
PORT_RECORD = 7

# The charging protocol each port negotiated, reported one byte per port at the
# tail of the power frame.
HANDSHAKE_PROTOCOL: Final[dict[int, str]] = {
    0: "none", 1: "QC", 2: "AFC", 3: "FCP", 4: "UFCS", 5: "PD", 6: "PPS", 7: "AVS",
}

# The order the power report puts its ports in, per model. `productNo` is what
# the account API calls the model, and it is fetched already for the device
# page, so knowing which list to use costs nothing extra.
PORTS_BY_MODEL: Final[dict[str, tuple[str, ...]]] = {
    # Read off the app's own port table, and confirmed against a charger.
    "X783": ("C1", "C2", "C3", "C4", "C5", "C6", "A1", "DC"),
    # Nexode Pro 160W, confirmed against one by its owner in issue #2: devices
    # were put on the built-in cable and on C2, and records 0 and 2 -- and only
    # those -- carried voltage.
    "X776": ("C-Cable", "C1", "C2", "A"),
}


def ports_for(model: str | None, body_length: int) -> tuple[str, ...]:
    """What to call each port of a report this long, on this model.

    A model nobody has a table for still gets its readings: seven bytes of
    measurement and up to one protocol byte per port means the report's own
    length says how many there are. Only the names are lost, and numbered ports
    are honest about that -- better than one model's labels on another's
    sockets.

    "Up to" is what makes this awkward, and it is measured rather than assumed.
    The X783 sends 63 bytes for eight ports: 56 of measurement and only seven
    protocol bytes, the last one simply absent. The X776 sends 32 for four --
    28 and four, with nothing left off. So the count is taken as high as the
    protocol block allows and no higher than the measurements can fill, which
    reads both shapes without having to know which it is looking at.

    One length is genuinely undecidable: 56 is eight ports with no protocol
    tail and seven ports with a full one, and nothing in the frame separates
    them. This answers seven, and a model table is the only thing that could
    answer better.

    Resist the obvious repair. Letting the measurement count win on exact
    multiples of seven looks like it settles 56 and breaks 63 instead, which
    is 7 x 9: the X783 would come back with nine ports.
    """
    if known := PORTS_BY_MODEL.get(model or ""):
        return known
    by_protocol = -(-body_length // (PORT_RECORD + 1))   # rounded up
    by_measurement = body_length // PORT_RECORD
    return tuple(f"P{index + 1}" for index in range(min(by_protocol, by_measurement)))


# Which fields of the GET_DEVICE_STATE reply have been read on real hardware,
# per model.
#
# Deliberately not the same list as the ports above, and the difference is the
# point. How many ports a report describes can be counted from its length, so
# readings work anywhere. Where brightness or the screensaver sit cannot be
# counted -- they are offsets, established by changing a value in the app and
# watching which byte moved. On a model laid out differently they would read
# something plausible and wrong, and every one of these entities writes back as
# well as reads.
#
# Per field rather than per model, because a model does not arrive understood
# all at once. The X776's owner mapped its brightness, screen timeout, charging
# mode, screensaver group and current wallpaper by hand, one change at a time;
# its wallpaper library is still not understood. Under an all-or-nothing rule
# that knowledge would sit unused until the last byte fell.
STATE_FIELDS_ALL: Final[frozenset[str]] = frozenset(
    {
        "brightness",
        "sleep_time",
        "charging_mode",
        "screensaver",
        "screensaver_theme",
        "screensaver_flag",
        "wallpaper",
        "wallpapers",
    }
)

STATE_FIELDS_BY_MODEL: Final[dict[str, frozenset[str]]] = {
    "X783": STATE_FIELDS_ALL,
    # `wallpapers` is missing on purpose: the byte where the X783 counts its
    # library reads 5 on a 160W whether three ids follow or four, so whatever
    # it counts, it is not them.
    "X776": STATE_FIELDS_ALL - {"wallpapers"},
}

# Reading a byte and writing it are separate permissions, because the commands
# are not symmetrical. Brightness and the screen timeout are set by a command
# carrying one byte, so knowing where to read them is knowing how to set them.
# The charging mode is not: its command carries the whole parameter block, and
# the 160W's block is nine bytes shorter than the one that shape was learned
# on. Sending 35 bytes into a 26-byte space would land on the screensaver group
# and the wallpaper id, which sit directly after it.
#
# So a field is writable where a write has actually been made and read back.
# Everything else is shown and refuses, which is a better answer than either
# hiding it or sending a frame nobody has tried.
STATE_WRITABLE_BY_MODEL: Final[dict[str, frozenset[str]]] = {
    "X783": STATE_FIELDS_ALL,
    "X776": frozenset({"brightness", "sleep_time"}),
}


class StateLayout(NamedTuple):
    """Where the tail of a state reply sits on one model.

    Brightness, the screen timeout and the charging mode are at 2, 3 and 4 on
    both chargers seen so far, so they stay constants. Everything after the
    charging mode's parameter block moves with its length: the 160W's block is
    26 bytes where the 300W's is 35, so its screensaver group and wallpaper sit
    nine bytes earlier.

    ``wallpaper_count`` is None where that byte has been seen and not
    understood -- reading a list from a count that does not count is worse than
    publishing no list.
    """

    screensaver: int          # then clock style at +1 and time format at +2
    image_id: int             # six ASCII bytes naming the picture on screen
    wallpaper_count: int | None


STATE_LAYOUT_BY_MODEL: Final[dict[str, StateLayout]] = {
    "X783": StateLayout(screensaver=40, image_id=43, wallpaper_count=49),
    "X776": StateLayout(screensaver=31, image_id=34, wallpaper_count=None),
}


def state_fields(model: str | None) -> frozenset[str]:
    """Which parts of a state reply may be believed on this model.

    A charger whose model the account API would not name is read in full, as it
    always has been: that is far more often the charger this was written on than
    a stranger, and the alternative is losing the screen to one failed lookup.
    Reading is the half that can be wrong and recovered from; state_writable
    next door refuses the other half for the same unknown model.
    """
    if model is None:
        return STATE_FIELDS_ALL
    return STATE_FIELDS_BY_MODEL.get(model, frozenset())


def state_writable(model: str | None) -> frozenset[str]:
    """Which of this model's state fields may be set as well as read.

    Nothing, when nobody could say which model this is. Reading an unknown
    charger at the X783's offsets shows wrong numbers, which is recoverable by
    looking again; writing at them puts bytes somewhere else on the device --
    the 160W keeps its screensaver group nine bytes earlier, so a write meant
    for one setting lands on another. Refusing is the right direction for a
    guard to fail in, and the controls come back the moment a lookup succeeds.
    """
    if model is None:
        return frozenset()
    return STATE_WRITABLE_BY_MODEL.get(model, frozenset())


def state_layout(model: str | None) -> StateLayout:
    """Where to read this model's screen settings; the X783's where unknown."""
    return STATE_LAYOUT_BY_MODEL.get(model or "", STATE_LAYOUT_BY_MODEL["X783"])


def state_layout_measured(model: str | None) -> bool:
    """Whether that layout was measured on this model or borrowed from the X783.

    Reading at borrowed offsets is a wrong number that the next successful
    lookup corrects. Keeping bytes read at them is different: they are kept in
    order to be sent back, and a 160W read at the 300W's offsets yields 35
    bytes with its screensaver group inside -- which is the write
    `state_writable` refuses, arriving later and from store.
    """
    return (model or "") in STATE_LAYOUT_BY_MODEL


def crc16_modbus(data: bytes) -> int:
    """CRC-16/MODBUS, the checksum the charger's frames carry."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def build_frame(frame_type: int, cmd: int, payload: bytes = b"\x00") -> str:
    """Encode one protocol frame as the uppercase hex string ``PT_data`` wants."""
    body = bytes((frame_type, cmd)) + len(payload).to_bytes(2, "big") + payload
    return (body + crc16_modbus(body).to_bytes(2, "little")).hex().upper()


def frame_body(value: str, frame_type: int, cmd: int) -> bytes | None:
    """Return a frame's payload if it is the reply we asked for and the CRC holds."""
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        _LOGGER.debug("PT_data is not hex: %r", value)
        return None
    if len(raw) < 6:
        return None
    length = int.from_bytes(raw[2:4], "big")
    body = raw[4 : 4 + length]
    if len(body) != length:
        return None
    if crc16_modbus(raw[: 4 + length]) != int.from_bytes(
        raw[4 + length : 6 + length], "little"
    ):
        _LOGGER.debug("PT_data CRC mismatch: %s", value)
        return None
    if raw[0] != frame_type or raw[1] != cmd:
        return None
    return body


def parse_power_frame(
    value: str, model: str | None = None
) -> dict[str, dict[str, Any]] | None:
    """Decode a ``GET_POWER_INFO`` reply into ``{port_name: {volt, amp, watt}}``.

    Returns None for anything else -- the property also holds replies to other
    commands, and the last one simply stays there until the device sends a new.
    """
    body = frame_body(value, FRAME_QUERY, QUERY_GET_POWER_INFO)
    if body is None:
        return None
    named = ports_for(model, len(body))
    measured = PORT_RECORD * len(named)
    if not named or len(body) < measured:
        _LOGGER.debug("power body too short: %d for %d ports", len(body), len(named))
        return None

    def u16(offset: int) -> int:
        return int.from_bytes(body[offset : offset + 2], "big")

    ports: dict[str, dict[str, Any]] = {}
    for index, name in enumerate(named):
        base = PORT_RECORD * index
        # The protocol byte block follows the port records. A port with nothing
        # attached reports 0 ("none") -- and so does a port whose byte was
        # never sent, which is the honest answer for the X783's DC socket.
        proto_at = measured + index
        ports[name] = {
            "voltage": u16(base) / 10,
            "current": u16(base + 2) / 10,
            "power": u16(base + 4) / 10,
            "protocol": HANDSHAKE_PROTOCOL.get(
                body[proto_at] if len(body) > proto_at else 0, "unknown"
            ),
        }
    return ports
