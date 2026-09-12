"""Setting a charging mode must carry that mode's parameters, not 35 zeros.

The 35 bytes after the mode byte belong to the mode in force. A charger left in
`priority` by the app reported 02 in the first of them; selecting `priority`
from Home Assistant sent zeros and that byte stayed 00 afterwards, so the write
is what decides the setting -- the charger keeps no copy to fall back on.

The frame below is that charger's real GET_DEVICE_STATE reply, taken before the
byte was overwritten.
"""

import asyncio

import pytest
from conftest import rtcx as rtcx_module

pytestmark = pytest.mark.skipif(
    rtcx_module is None, reason="rtcx needs aiohttp, which is not installed here"
)

IOT = "iot-one"
OTHER = "iot-two"

# X783, mode 3 (`priority`), parameter block 02 followed by 34 zeros.
STATE_PRIORITY = (
    "aa0100560037640103020000000000000000000000000000000000000000000000000000"
    "000000000000000000010135443742454306354437424543423136413535343942363631"
    "433431303733464341344244314434353145e8ec"
)


def _recorder(into: list):
    async def _setting(iot_id, cmd, payload):
        into.append((iot_id, cmd, payload))

    return _setting


class _Api:
    """Only the field the client reads out of it while being built."""

    user_id = "someone"


class _Client:
    """The real client with its two round trips replaced.

    ``_ask`` and ``_setting`` are the seam: everything below them is HTTP, and
    everything above them -- the decode, the remembering, the payload the
    setting is built from -- is what this is about.
    """

    def __init__(self, replies: dict[str, str] | None = None) -> None:
        self.client = rtcx_module.RtcxClient(None, _Api())
        self.sent: list[tuple[str, int, bytes]] = []
        self.replies = replies or {}

        async def _ask(iot_id, frame_type, cmd, payload=b"\x00"):
            return self.replies.get(iot_id)

        self.client._ask = _ask
        self.client._setting = _recorder(self.sent)

    def read(self, iot_id=IOT, model="X783"):
        return asyncio.run(self.client.async_device_state(iot_id, model))

    def set_mode(self, mode, iot_id=IOT, model="X783"):
        asyncio.run(self.client.async_set_charging_mode(iot_id, mode, model))
        return self.sent[-1][2]


def test_the_frame_this_rests_on_says_what_it_claims():
    # If the fixture stops being a priority reply with a non-zero first
    # parameter, every assertion below passes for the wrong reason.
    body = rtcx_module.frame_body(STATE_PRIORITY, rtcx_module.FRAME_QUERY, 1)
    assert body[rtcx_module.STATE_CHARGING_MODE] == 3
    assert body[rtcx_module.STATE_MODE_PARAMS] == 2
    assert not any(body[rtcx_module.STATE_MODE_PARAMS + 1 : 40])


def test_a_preset_is_set_with_the_parameters_it_was_carrying():
    c = _Client({IOT: STATE_PRIORITY})
    c.read()
    payload = c.set_mode(3)
    assert payload[0] == 3
    assert len(payload) == 1 + rtcx_module.CHARGING_MODE_PARAMS
    assert payload[1] == 2, "the priority byte the app set was discarded"
    assert not any(payload[2:])


def test_a_mode_never_seen_running_is_set_with_zeros():
    # Nothing has been watched running in `adaptive_power`, and inventing its
    # parameters would be worse than sending none.
    c = _Client({IOT: STATE_PRIORITY})
    c.read()
    payload = c.set_mode(0)
    assert payload == bytes([0]) + bytes(rtcx_module.CHARGING_MODE_PARAMS)


def test_one_charger_does_not_answer_for_another():
    # One client serves a whole account; a block learnt from one charger says
    # nothing about the next one's settings.
    c = _Client({IOT: STATE_PRIORITY})
    c.read(IOT)
    assert c.set_mode(3, OTHER) == bytes([3]) + bytes(rtcx_module.CHARGING_MODE_PARAMS)
    assert c.set_mode(3, IOT)[1] == 2


def test_a_reply_that_never_came_teaches_nothing():
    c = _Client({})
    assert c.read() is None
    assert c.set_mode(3) == bytes([3]) + bytes(rtcx_module.CHARGING_MODE_PARAMS)


