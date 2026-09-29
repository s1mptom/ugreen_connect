"""Installing firmware the way the UGREEN app does it.

Watched on 28 September 2026, the app taking an X783 from 1.2.1 to 1.2.3: it
asked the account API what to install, handed the charger the file in one
property write, then asked GET_UPGRADE_STATUS about once a second until the
charger said it was done. What it sent is written down below as it went out,
and the client has to send the same.
"""

import asyncio
import json

import pytest
from conftest import protocol as p
from conftest import rtcx as rtcx_module

# The params of the app's write, as captured, with the charger's id and the
# signed link swapped for stand-ins.
APP_COMMAND = (
    '{"iotId":"IOT","items":{"OTA_upgrade":{"size":307704.0,'
    '"md5sum":"da5c01c2fa8e690e28bd3a1bcfba6094","version":58.0,"url":"URL"}}}'
)

# An X783's GET_DEVICE_STATE reply at 1.2.1: the body opens 00 37.
X783_STATE = (
    "aa0100560037640103020000000000000000000000000000000000000000000000000000"
    "000000000000000000010135443742454306354437424543423136413535343942363631"
    "433431303733464341344244314434353145e8ec"
)


def test_the_upgrade_status_is_a_status_then_a_percentage():
    assert p.parse_upgrade_status(bytes([p.UPGRADE_RUNNING, 40])) == (1, 40)
    assert p.parse_upgrade_status(bytes([p.UPGRADE_DONE, 100])) == (2, 100)
    assert p.parse_upgrade_status(b"\x01") is None
    # A percentage past 100 is not one; the bar stops at full.
    assert p.parse_upgrade_status(bytes([1, 250])) == (1, 100)


def test_the_status_names_are_the_apps():
    assert (p.UPGRADE_IDLE, p.UPGRADE_RUNNING, p.UPGRADE_DONE, p.UPGRADE_FAILED) == (0, 1, 2, 3)


def test_its_frames_may_go_in_a_diagnostics_download():
    # Two bytes of progress; nothing of the household's in them.
    assert f"{p.FRAME_QUERY:02X}/{p.QUERY_GET_UPGRADE_STATUS}" in p.PUBLISHABLE_FRAMES


def test_only_the_model_it_was_watched_on_installs():
    assert frozenset({"X783"}) == p.FIRMWARE_INSTALL_MODELS
    assert "mcu_version" not in p.state_writable("X783")
    assert "mcu_version" in p.state_fields("X783")
    assert "mcu_version" in p.state_fields("X776")


pytestmark_rtcx = pytest.mark.skipif(
    rtcx_module is None, reason="rtcx needs aiohttp, which is not installed here"
)


class _Api:
    user_id = "someone"


@pytestmark_rtcx
def test_the_mcu_version_is_the_state_replys_first_two_bytes():
    client = rtcx_module.RtcxClient(None, _Api())

    async def _ask(*_args, **_kwargs):
        return X783_STATE

    client._ask = _ask
    state = asyncio.run(client.async_device_state("IOT", "X783"))
    assert state["mcu_version"] == 55


@pytestmark_rtcx
def test_the_command_is_the_apps_to_the_byte():
    client = rtcx_module.RtcxClient(None, _Api())
    sent = []

    async def _call(path, params):
        sent.append((path, json.dumps(params, separators=(",", ":"))))
        return {"code": 200}

    client.call = _call
    asyncio.run(
        client.async_start_firmware_update(
            "IOT", url="URL", size=307704, md5="da5c01c2fa8e690e28bd3a1bcfba6094", version=58
        )
    )
    assert sent == [("/client/thing/properties/set", APP_COMMAND)]
    # And the state is re-read after, since the charger restarts into it.
    assert client.state_is_stale("IOT")


@pytestmark_rtcx
def test_the_command_waits_for_a_question_in_flight():
    """One conversation with the charger at a time, this included."""
    client = rtcx_module.RtcxClient(None, _Api())
    order = []

    async def _call(path, params):
        order.append("firmware" if "OTA_upgrade" in params.get("items", {}) else "other")
        return {"code": 200}

    client.call = _call

    async def scenario():
        async with client._talk:
            task = asyncio.create_task(
                client.async_start_firmware_update("IOT", url="U", size=1, md5="m", version=2)
            )
            await asyncio.sleep(0)
            order.append("question answered")
        await task

    asyncio.run(scenario())
    assert order == ["question answered", "firmware"]


@pytestmark_rtcx
def test_progress_is_read_from_the_charger():
    client = rtcx_module.RtcxClient(None, _Api())
    asked = []

    async def _ask(iot_id, frame_type, cmd, payload=b"\x00"):
        asked.append((frame_type, cmd))
        body = bytes([1, 96])
        head = bytes((frame_type, cmd)) + len(body).to_bytes(2, "big") + body
        return (head + p.crc16_modbus(head).to_bytes(2, "little")).hex()

    client._ask = _ask
    assert asyncio.run(client.async_upgrade_status("IOT")) == (1, 96)
    assert asked == [(p.FRAME_QUERY, p.QUERY_GET_UPGRADE_STATUS)]
    # The frame the app sent, word for word.
    assert p.build_frame(p.FRAME_QUERY, p.QUERY_GET_UPGRADE_STATUS) == "AA070001003CFC"
