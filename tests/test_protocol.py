"""How a power report is read, on the two chargers anyone has measured.

Both frames below came off hardware and carry their own CRC, so they are the
bytes the device sent rather than a transcription of them. They are here
because the port count is the one thing in this integration that cannot be
checked by reasoning: it depends on a report's length, and the two models
disagree about what a report of a given length contains.
"""

import pytest
from conftest import const
from conftest import protocol as p

# A Nexode Pro 300W, eight ports, C3 charging at 25 W and cables on C1 and C2.
# Sixty-three bytes: fifty-six of measurement and only *seven* protocol bytes.
# The eighth is not sent, which is why counting ports as len // 8 gives seven
# here and loses the DC socket -- quietly, with the protocol bytes then read
# out of the middle of port eight's measurements.
X783_FRAME = (
    "aa06003f00330000000001003300000000010117000900fb010000000000000000000000"
    "00000000000000000000000000000000000000000000000000000500000000541b"
)

# A Nexode Pro 160W, four ports, with devices on the built-in cable and on C2.
# Thirty-two bytes: twenty-eight of measurement and four protocol bytes, none
# left off. Posted by its owner in issue #2.
X776_FRAME = (
    "aa06002000c70005006301000000000000000035001f00a401"
    "00000000000000050005005c4d"
)


# Two `GET_DEVICE_STATE` replies from one X783, custom mode active, taken with
# the shared C6+A slider at 15 W and then at 30 W in the UgreenConnect app.
# Nothing else was touched between them, and exactly two bytes differ:
#
#   [15]  0x01 -> 0x02   the shared limit, counted in steps
#   [39]  0x01 -> 0x25   the low byte of that group's protocol mask
#
# The wattage each was taken at is written here because it cannot be read out
# of the bytes -- it is the thing they are evidence for. Before these, [15] was
# zero in every frame anyone had, which is why the multiplier could not be
# checked at all.
SHARED_15 = (
    "aa0100560037640004000f000f008c003c000f0100000001000000010000006500000065"
    "000000010000000101010033314632303706423136413535343942363631433431303733"
    "31443435314533314632303746434134424437ae"
)

SHARED_30 = (
    "aa0100560037640004000f000f008c003c000f0200000001000000010000006500000065"
    "000000010000002501010033314632303706423136413535343942363631433431303733"
    "3144343531453331463230374643413442445a17"
)


def test_the_300w_sends_one_protocol_byte_short():
    body = p.frame_body(X783_FRAME, p.FRAME_QUERY, p.QUERY_GET_POWER_INFO)
    assert body is not None and len(body) == 63
    assert len(p.ports_for("X783", len(body))) == 8


def test_the_160w_sends_a_protocol_byte_for_every_port():
    body = p.frame_body(X776_FRAME, p.FRAME_QUERY, p.QUERY_GET_POWER_INFO)
    assert body is not None and len(body) == 32
    assert len(p.ports_for("X776", len(body))) == 4


def test_counting_by_length_survives_both_shapes():
    # The rule that has to hold without knowing which model sent the report:
    # as many ports as the protocol block allows, no more than the
    # measurements can fill.
    assert len(p.ports_for(None, 63)) == 8     # 300W, one byte short
    assert len(p.ports_for(None, 32)) == 4     # 160W, complete
    assert len(p.ports_for(None, 31)) == 4     # a 160W with the same quirk
    assert len(p.ports_for(None, 28)) == 4     # measurements only


def test_the_300w_keeps_its_dc_port():
    # len // 8 is 7 for this frame. Losing DC is the failure this guards.
    ports = p.parse_power_frame(X783_FRAME, "X783")
    assert ports is not None and len(ports) == 8
    assert "DC" in ports


def test_the_300w_reads_what_it_measured():
    ports = p.parse_power_frame(X783_FRAME, "X783")
    assert ports["C3"] == {'voltage': 27.9, 'current': 0.9, 'power': 25.1, 'protocol': 'PD'}
    # A cable with nothing on it: the port is live at 5.1 V and drawing nothing.
    assert ports["C1"]["voltage"] == 5.1 and ports["C1"]["power"] == 0.0
    # The socket whose protocol byte was never sent reads "none", which happens
    # to be the truth.
    assert ports["DC"]["protocol"] == "none"


