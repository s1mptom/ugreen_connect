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
        QUERY_GET_UPGRADE_STATUS,
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
# mode, screensaver group, wallpaper library and port outputs by hand, one
# change at a time, over several evenings. Under an all-or-nothing rule that
# knowledge would have sat unused until the last byte fell.
STATE_FIELDS_ALL: Final[frozenset[str]] = frozenset(
    {
        "brightness",
        "sleep_time",
        "charging_mode",
        "custom",
        "priority",
        "dc_turbo",
        "screensaver",
        "screensaver_theme",
        "screensaver_flag",
        "wallpaper",
        "wallpapers",
        "port_outputs",
        "mcu_version",
    }
)

STATE_FIELDS_BY_MODEL: Final[dict[str, frozenset[str]]] = {
    # The 300W has no per-port switches in its app, so nothing to read.
    "X783": STATE_FIELDS_ALL - {"port_outputs"},
    # `custom` is missing on purpose: five wattages, a shared pair in steps and
    # six masks is the X783's shape, and the 160W's block is 26 bytes where
    # this shape needs 35, with nobody having mapped what it holds. Its
    # wallpaper library is read without the X783's count byte (see
    # StateLayout).
    # `priority` too: its mask is the first byte of a block nobody has mapped
    # on this model, and a port choice read from the wrong byte is a control
    # that sets the wrong ports. And `dc_turbo`, for the same reason: the 160W
    # has no DC port for its first two bytes to be about.
    "X776": STATE_FIELDS_ALL - {"custom", "priority", "dc_turbo"},
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
    # Everything the X783 has been written to and read back -- except the
    # custom block, which is out for a different reason than the rest. Nothing
    # here writes it: there is no control over it, and `custom` is not in
    # SELECTABLE_MODES. Replaying the block whole is safe -- a state reply's 36
    # bytes sent back verbatim changed nothing -- and the charger accepts a
    # one-field edit, C4 from 60 W to 55 W landing on that field alone. But the
    # app can change a group's protocol mask along with its limit, and which
    # mask goes with which limit is not mapped, so setting one limit on its own
    # could leave a pair the app never sends. `custom` arrived in this table by
    # riding STATE_FIELDS_ALL, so it is refused rather than left to say yes by
    # accident the day somebody builds the entity that asks.
    "X783": STATE_FIELDS_ALL - {"custom", "port_outputs", "mcu_version"},
    "X776": frozenset({"brightness", "sleep_time"}),
}

# Where Home Assistant may install the firmware the cloud offers. The X783's
# path was watched whole, in the UGREEN app, taking one from 1.2.1 to 1.2.3:
# the command, the progress it reports, and the state it comes back in. The
# 160W very likely takes the same command -- the app sends it without looking
# at the model -- but a flash is the one write here that cannot be taken back
# by setting the old value again, so it waits until someone has seen it.
FIRMWARE_INSTALL_MODELS: Final[frozenset[str]] = frozenset({"X783"})


class StateLayout(NamedTuple):
    """Where the tail of a state reply sits on one model.

    Brightness, the screen timeout and the charging mode are at 2, 3 and 4 on
    both chargers seen so far, so they stay constants. Everything after the
    charging mode's parameter block moves with its length: the 160W's block is
    26 bytes where the 300W's is 35, so its screensaver group and wallpaper sit
    nine bytes earlier.

    ``wallpaper_count`` is None where that byte has been seen and not
    understood -- reading a list from a count that does not count is worse than
    publishing no list. The 160W's byte in that place read 5 with three ids
    after it, with four, and after its owner put a new picture on it, so it
    counts something else. Its list is read from ``wallpaper_start`` to the end
    of the reply instead: the reply grows six bytes a picture.
    """

    screensaver: int          # then clock style at +1 and time format at +2
    image_id: int             # six ASCII bytes naming the picture on screen
    wallpaper_count: int | None
    wallpaper_start: int | None = None  # where the ids start, when uncounted