def test_the_160w_block_stops_where_its_screensaver_begins():
    """The 160W's block is 26 bytes, and its screensaver group sits right after.

    Built here rather than captured -- nobody on this side has that charger --
    so it proves the arithmetic against the layout the decode already uses, not
    the layout itself. That is measured in `test_the_tail_moves_with_the_parameter_block`.
    """
    body = bytearray(59)
    body[rtcx_module.STATE_CHARGING_MODE] = 1
    params = bytes(range(0xA0, 0xA0 + 26))
    body[rtcx_module.STATE_MODE_PARAMS : 31] = params
    # Distinctive, so copying too much is visible rather than plausible.
    body[31:34] = b"\xee\xee\xee"
    body[34:40] = b"ABCDEF"

    frame = rtcx_module.build_frame(rtcx_module.FRAME_QUERY, 1, bytes(body))
    c = _Client({IOT: frame})
    assert c.read(model="X776") is not None

    kept = c.client._mode_params[(IOT, 1)]
    assert kept == params
    assert len(kept) == 26
    assert b"\xee" not in kept, "the screensaver group was copied into the parameters"


def test_a_charger_read_at_a_borrowed_layout_is_not_remembered():
    """An unknown model is read at the X783's offsets, and that is fine to show.

    It is not fine to keep: these bytes exist to be written back, and on a 160W
    the X783's offsets take 35 where the block is 26 -- the screensaver group
    and the wallpaper id ride along. `state_writable` refuses that write today;
    a remembered block would be a way for it to arrive tomorrow.
    """
    body = bytearray(59)
    body[rtcx_module.STATE_CHARGING_MODE] = 1
    body[rtcx_module.STATE_MODE_PARAMS : 31] = bytes(range(0xA0, 0xA0 + 26))
    body[31:34] = b"\xee\xee\xee"
    body[34:40] = b"ABCDEF"

    frame = rtcx_module.build_frame(rtcx_module.FRAME_QUERY, 1, bytes(body))
    c = _Client({IOT: frame})
    # Read as a charger whose model nobody could name -- which is how an X776
    # looks on the polls before its product lookup answers.
    assert c.read(model=None) is not None
    assert c.client._mode_params == {}
    assert c.set_mode(1) == bytes([1]) + bytes(rtcx_module.CHARGING_MODE_PARAMS)


def test_all_zeros_is_an_answer_and_not_an_absence(caplog):
    """A preset whose parameters really are zero must not read as never-seen.

    Both send the same frame, so the difference only shows in the log -- and a
    warning that cries wolf on every ordinary mode change is one nobody reads
    when the real one arrives. (This does not discriminate `is None` from
    truthiness: `bytes` are falsy only when empty. It pins the behaviour, not
    the spelling.)
    """
    body = bytearray(86)
    body[rtcx_module.STATE_CHARGING_MODE] = 0
    body[43:49] = b"ABCDEF"
    frame = rtcx_module.build_frame(rtcx_module.FRAME_QUERY, 1, bytes(body))
    c = _Client({IOT: frame})
    c.read()

    caplog.clear()
    assert c.set_mode(0) == bytes(1 + rtcx_module.CHARGING_MODE_PARAMS)
    assert "has not been seen running" not in caplog.text

    # And the mode that genuinely has not been seen still says so.
    assert c.set_mode(2) == bytes([2]) + bytes(rtcx_module.CHARGING_MODE_PARAMS)
    assert "has not been seen running" in caplog.text


def test_what_was_learned_survives_a_restart():
    """A mode's parameters can only be learned while that mode is running.

    So a process that forgets them sends the next mode change out empty, and
    the charger has no copy to fall back on -- which is the original bug,
    arriving once after every restart.
    """
    learned = _Client({IOT: STATE_PRIORITY})
    learned.read()
    saved = learned.client.mode_params_snapshot()
    assert saved == {f"{IOT}:3": learned.client._mode_params[(IOT, 3)].hex()}

    # A new process, told only what the store held. Nothing is read.
    restarted = _Client()
    restarted.client = rtcx_module.RtcxClient(None, _Api(), mode_params=saved)
    restarted.client._setting = _recorder(restarted.sent)
    assert restarted.set_mode(3)[1] == 2