def test_the_160w_ports_are_named_in_the_order_they_are_wired():
    ports = p.parse_power_frame(X776_FRAME, "X776")
    assert ports is not None
    assert ports["C-Cable"] == {
        "voltage": 19.9, "current": 0.5, "power": 9.9, "protocol": "PD"
    }
    assert ports["C2"]["voltage"] == 5.3
    # The two the owner had nothing plugged into.
    assert ports["C1"]["voltage"] == 0.0 and ports["A"]["voltage"] == 0.0


def test_a_model_nobody_has_a_table_for_is_still_read():
    # Same readings, numbered instead of named -- the alternative is one
    # model's labels on another model's sockets.
    named = p.parse_power_frame(X776_FRAME, "X776")
    numbered = p.parse_power_frame(X776_FRAME, "X999")
    assert numbered is not None and len(numbered) == 4
    assert numbered["P1"] == named["C-Cable"]
    assert numbered["P3"] == named["C2"]


def test_a_frame_that_is_not_a_power_report_is_refused():
    # The property holds the last reply to any question, not only this one.
    state = "aa01003e00376400040000000000000000000000000000000000000000000000000000000000000000000000010100ffffffffffff024231364135353439423636311f91"  # noqa: E501 - one frame, one line; splitting it hides what it is
    assert p.parse_power_frame(state, "X783") is None


def test_the_ambiguous_length_leans_low_and_says_so():
    # 56 is eight ports with no protocol tail and seven with a full one. The
    # frame cannot separate them; this answers seven.
    assert len(p.ports_for(None, 56)) == 7
    # And the repair that suggests itself -- letting the measurement count win
    # on exact multiples of seven -- would break the charger this was written
    # on: 63 is 7 x 9.
    assert len(p.ports_for(None, 63)) == 8


def test_only_frames_known_to_be_harmless_may_be_published():
    # An allowlist, so a query added later does not travel by default. Two of
    # the ones this client can send answer with the household's own details.
    assert f"{p.FRAME_QUERY:02X}/{p.QUERY_GET_POWER_INFO}" in p.PUBLISHABLE_FRAMES
    assert f"{p.FRAME_QUERY:02X}/{p.QUERY_GET_DEVICE_STATE}" in p.PUBLISHABLE_FRAMES
    assert f"{p.FRAME_QUERY:02X}/{p.QUERY_GET_WIFI_SSID}" not in p.PUBLISHABLE_FRAMES
    assert f"{p.FRAME_QUERY:02X}/{p.QUERY_GET_SN}" not in p.PUBLISHABLE_FRAMES
# --- what may be believed, and what may be set -----------------------------


def test_the_tail_moves_with_the_parameter_block():
    # The 160W's block is 26 bytes where the 300W's is 35, so everything after
    # it sits nine bytes earlier. Measured on one, not derived.
    x783, x776 = p.state_layout("X783"), p.state_layout("X776")
    assert (x783.screensaver, x783.image_id) == (40, 43)
    assert (x776.screensaver, x776.image_id) == (31, 34)
    assert x783.screensaver - x776.screensaver == 9


def test_a_count_nobody_has_watched_counting_is_not_read():
    """The 160W's byte there read 5 with three ids after it, and with four."""
    assert p.state_layout("X783").wallpaper_count == 49
    assert p.state_layout("X776").wallpaper_count is None
    # Its library is read to the end of the reply instead, six bytes a picture.
    assert p.state_layout("X776").wallpaper_start == 41
    assert "wallpapers" in p.state_fields("X776")


def test_a_model_nobody_has_read_gets_no_screen_at_all():
    assert p.state_fields("X999") == frozenset()
    # ...but a charger the account API would not name is far more often the one
    # this was written on than a stranger.
    # Read as an X783, which has no rotating screen.
    assert p.state_fields(None) == p.STATE_FIELDS_ALL - {"auto_rotate"}