STATE_LAYOUT_BY_MODEL: Final[dict[str, StateLayout]] = {
    "X783": StateLayout(screensaver=40, image_id=43, wallpaper_count=49),
    "X776": StateLayout(screensaver=31, image_id=34, wallpaper_count=None, wallpaper_start=41),
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

# --- The custom mode's parameter block --------------------------------------
#
# The 35 bytes belonging to whichever mode is in force. Settled against the
# app's own editor on a live X783 by moving one slider at a time and reading
# the frame back: five ports carry a plain wattage, C6 and A share one setting
# -- one slider in the app, one byte here -- and each group then has a bitmask
# of the protocols it may negotiate.
#
# Only while custom is the mode running. A preset keeps its own settings in the
# same bytes -- `priority` a bitmask of its priority ports, `dc_turbo` its DC
# voltage -- so reading them at this layout produces numbers, and wrong ones.
CUSTOM_PORTS: Final[tuple[str, ...]] = ("C1", "C2", "C3", "C4", "C5", "C6+A")
# The shared C6+A slider offers 0, 15 and 30 W, and stores the step rather than
# the watts. The five plain ports store watts outright.
#
# Measured rather than remembered, on frames taken with that group at 15 W and
# then 30 W: the byte reads 1 and 2. Before those it was 0 in everything
# captured, so the multiplier was unobservable -- zero times anything is zero,
# and no test could have caught a wrong one.
#
# The slider offers exactly 0, 15 and 30, so "steps of 15" and "an index into
# those three" fit the same bytes. What is pinned is the reachable range; the
# multiplication is the reading that fits it rather than one the charger has
# confirmed. A model whose slider went further would tell them apart.
CUSTOM_SHARED_STEP: Final = 15
# Bit positions in a group's protocol mask. The app lists exactly these seven,
# in this order -- and for bits 4, 6 and 7 the order is all that ties a name to
# a bit. Which bits exist is measured: 0xFD is every box the app offers, ticked
# at once. Which name belongs to which is measured for the other four, by
# reading the app's protocol screen beside the frame: bit 0 alone at mask 0x01,
# bits 0, 2 and 5 at 0x25, bits 0, 2 and 3 at 0x0D. With bit 0 settled by 0x01,
# bit 2 is the only other bit in both of the latter and AFC the only other name
# on both screens, which settles 2 -- and the remainders then give 5 and 3.
# Moving any bit is caught, since the tests pin the set of positions. Renaming
# one is caught where a screen was read, and for 6 by an assertion spelling out
# 0x65's four names -- which restates the list order rather than checking it.
# That mask is C3, C4 and C5 in the CUSTOM_STATE frame, on the charger it came
# from, with 4 and 7 clear, so reading any of those three screens there would
# settle 6; no port on the second charger carries it. For 4 and 7 no frame
# holds one without the other, so nothing separates them at all. Bit 1 has
# never been seen set: ticking every box the app offers gives 0xFD, the seven
# below.
CUSTOM_PROTOCOLS: Final[dict[int, str]] = {
    0: "Apple5V/2.4A",
    2: "AFC",
    3: "SCP",
    4: "UFCS",
    5: "5-11V PPS",
    6: "5-21V PPS",
    7: "AVS",
}
# Where a state reply keeps the charging mode, and where that mode's parameter
# block starts -- at 4 and 5 on both models seen. The block runs from there up to
# the model's screensaver group, `StateLayout.screensaver`: 35 bytes ending
# before byte 40 on the X783, 26 ending before byte 31 on the 160W. On the X783
# its bytes are in the order the setting command takes them, which is what lets
# a block read back be sent back as it is.
STATE_CHARGING_MODE = 4
STATE_MODE_PARAMS = 5
# The value the mode byte takes for "custom" -- the key of that name in
# CHARGING_MODES.
# Repeated rather than imported: `tests/conftest.py` loads this module inside a
# stand-in package that has `const` in it, so `from .const import` would
# resolve there, but `tests/test_diagnostics_privacy.py` builds a package
# without it and compiling this file then fails outright. One literal is the
# cheaper of the two, and the two are held together by a test named
# test_the_custom_mode_byte_is_the_one_the_mode_table_names.
CUSTOM_MODE = 4
# And "priority" and "dc_turbo", held to the table the same way, by
# test_the_priority_mode_byte_is_the_one_the_mode_table_names and
# test_the_dc_turbo_mode_byte_is_the_one_the_mode_table_names.
PRIORITY_MODE = 3
DC_TURBO_MODE = 2
# What the custom decoder needs about the X783's block, in body bytes: where
# the masks start, where the block ends, and how many plain limits come
# before the shared C6+A byte.
STATE_CUSTOM_MASKS = 16
STATE_CUSTOM_END = 40
CUSTOM_LIMITS = 5


def parse_custom_mode(
    body: bytes, model: str | None = None
) -> list[dict[str, Any]] | None:
    """Decode the parameter block only the custom charging mode fills in.

    Layout, established against the app's editor on a live charger by changing
    one slider at a time and reading the frame back::

         5..14   C1..C5 power limit, U16 big endian, in watts -- or a byte
                 of something and then a U8 wattage at 6, 8, 10, 12, 14, which
                 fits every frame anyone has equally well, since no port on
                 this charger exceeds 255 W and the odd bytes are zero in all
                 of them. Not distinguishable, and it changes nothing that is
                 published
        15       C6 and A together, one byte -- what it counts is in the
                 comment on CUSTOM_SHARED_STEP, and rests on a slider with
                 three positions
        16..39   four bytes per group, C1 first, read big-endian as that
                 group's protocol bitmask -- only the low byte has ever been
                 non-zero

    Read only while custom is the active mode, and that gate is the important
    part. The block was thought to be zero under a preset -- it was under the
    preset this layout was worked out beside, which made "not all zero" look
    like a safe way to ask whether a custom mode exists. A second X783 running
    `priority` with C2 as its priority port carries `02` at byte 5, and
    decoding that at this layout gives C1 a 512 W limit -- on a port whose
    slider in the app stops at 140 W.

    So whatever else the block holds under a preset, it is not this layout, and
    this parser cannot say what it is; `rtcx.py` records what `priority` and
    `dc_turbo` keep there. `body[4] == 4` settles it, where "not all zero"
    would also hide a custom mode whose ports are genuinely all at zero -- a
    claim about the app nobody here has checked.
    """
    if "custom" not in state_fields(model):
        # Five plain wattages, a shared pair counted in steps, then a mask
        # each: that shape is the X783's, and another model's ports do not
        # divide the same way -- the 160W's block is 26 bytes where this
        # shape needs 35, and nobody has mapped what it holds. Guessing would
        # put numbers on a page that mean nothing, which is worse than
        # showing none.
        return None
    if len(body) < STATE_CUSTOM_END:
        return None
    if body[STATE_CHARGING_MODE] != CUSTOM_MODE:
        return None

    limits = [
        int.from_bytes(body[STATE_MODE_PARAMS + 2 * i : STATE_MODE_PARAMS + 2 * i + 2], "big")
        for i in range(CUSTOM_LIMITS)
    ]
    # The shared group stores its step rather than its wattage.
    limits.append(body[STATE_MODE_PARAMS + 2 * CUSTOM_LIMITS] * CUSTOM_SHARED_STEP)

    groups = []
    for index, name in enumerate(CUSTOM_PORTS):
        at = STATE_CUSTOM_MASKS + 4 * index
        mask = int.from_bytes(body[at : at + 4], "big")
        groups.append(
            {
                "port": name,
                "limit": limits[index],
                "protocols": [
                    label for bit, label in CUSTOM_PROTOCOLS.items() if mask >> bit & 1
                ],
                "mask": mask,
            }
        )
    return groups


# --- The priority mode's ports ----------------------------------------------
#
# Under `priority` the block's first byte says which ports are charged first,
# as a bitmask. Read off a live X783 by changing the choice in the app one step
# at a time: C2 alone reads 2, C3 alone reads 4, C1 with C3 reads 5 -- so C1 is
# the bit the 5 leaves. The app offers C1, C2 and C3, in any combination, all
# three at once included.
PRIORITY_PORTS: Final[tuple[str, ...]] = ("C1", "C2", "C3")


def parse_priority(body: bytes, model: str | None = None) -> list[str] | None:
    """Which ports the priority mode charges first, while it is the mode.

    None under any other mode: the byte is then that mode's own setting --
    under `dc_turbo` the DC port's voltage -- and reading it as ports would put
    C1 and C2 in front for a charger giving its DC port 20 V.
    """
    if "priority" not in state_fields(model):
        return None
    if len(body) <= STATE_MODE_PARAMS or body[STATE_CHARGING_MODE] != PRIORITY_MODE:
        return None
    mask = body[STATE_MODE_PARAMS]
    return [port for bit, port in enumerate(PRIORITY_PORTS) if mask >> bit & 1]


def priority_mask(ports: list[str] | tuple[str, ...] | set[str]) -> int:
    """The byte for a set of ports charged first. Unknown names are refused."""
    unknown = set(ports) - set(PRIORITY_PORTS)
    if unknown:
        raise ValueError(f"not a priority port: {', '.join(sorted(unknown))}")
    return sum(1 << PRIORITY_PORTS.index(port) for port in set(ports))


# --- The DC turbo mode's settings -------------------------------------------
#
# Under `dc_turbo` the block's first byte is the DC port's voltage and the
# second its Always On switch, 0 or 1. Read off a live X783 by changing one
# control at a time in the app: the voltage byte reads 1 at 12 V, 2 at 15 V and
# 3 at 20 V, and the two bytes move independently.
DC_VOLTAGES: Final[dict[int, int]] = {1: 12, 2: 15, 3: 20}
DC_VOLTAGE_BYTE: Final[dict[int, int]] = {volts: byte for byte, volts in DC_VOLTAGES.items()}


def parse_dc_turbo(body: bytes, model: str | None = None) -> dict[str, Any] | None:
    """The DC port's voltage and Always On switch, while `dc_turbo` is the mode.

    None under any other mode, where the two bytes are that mode's own: under
    `priority` the first is the port mask, and C2 alone would read as 15 V.

    A voltage byte outside the three the app sets is reported as None rather
    than guessed at, while the switch beside it still reads.
    """
    if "dc_turbo" not in state_fields(model):
        return None
    if len(body) <= STATE_MODE_PARAMS + 1 or body[STATE_CHARGING_MODE] != DC_TURBO_MODE:
        return None
    return {
        "voltage": DC_VOLTAGES.get(body[STATE_MODE_PARAMS]),
        "always_on": bool(body[STATE_MODE_PARAMS + 1]),
    }


# --- The 160W's port outputs -----------------------------------------------
#
# The 160W's app turns ports off one by one, which the 300W's does not. Its
# owner turned off the built-in cable, then C1, then A, and exactly three bytes
# of the state reply went from 01 to 00: 8, 15 and 22. They sit seven bytes
# apart, one per group of the parameter block. C2 and A are one switch in the
# app, and so one byte here.
#
# Read only. The app sets them with SET_PORT_CONTROL, whose payload nobody has
# watched go out, and a guessed frame is not something to send a charger.
PortGroup = tuple[str, tuple[str, ...], int]
PORT_OUTPUTS_BY_MODEL: Final[dict[str, tuple[PortGroup, ...]]] = {
    "X776": (
        ("C-Cable", ("C-Cable",), 8),
        ("C1", ("C1",), 15),
        ("C2 & A", ("C2", "A"), 22),
    ),
}


def port_output_groups(model: str | None) -> tuple[PortGroup, ...]:
    """This model's port switches: a name, the ports it covers, its byte."""
    return PORT_OUTPUTS_BY_MODEL.get(model or "", ())


def parse_port_outputs(body: bytes, model: str | None = None) -> dict[str, bool] | None:
    """Whether each of this model's port switches is on; None where it has none."""
    groups = port_output_groups(model)
    if "port_outputs" not in state_fields(model) or not groups:
        return None
    if len(body) <= max(offset for _, _, offset in groups):
        return None
    return {name: bool(body[offset]) for name, _, offset in groups}


# GET_UPGRADE_STATUS answers two bytes: a status and a percentage. The status
# names are the app's own. It reads NOT_UPGRADING for the first seconds after
# the command, while the charger is still fetching the file, and the app waits
# through that rather than calling it a failure.
UPGRADE_IDLE = 0
UPGRADE_RUNNING = 1
UPGRADE_DONE = 2
UPGRADE_FAILED = 3


def parse_upgrade_status(body: bytes) -> tuple[int, int] | None:
    """(status, percent) from a GET_UPGRADE_STATUS reply; None if too short."""
    if len(body) < 2:
        return None
    return body[0], min(body[1], 100)


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
        # Not the value: a frame can be the Wi-Fi name or the serial as ASCII.
        _LOGGER.debug("PT_data is not hex: %d characters", len(value or ""))
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
        _LOGGER.debug(
            "PT_data CRC mismatch on a 0x%02X/%d frame, %d bytes", raw[0], raw[1], len(raw)
        )
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