def test_a_store_written_by_something_else_does_not_stop_the_charger():
    """The file outlives this code, so a line it cannot read is dropped.

    One mode change going out empty is a smaller price than an integration
    that refuses to start.
    """
    client = rtcx_module.RtcxClient(
        None,
        _Api(),
        mode_params={
            f"{IOT}:3": "02" + "00" * 34,
            f"{IOT}:notanumber": "00" * 35,
            f"{IOT}:1": "not hex at all",
            # Not a string at all: `bytes.fromhex` raises TypeError here, and
            # letting that out would fail the config entry on every retry.
            f"{IOT}:6": None,
            f"{IOT}:7": ["00" * 35],
            # Lengths that are not a block on any model measured. The empty one
            # is the dangerous shape: `bytes.fromhex("")` raises nothing, and a
            # block of no bytes would put a one-byte payload on the wire.
            f"{IOT}:2": "",
            f"{IOT}:4": "00" * 34,
            f"{IOT}:5": "00" * 200,
        },
    )
    assert set(client._mode_params) == {(IOT, 3)}


def test_a_store_of_the_wrong_shape_is_ignored_rather_than_fatal():
    # Read at startup, so raising here fails the config entry, which retries
    # into the same crash. The cache is relearned in a minute; the integration
    # is not.
    for wrong in ([{"a": "b"}], "a string", 7):
        assert rtcx_module.RtcxClient(None, _Api(), mode_params=wrong)._mode_params == {}


def test_a_dropped_entry_does_not_shorten_the_payload():
    # The point of refusing them: whatever survives is a whole block, so the
    # frame that reaches the charger is the length the command takes.
    c = _Client()
    c.client = rtcx_module.RtcxClient(None, _Api(), mode_params={f"{IOT}:3": ""})
    c.client._setting = _recorder(c.sent)
    assert len(c.set_mode(3)) == 1 + rtcx_module.CHARGING_MODE_PARAMS


def test_a_block_of_the_wrong_model_s_length_is_not_sent(caplog):
    """26 bytes into a 35-byte command, or the reverse, is a write gone astray.

    Unreachable while the cache is keyed by the charger -- one charger is one
    model -- so this guards the direction the code could be taken later, which
    is the direction `protocol.py` spends a paragraph warning about.
    """
    c = _Client()
    c.client = rtcx_module.RtcxClient(
        None, _Api(), mode_params={f"{IOT}:3": "00" * 26}
    )
    c.client._setting = _recorder(c.sent)

    payload = c.set_mode(3)
    assert len(payload) == 1 + 35, "a 160W block was sent to a 300W"
    assert "26 bytes where" in caplog.text

    # And the same block is the right length for the model it came from.
    c.sent.clear()
    asyncio.run(c.client.async_set_charging_mode(IOT, 3, "X776"))
    assert len(c.sent[-1][2]) == 1 + 26


def test_a_mode_never_seen_is_set_with_that_model_s_own_length():
    # The fallback is as model-specific as the block it stands in for: 35 zeros
    # into the 160W's 26-byte block would land on its screensaver group.
    c = _Client()
    assert len(c.set_mode(1, model="X776")) == 1 + 26
    assert len(c.set_mode(1, model="X783")) == 1 + 35


def test_an_unmeasured_model_is_not_written_to_at_all():
    """Reading at a borrowed layout is recoverable; writing at one is not.

    `state_layout` answers with the X783's where it has not been told, which is
    the right guess for a reading and the wrong one for a frame that goes to a
    charger. `state_writable` refuses the same charger a layer up; this refuses
    at the layer that touches the hardware.
    """
    c = _Client()
    for unknown in (None, "X999"):
        with pytest.raises(rtcx_module.UgreenError):
            asyncio.run(c.client.async_set_charging_mode(IOT, 3, unknown))
    assert c.sent == []


def test_a_mode_the_charger_refuses_is_warned_about_every_time(caplog):
    """Because a block is learned from the mode the charger reports being in.

    A charger that took the empty block is then in that mode, so the next read
    learns it and the warning is over. A charger that refused it never entered
    the mode -- `dc_turbo` on an X783 -- so there is nothing to learn and the
    next attempt is in exactly the same position. Saying so again is right: the
    change really is not taking.
    """
    c = _Client()
    for _ in range(3):
        c.set_mode(2)
    assert caplog.text.count("has not been seen running") == 3

    # And the other way: once the charger reports the mode, it goes quiet.
    body = bytearray(86)
    body[rtcx_module.STATE_CHARGING_MODE] = 2
    body[43:49] = b"ABCDEF"
    c.replies[IOT] = rtcx_module.build_frame(rtcx_module.FRAME_QUERY, 1, bytes(body))
    c.read()
    caplog.clear()
    c.set_mode(2)
    assert "has not been seen running" not in caplog.text