def test_reading_a_field_is_not_permission_to_write_it():
    # Brightness is one byte and its command carries one byte. The charging
    # mode's command carries the whole parameter block, which is a different
    # length on the 160W -- so it is shown there and not set.
    assert "charging_mode" in p.state_fields("X776")
    assert "charging_mode" not in p.state_writable("X776")
    assert "brightness" in p.state_writable("X776")
    # Everything but the custom block -- not because those bytes cannot be
    # written, they have been and were read back, but because nothing here
    # writes them, and a one-limit edit could leave a limit and a mask paired
    # the way the app never sends. It was in this table only because it rode
    # STATE_FIELDS_ALL.
    # And the MCU version, which is read to ask for firmware and changes only
    # by installing some -- see FIRMWARE_INSTALL_MODELS for that.
    assert p.state_writable("X783") == p.STATE_FIELDS_ALL - {
        "custom", "port_outputs", "mcu_version", "auto_rotate",
    }
    assert "custom" in p.state_fields("X783"), "read, though -- that is the point"
    assert p.state_writable("X999") == frozenset()


def test_a_model_whose_mode_may_be_set_has_had_its_layout_measured():
    """Otherwise the refusal in `rtcx` becomes the thing the user sees.

    Setting a charging mode sends that mode's parameter block, and how long the
    block is comes from the model's layout. `state_writable` is the gate people
    will reach for when a second model's writes are confirmed; if the layout is
    forgotten there, the entity agrees to the write and the client refuses it,
    in an untranslated sentence where every sibling guard has a proper message.
    """
    for model, fields in p.STATE_WRITABLE_BY_MODEL.items():
        if "charging_mode" in fields:
            assert p.state_layout_measured(model), model


def test_an_unmeasured_layout_is_the_x783_s_and_says_so():
    assert p.state_layout_measured("X783")
    assert p.state_layout_measured("X776")
    assert not p.state_layout_measured(None)
    assert not p.state_layout_measured("X999")
    # And what it hands back meanwhile is the X783's, which is what makes it
    # safe to read an unknown charger and unsafe to write to one.
    assert p.state_layout("X999") == p.state_layout("X783")

# --- the custom charging mode ----------------------------------------------
#
# The 35 bytes of whichever mode is running, read off a live X783 while its owner
# moved one slider at a time in the app. The frame below is that charger with
# a configuration the app calls "Laptop Prio".

CUSTOM_STATE = bytes.fromhex(
    "0037640004000f000f008c003c003c000000000100000001000000650000006500"
    "0000650000000001010033314632303706"
)

# A second X783, in custom mode, with its own profile: C1..C5 at 60, 140, 30,
# 20 and 15 W and C6+A at step 2. It is here for C4, whose mask is 0x0D while
# the app's Charging Protocol screen for that port listed exactly three names,
# all ticked: Apple5V/2.4A, AFC and SCP. The frame and the screen were read at
# the same moment, which is what makes bit 3 a measurement rather than the
# third entry in a list.
SECOND_X783_CUSTOM = bytes.fromhex(
    "0037640104003c008c001e0014000f02000000fd000000fd000000250000000d00000001"
    "000000250001013544374245430635443742454342313641353534394236363143343130"
    "3733464341344244314434353145"
)


def test_the_custom_block_reads_as_the_app_shows_it():
    groups = p.parse_custom_mode(CUSTOM_STATE, "X783")
    assert groups is not None
    assert [g["limit"] for g in groups] == [15, 15, 140, 60, 60, 0]
    assert [g["port"] for g in groups] == ["C1", "C2", "C3", "C4", "C5", "C6+A"]


def test_the_protocols_come_out_of_the_mask():
    groups = p.parse_custom_mode(CUSTOM_STATE, "X783")
    c3 = next(g for g in groups if g["port"] == "C3")
    assert c3["mask"] == 0x65
    assert c3["protocols"] == ["Apple5V/2.4A", "AFC", "5-11V PPS", "5-21V PPS"]


