"""How a power report is read, on the two chargers anyone has measured.

Both frames below came off hardware and carry their own CRC, so they are the
bytes the device sent rather than a transcription of them. They are here
because the port count is the one thing in this integration that cannot be
checked by reasoning: it depends on a report's length, and the two models
disagree about what a report of a given length contains.
"""

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
    assert p.state_layout("X783").wallpaper_count == 49
    assert p.state_layout("X776").wallpaper_count is None
    assert "wallpapers" not in p.state_fields("X776")


def test_a_model_nobody_has_read_gets_no_screen_at_all():
    assert p.state_fields("X999") == frozenset()
    # ...but a charger the account API would not name is far more often the one
    # this was written on than a stranger.
    assert p.state_fields(None) == p.STATE_FIELDS_ALL


def test_reading_a_field_is_not_permission_to_write_it():
    # Brightness is one byte and its command carries one byte. The charging
    # mode's command carries the whole parameter block, which is a different
    # length on the 160W -- so it is shown there and not set.
    assert "charging_mode" in p.state_fields("X776")
    assert "charging_mode" not in p.state_writable("X776")
    assert "brightness" in p.state_writable("X776")
    assert p.state_writable("X783") == p.STATE_FIELDS_ALL
    assert p.state_writable("X999") == frozenset()