def test_bit_three_is_scp_because_the_app_said_so_beside_this_frame():
    """The name this frame settles: bit 3 is SCP.

    Bit 0 is Apple5V/2.4A on its own: C6+A at mask 0x01 showed that one name.
    At 0x25 it showed three -- Apple5V/2.4A, AFC and 5-11V PPS -- for bits 0, 2
    and 5. Here C4 is 0x0D, bits 0, 2 and 3, for Apple5V/2.4A, AFC and SCP.
    With bit 0 settled by 0x01, bit 2 is the only other bit in both masks and
    AFC the only other name on both screens, so bit 2 is AFC -- and the two
    remainders fall out: bit 5 is 5-11V PPS and bit 3 is SCP. Renaming bit 3
    fails here.
    """
    groups = p.parse_custom_mode(SECOND_X783_CUSTOM, "X783")
    c4 = next(g for g in groups if g["port"] == "C4")
    assert c4["limit"] == 20
    assert c4["mask"] == 0x0D
    assert c4["protocols"] == ["Apple5V/2.4A", "AFC", "SCP"]


def test_which_bits_carry_a_protocol_is_pinned_even_where_the_name_is_not():
    """The half of that table the frames actually decide.

    Which bits exist is measured -- 0xFD is every box the app offers. Which
    name sits on which is measured for 0, 2, 3 and 5, by reading the app's
    protocol screen beside the frame; 4, 6 and 7 rest on the order of the
    app's list. Without this, moving 4 or 7 to the never seen bit 1 -- a real
    edit, since nothing here sets it -- changed nothing in the suite; 6 is
    caught by the 0x65 assertion. The positions are the claim; those three
    names are still order in the app's list, and say so above.
    """
    assert sorted(p.CUSTOM_PROTOCOLS) == [0, 2, 3, 4, 5, 6, 7]


def test_the_custom_mode_byte_is_the_one_the_mode_table_names():
    """The literal `protocol.py` keeps rather than importing.

    Three test loaders compile that module, and one builds a package without
    `const` in it, so `from .const import CHARGING_MODES` cannot go there. The
    value is repeated instead, and only one direction of that was covered: the
    frames pin `protocol`'s side, so changing the literal fails tests whatever
    value it is changed to, while renumbering `CHARGING_MODES` broke none. This
    is the other direction.
    """
    assert const.CHARGING_MODES[p.CUSTOM_MODE] == "custom"
    assert "custom" not in const.SELECTABLE_MODES, "read-only, so never offered"


def test_the_priority_mode_byte_is_the_one_the_mode_table_names():
    """The same literal-against-table check, for the other mode read here."""
    assert const.CHARGING_MODES[p.PRIORITY_MODE] == "priority"


def _mode_body(mode: int, first: int) -> bytes:
    body = bytearray(CUSTOM_STATE)
    body[p.STATE_CHARGING_MODE] = mode
    body[p.STATE_MODE_PARAMS] = first
    return bytes(body)


def test_the_priority_mask_reads_as_the_app_shows_it():
    """Read off a live X783 while the choice in the app moved a step at a time.

    2, 4 and 5 were seen; C2 alone, C3 alone, C1 with C3. 1 and 7 follow from
    them, and 7 is the app's "all three" -- which it offers.
    """
    for mask, ports in (
        (0b001, ["C1"]),
        (0b010, ["C2"]),
        (0b100, ["C3"]),
        (0b101, ["C1", "C3"]),
        (0b111, ["C1", "C2", "C3"]),
    ):
        assert p.parse_priority(_mode_body(p.PRIORITY_MODE, mask), "X783") == ports


def test_the_mask_is_only_a_mask_under_priority():
    """Under `dc_turbo` the same byte is the DC port's voltage, 1 to 3.

    3 there is 20 V; read as ports it would be C1 and C2 first on a charger
    that has no priority mode running.
    """
    assert p.parse_priority(_mode_body(2, 3), "X783") is None
    assert p.parse_priority(_mode_body(p.CUSTOM_MODE, 0), "X783") is None
    assert p.parse_priority(CUSTOM_STATE, "X783") is None


def test_the_160w_reads_its_priority_ports_from_its_own_frames():
    """Its owner's changes in #2: C-Cable alone read 01, with C1 03.

    The frame with the outputs off was taken under priority, mode 2 here,
    with C1 chosen; the one with the new picture under Custom Power.
    """
    assert X776_OUTPUTS_OFF[p.STATE_CHARGING_MODE] == 2
    assert p.parse_priority(X776_OUTPUTS_OFF, "X776") == ["C1"]
    assert p.parse_priority(X776_NEW_PICTURE, "X776") is None, "custom, not priority"
    for mask, ports in ((0x01, ["C-Cable"]), (0x03, ["C-Cable", "C1"])):
        body = bytearray(X776_OUTPUTS_OFF)
        body[p.STATE_MODE_PARAMS] = mask
        assert p.parse_priority(bytes(body), "X776") == ports
    # Mode 3 is Custom Power on the 160W, not priority as on the 300W.
    body = bytearray(X776_OUTPUTS_OFF)
    body[p.STATE_CHARGING_MODE] = 3
    assert p.parse_priority(bytes(body), "X776") is None


def test_a_160w_bit_nobody_has_named_is_left_unread():
    body = bytearray(X776_OUTPUTS_OFF)
    body[p.STATE_MODE_PARAMS] = 0b0111
    assert p.parse_priority(bytes(body), "X776") == ["C-Cable", "C1"]


def test_the_160w_s_priority_is_read_and_not_set():
    assert "priority" in p.state_fields("X776")
    assert "priority" not in p.state_writable("X776")
    assert "priority" in p.state_writable("X783")
    assert p.priority_mask(["C-Cable", "C1"], "X776") == 0b11
    with pytest.raises(ValueError):
        p.priority_mask(["C2"], "X776")


def test_the_160w_s_screen_auto_rotate_is_byte_29():
    """Its owner's logged change: auto-rotate turned off read `byte 29 01>00`."""
    assert X776_OUTPUTS_OFF[29] == X776_NEW_PICTURE[29] == 1
    assert p.parse_auto_rotate(X776_OUTPUTS_OFF, "X776") is True
    body = bytearray(X776_OUTPUTS_OFF)
    body[29] = 0
    assert p.parse_auto_rotate(bytes(body), "X776") is False
    # Byte 33, once taken for it, says nothing about it.
    body = bytearray(X776_OUTPUTS_OFF)
    body[33] ^= 1
    assert p.parse_auto_rotate(bytes(body), "X776") is True
    # Nobody has seen byte 33 move, so it is not read; nor is there an
    # auto-rotate on the 300W.
    assert "screensaver_flag" not in p.state_fields("X776")
    assert "auto_rotate" not in p.state_fields("X783")
    assert p.parse_auto_rotate(bytes(60), "X783") is None
    assert p.parse_auto_rotate(bytes(60), None) is None


def test_a_body_that_stops_before_the_block_has_no_priority():
    assert p.parse_priority(bytes([0, 0x37, 100, 0, p.PRIORITY_MODE]), "X783") is None


def test_the_mask_is_the_ports_it_came_from():
    for ports in (["C1"], ["C2"], ["C3"], ["C1", "C3"], ["C1", "C2", "C3"]):
        body = _mode_body(p.PRIORITY_MODE, p.priority_mask(ports))
        assert p.parse_priority(body, "X783") == ports
    assert p.priority_mask([]) == 0
    assert p.priority_mask(["C3", "C3"]) == 0b100


def test_a_port_the_mode_does_not_offer_is_refused():
    with pytest.raises(ValueError):
        p.priority_mask(["C4"])


def test_the_dc_turbo_mode_byte_is_the_one_the_mode_table_names():
    assert const.CHARGING_MODES[p.DC_TURBO_MODE] == "dc_turbo"


def _turbo_body(volts_byte: int, always_on: int, mode: int = 2) -> bytes:
    body = bytearray(CUSTOM_STATE)
    body[p.STATE_CHARGING_MODE] = mode
    body[p.STATE_MODE_PARAMS] = volts_byte
    body[p.STATE_MODE_PARAMS + 1] = always_on
    return bytes(body)


def test_the_dc_turbo_block_reads_as_the_app_shows_it():
    """Read off a live X783 while one control at a time moved in the app."""
    for volts_byte, volts in ((1, 12), (2, 15), (3, 20)):
        for always_on in (0, 1):
            assert p.parse_dc_turbo(_turbo_body(volts_byte, always_on), "X783") == {
                "voltage": volts,
                "always_on": bool(always_on),
            }


def test_a_voltage_byte_the_app_never_sets_is_not_guessed_at():
    # 0 is the empty block the charger refuses; 4 is past the three the app offers.
    for odd in (0, 4):
        assert p.parse_dc_turbo(_turbo_body(odd, 1), "X783") == {
            "voltage": None,
            "always_on": True,
        }


def test_the_dc_turbo_bytes_are_only_its_own_under_dc_turbo():
    """Under `priority` the first byte is the port mask: C2 alone is 2."""
    assert p.parse_dc_turbo(_turbo_body(2, 0, mode=p.PRIORITY_MODE), "X783") is None
    assert p.parse_dc_turbo(CUSTOM_STATE, "X783") is None
    assert p.parse_priority(_turbo_body(3, 1), "X783") is None


def test_the_160w_has_no_dc_turbo_to_read():
    assert p.parse_dc_turbo(_turbo_body(3, 1), "X776") is None
    assert "dc_turbo" not in p.state_fields("X776")
    assert "dc_turbo" in p.state_writable("X783")


def test_a_body_that_stops_inside_the_dc_turbo_pair_has_none():
    assert p.parse_dc_turbo(bytes([0, 0x37, 100, 0, p.DC_TURBO_MODE, 3]), "X783") is None


def test_the_voltage_table_goes_both_ways():
    assert {p.DC_VOLTAGE_BYTE[v]: v for v in p.DC_VOLTAGE_BYTE} == p.DC_VOLTAGES


def test_a_preset_has_no_custom_mode_to_describe():
    """The mode byte decides it, not the contents.

    An all-zero body passes because byte 4 is 0, which is `adaptive_power`,
    not because the block is empty. A preset's own settings live in those same
    bytes: a second X783 in `priority` carries `02` there, which at this
    layout decodes as 512 W.
    """
    assert p.parse_custom_mode(bytes(60), "X783") is None

    # The same body with something in the block, still a preset: also nothing.
    body = bytearray(60)
    body[4] = 3
    body[5] = 0x02
    assert p.parse_custom_mode(bytes(body), "X783") is None


def test_another_model_is_not_measured_with_this_ruler():
    # Five wattages, a shared pair in steps and six masks is the X783's shape.
    # The 160W's block is 26 bytes where this shape needs 35.
    assert p.parse_custom_mode(CUSTOM_STATE, "X776") is None
    # Its own is a byte a slider -- C-Cable, C1, C2 & A -- never the X783's groups.
    body = bytearray(CUSTOM_STATE)
    body[p.STATE_CHARGING_MODE] = 3
    groups = p.parse_custom_mode(bytes(body), "X776")
    assert [g["port"] for g in groups] == ["C-Cable", "C1", "C2 & A"]
    assert all("mask" not in g for g in groups)


def test_a_preset_is_not_read_as_a_custom_configuration():
    """A second X783 in `priority` carries 02 at byte 5, and it is not a limit.

    The block was thought to be zero under a preset. It was under the preset
    this layout was worked out beside, so "not all zero" looked like a safe way
    to ask whether a custom mode exists -- and on that second charger, with C2
    as its priority port, byte 5 read `0200` as a 512 W limit for C1, whose
    slider in the app stops at 140 W.

    Built from the fixture rather than typed out, so the only differences from a
    frame known to parse are the two bytes under test.
    """
    body = bytearray(CUSTOM_STATE)
    body[4] = 3          # priority
    body[5] = 0x02       # what that charger actually carries
    for offset in range(6, p.STATE_CUSTOM_END):
        body[offset] = 0

    assert p.parse_custom_mode(bytes(body), "X783") is None

    # And the same bytes with custom active still decode, so the gate is the
    # mode and not the zeroing.
    body[4] = 4
    assert p.parse_custom_mode(bytes(body), "X783") is not None


def test_the_shared_slider_counts_steps_rather_than_watts():
    """Two frames off a charger, differing only in that slider.

    `body[15]` is 0 in every frame captured before these, so the multiplier was
    unobservable -- zero times anything is zero, and no mutation of the constant
    could be caught. Setting the shared group to 15 W and then 30 W gives the
    two readings that make it a scale.

    What this does not settle: the slider offers exactly 0, 15 and 30, so
    "steps of 15 W" and "an index into those three" produce identical bytes.
    The reachable range is pinned; the multiplication is still the reading that
    fits it, not one the charger has confirmed.
    """
    for frame, expected in ((SHARED_15, 15), (SHARED_30, 30)):
        body = p.frame_body(frame, p.FRAME_QUERY, p.QUERY_GET_DEVICE_STATE)
        assert body is not None, "the fixture has to survive its own CRC"
        groups = p.parse_custom_mode(body, "X783")
        shared = next(g for g in groups if g["port"] == "C6+A")
        assert shared["limit"] == expected


def test_the_shared_group_changes_its_protocols_with_its_limit():
    """The only other byte that moved between those two frames.

    Raising the shared limit from 15 W to 30 W also widened what that group may
    negotiate -- `0x01` to `0x25`. Recorded because it was not expected: the
    wattage and the protocol mask are separate fields in the layout, and the app
    writes both from one slider.
    """
    masks = {}
    for frame, label in ((SHARED_15, 15), (SHARED_30, 30)):
        body = p.frame_body(frame, p.FRAME_QUERY, p.QUERY_GET_DEVICE_STATE)
        groups = p.parse_custom_mode(body, "X783")
        masks[label] = next(g for g in groups if g["port"] == "C6+A")["protocols"]

    assert masks[15] == ["Apple5V/2.4A"]
    assert masks[30] == ["Apple5V/2.4A", "AFC", "5-11V PPS"]


def test_a_frame_that_stops_inside_the_block_is_not_decoded():
    """The length guard is the only thing holding the last read together.

    The final mask comes from `body[36:40]`. A body one byte short slices to
    three without raising, so the mask would be quietly wrong rather than
    absent, and this guard is the only thing preventing it. Its value was
    right and nothing held it: `STATE_CUSTOM_END` could be lowered to 39 and
    every other test stayed green.
    """
    def block(length: int) -> bytes:
        body = bytearray(length)
        body[4] = 4
        body[5:16] = bytes([0, 60, 0, 140, 0, 30, 0, 20, 0, 30, 1])
        return bytes(body)

    assert p.parse_custom_mode(block(39), "X783") is None
    # And exactly forty is enough, which pins the bound from the other side --
    # a value one too high refuses a frame that carries the whole block.
    assert p.parse_custom_mode(block(40), "X783") is not None



# --- what the 160W's owner sent in #2 --------------------------------------
#
# Two state bodies off a 160W, as the integration logged them: the first after
# turning off the built-in cable, C1 and A in the app; the second after putting
# a new picture on it, in Custom Power.
X776_OUTPUTS_OFF = bytes.fromhex(
    "000408010202000000efffff65000000efffff65000000efffff6100000100010101"
    "30383031324505334537463832454131413342464331433737303830313245"
)
X776_NEW_PICTURE = bytes.fromhex(
    "00040805030f0f1701efffff65000001efffff65000001efffff6100000100010001"
    "34333845463205334537463832454131413342464331433737343338454632"
)


def test_the_160w_numbers_its_modes_without_dc_turbo():
    """Its app lists four presets, no DC turbo; the owner had it in Priority
    Charging for the first frame and in Custom Power for the second."""
    modes = const.charging_modes("X776")
    assert modes[X776_OUTPUTS_OFF[p.STATE_CHARGING_MODE]] == "priority"
    assert modes[X776_NEW_PICTURE[p.STATE_CHARGING_MODE]] == "custom"
    assert "dc_turbo" not in modes.values()
    assert const.selectable_modes("X776") == ("adaptive_power", "thermal_safe", "priority")
    # The 300W's table stays what it was, and is the answer for a stranger.
    assert const.charging_modes("X783") is const.CHARGING_MODES
    assert const.charging_modes(None) is const.CHARGING_MODES


def test_custom_is_the_only_mode_no_model_offers():
    """The refusal names `custom` outright, so it has to be the only one."""
    for model in (None, "X783", "X776"):
        assert set(const.charging_modes(model).values()) - set(const.selectable_modes(model)) == {
            "custom"
        }


def test_the_160w_s_port_switches_are_three_bytes():
    assert p.parse_port_outputs(X776_OUTPUTS_OFF, "X776") == {
        "C-Cable": False, "C1": False, "C2 & A": False,
    }
    assert p.parse_port_outputs(X776_NEW_PICTURE, "X776") == {
        "C-Cable": True, "C1": True, "C2 & A": True,
    }
    names = [(name, ports) for name, ports, _ in p.port_output_groups("X776")]
    assert names == [("C-Cable", ("C-Cable",)), ("C1", ("C1",)), ("C2 & A", ("C2", "A"))]


def test_a_charger_without_port_switches_has_none_to_read():
    assert p.parse_port_outputs(X776_NEW_PICTURE, "X783") is None
    assert p.parse_port_outputs(X776_NEW_PICTURE, None) is None
    assert p.parse_port_outputs(X776_NEW_PICTURE[:22], "X776") is None
    assert "port_outputs" not in p.state_fields("X783")
    assert "port_outputs" not in p.state_writable("X776")


def test_the_160w_s_custom_power_is_a_byte_a_port():
    """Its owner's lines in #2, one slider at a time with Custom Power running.

    70 and 45 read 46 and 2d at bytes 5 and 6; C1 moved to 30 read 1e; the
    third slider, C2 & A, moved to 45 read 2d at byte 7.
    """
    assert X776_NEW_PICTURE[p.STATE_CHARGING_MODE] == 3, "Custom Power on the 160W"
    assert p.parse_custom_mode(X776_NEW_PICTURE, "X776") == [
        {"port": "C-Cable", "limit": 15},
        {"port": "C1", "limit": 15},
        {"port": "C2 & A", "limit": 23},
    ]
    body = bytearray(X776_NEW_PICTURE)
    body[5], body[6] = 0x46, 0x2D
    assert [g["limit"] for g in p.parse_custom_mode(bytes(body), "X776")] == [70, 45, 23]
    body[6] = 0x1E
    assert [g["limit"] for g in p.parse_custom_mode(bytes(body), "X776")] == [70, 30, 23]
    body[7] = 0x2D
    assert [g["limit"] for g in p.parse_custom_mode(bytes(body), "X776")] == [70, 30, 45]
    # Under priority those bytes are the priority block, not limits.
    assert p.parse_custom_mode(X776_OUTPUTS_OFF, "X776") is None
    # Mode 4 is the 300W's custom; on the 160W it is not a mode at all.
    body[p.STATE_CHARGING_MODE] = 4
    assert p.parse_custom_mode(bytes(body), "X776") is None


def test_the_160w_s_custom_limits_are_read_and_not_set():
    assert "custom" in p.state_fields("X776")
    assert "custom" not in p.state_writable("X776")


def test_the_160w_keeps_its_clock_style_where_the_300w_keeps_its_hours():
    """Changing only the clock style moved byte 32; only the hours moved nothing."""
    assert p.clock_style_field("X776") == "screensaver_theme"
    assert p.time_format_field("X776") is None
    assert p.clock_style_field("X783") == "screensaver_flag"
    assert p.time_format_field("X783") == "screensaver_theme"
    # A charger nobody could name is read as the X783 it most likely is.
    assert p.clock_style_field(None) == "screensaver_flag"
    assert p.time_format_field(None) == "screensaver_theme"

