"""The firmware update entity: what is offered, and installing it.

The account API is asked what to install with the MCU version the charger
reports, as the UGREEN app asks it. Installing takes a fresh offer -- its link
is signed and short-lived -- sends it to the charger, and follows the
charger's own progress until it names an outcome.
"""

from __future__ import annotations

import json

import pytest
from homeassistant.components.update import UpdateEntityFeature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.ugreen_connect.api import UgreenError
from custom_components.ugreen_connect.const import DOMAIN
from tests_ha.conftest import (
    DEVICE_CODE,
    FIRMWARE_OFFER,
    IOT_ID,
    SECOND,
    SECOND_CODE,
)
from tests_ha.test_chargers import _set_up

FIRMWARE = "update.ugreen_nexode_pro_x783_firmware"


def _entity(hass, domain: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id is not None, unique_id
    return entity_id


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The charger here answers at once; the pause between questions is not tested.

    The overall limit is cut too, so a loop that never ends fails in seconds
    rather than holding the suite for ten minutes.
    """
    module = "custom_components.ugreen_connect.coordinator"
    monkeypatch.setattr(f"{module}.FIRMWARE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{module}.FIRMWARE_INSTALL_TIMEOUT", 2)


@pytest.fixture
def offered(api):
    api.firmware["030002"] = dict(FIRMWARE_OFFER)
    return api


async def _install(hass, **data):
    await hass.services.async_call(
        "update", "install", {"entity_id": FIRMWARE, **data}, blocking=True
    )


async def test_an_offer_is_shown_with_its_notes(hass, offered, started):
    state = hass.states.get(FIRMWARE)
    assert state.state == "on"
    assert state.attributes["installed_version"] == "1.2.1"
    # "V1.2.3" in the answer; the charger's own reading has no V.
    assert state.attributes["latest_version"] == "1.2.3"
    assert state.attributes["release_summary"] == "1. Fixed some known issues"
    features = UpdateEntityFeature(state.attributes["supported_features"])
    assert features & UpdateEntityFeature.INSTALL
    assert features & UpdateEntityFeature.PROGRESS
    # Asked about the MCU version, not the version's name.
    assert offered.firmware_checks[0] == ("030002", 55)


async def test_the_signed_link_is_kept_nowhere(hass, offered, started):
    data = json.dumps(started.runtime_data.data, default=str)
    assert "auth_key" not in data
    assert "fileUrl" not in data
    assert "auth_key" not in json.dumps(dict(hass.states.get(FIRMWARE).attributes))


async def test_no_offer_means_current(hass, started):
    state = hass.states.get(FIRMWARE)
    assert state.state == "off"
    assert state.attributes["latest_version"] == "1.2.1"


async def test_the_cloud_is_asked_rarely(hass, offered, started):
    before = len(offered.firmware_checks)
    await started.runtime_data.async_refresh()
    await started.runtime_data.async_refresh()
    assert len(offered.firmware_checks) == before


async def test_installing_sends_a_fresh_offer_and_follows_it_to_the_end(
    hass, offered, started, rtcx
):
    checks = len(offered.firmware_checks)
    await _install(hass)
    await hass.async_block_till_done()

    # Asked again at install time, for a link that has not expired.
    assert len(offered.firmware_checks) > checks
    assert rtcx.firmware_sent == [
        {
            "iot_id": IOT_ID,
            "url": FIRMWARE_OFFER["fileUrl"],
            "size": 307704,
            "md5": "da5c01c2fa8e690e28bd3a1bcfba6094",
            "version": 58,
        }
    ]
    # The charger came back at 58, which the cloud has nothing newer than.
    assert offered.firmware_checks[-1] == ("030002", 58)
    state = hass.states.get(FIRMWARE)
    assert state.state == "off"
    assert state.attributes["in_progress"] is False


async def test_nothing_else_is_asked_of_the_charger_while_it_installs(
    hass, offered, started, rtcx
):
    coordinator = started.runtime_data
    total = _entity(hass, "sensor", f"{DEVICE_CODE}_total_power")
    seen: dict[str, object] = {}
    answers = iter([(1, 40), (1, 99), (2, 100)])

    async def _status(iot_id):
        rtcx.talk.append((iot_id, "upgrade_status"))
        answer = next(answers)
        if answer == (1, 99):
            # A poll lands mid-install.
            await coordinator.async_refresh()
            seen["total"] = hass.states.get(total).state
            seen["firmware"] = dict(hass.states.get(FIRMWARE).attributes)
        if answer[0] == 2:
            rtcx.state = {**rtcx.state, "mcu_version": 58}
        return answer

    rtcx.async_upgrade_status = _status
    await _install(hass)

    sent = rtcx.talk.index((IOT_ID, "firmware"))
    during = [what for iot_id, what in rtcx.talk[sent + 1 :] if iot_id == IOT_ID]
    last_status = max(i for i, what in enumerate(during) if what == "upgrade_status")
    assert set(during[: last_status + 1]) == {"upgrade_status"}, during
    # Nothing measured is nothing shown, rather than the last watts again.
    assert seen["total"] == "unknown"
    assert seen["firmware"]["in_progress"] is True
    assert seen["firmware"]["update_percentage"] == 40


async def test_a_failure_the_charger_reports_is_an_error(hass, offered, started, rtcx):
    rtcx.upgrade_answers = [(1, 30), (3, 30)]
    with pytest.raises(HomeAssistantError) as err:
        await _install(hass)
    assert err.value.translation_key == "firmware_failed"
    assert not started.runtime_data.installs
    assert hass.states.get(FIRMWARE).attributes["in_progress"] is False


async def test_a_lost_reply_is_waited_through(hass, offered, started, rtcx):
    rtcx.upgrade_answers = [UgreenError("no reply"), None, (1, 50), (2, 100)]
    await _install(hass)
    assert rtcx.state["mcu_version"] == 58


async def test_a_missed_done_is_read_off_the_version(hass, offered, started, rtcx):
    """Back to "not upgrading" after running, and the charger is at 58: done."""
    answers = iter([(1, 50), (1, 99), (0, 0)])

    async def _status(iot_id):
        answer = next(answers)
        if answer == (0, 0):
            rtcx.state = {**rtcx.state, "mcu_version": 58}
        return answer

    rtcx.async_upgrade_status = _status
    await _install(hass)


async def test_a_charger_that_never_starts_is_reported(
    hass, offered, started, rtcx, monkeypatch
):
    monkeypatch.setattr(
        "custom_components.ugreen_connect.coordinator.FIRMWARE_START_TIMEOUT", 0
    )
    rtcx.upgrade_answers = [(0, 0)]
    with pytest.raises(HomeAssistantError) as err:
        await _install(hass)
    assert err.value.translation_key == "firmware_unconfirmed"


async def test_an_offer_withdrawn_meanwhile_installs_nothing(hass, offered, started, rtcx):
    offered.firmware.clear()
    with pytest.raises(HomeAssistantError) as err:
        await _install(hass)
    assert err.value.translation_key == "firmware_current"
    assert rtcx.firmware_sent == []


async def test_the_version_already_running_is_not_an_offer(hass, api, rtcx, started):
    """Should the cloud offer what the charger already runs, it is current."""
    async def _same(product_serial, version_code):
        return {**FIRMWARE_OFFER, "versionCode": version_code}

    api.check_firmware = _same
    started.runtime_data._offers.clear()
    await started.runtime_data.async_refresh()
    assert hass.states.get(FIRMWARE).state == "off"


async def test_an_offer_without_a_link_installs_nothing(hass, offered, started, rtcx):
    offered.firmware["030002"].pop("fileUrl")
    with pytest.raises(HomeAssistantError) as err:
        await _install(hass)
    assert err.value.translation_key == "firmware_check_failed"
    assert rtcx.firmware_sent == []


async def test_another_version_is_refused(hass, offered, started, rtcx):
    with pytest.raises(HomeAssistantError):
        await _install(hass, version="9.9.9")
    assert rtcx.firmware_sent == []


async def test_the_160w_is_told_of_firmware_and_not_given_the_button(hass, api, rtcx):
    api.devices.append(dict(SECOND))
    api.firmware["030007"] = {**FIRMWARE_OFFER, "softwareSerialNo": "030007", "versionCode": 5}
    await _set_up(hass, api, rtcx)
    state = hass.states.get(_entity(hass, "update", f"{SECOND_CODE}_firmware"))
    assert state.state == "on"
    features = UpdateEntityFeature(state.attributes["supported_features"])
    assert not features & UpdateEntityFeature.INSTALL
    assert ("030007", 4) in api.firmware_checks


# What review found: each of these went wrong before it was fixed.


async def test_two_installs_at_once_send_the_charger_one(hass, offered, started, rtcx):
    """Two tabs, or the card and an automation, pressing within a second."""
    import asyncio

    ask = offered.check_firmware

    async def _slow(*args):
        # The real call is a round trip; the second press lands inside it.
        await asyncio.sleep(0)
        return await ask(*args)

    offered.check_firmware = _slow
    results = await asyncio.gather(_install(hass), _install(hass), return_exceptions=True)
    assert len(rtcx.firmware_sent) == 1
    assert sum(isinstance(r, HomeAssistantError) for r in results) == 1
    assert not started.runtime_data.installs


async def test_a_second_install_is_refused_by_the_coordinator_too(hass, offered, started, rtcx):
    """Home Assistant's own check reads in_progress; this holds without it."""
    from tests_ha.conftest import DEVICE

    coordinator = started.runtime_data
    coordinator.installs[DEVICE_CODE] = {"status": 1, "progress": 10}
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_install_firmware(DEVICE_CODE, dict(DEVICE))
    assert err.value.translation_key == "firmware_installing"
    assert rtcx.firmware_sent == []
    coordinator.installs.clear()


async def test_an_odd_answer_from_the_cloud_costs_its_line_not_the_poll(hass, api, rtcx):
    api.firmware["030002"] = {**FIRMWARE_OFFER, "changeList": ["a", "list"], "versionName": 123}
    from tests_ha.test_chargers import _set_up as set_up

    entry = await set_up(hass, api, rtcx)
    assert entry.runtime_data.last_update_success
    state = hass.states.get(FIRMWARE)
    assert state.state == "on"
    # Not a string, so not a name: the MCU version stands in.
    assert state.attributes["latest_version"] == "58"
    assert state.attributes["release_summary"] is None


async def test_a_failing_check_is_not_asked_every_poll(hass, api, rtcx):
    async def _fails(*_args):
        api.firmware_checks.append(_args)
        raise UgreenError("check_upgrade: code 500")

    api.check_firmware = _fails
    from tests_ha.test_chargers import _set_up as set_up

    entry = await set_up(hass, api, rtcx)
    for _ in range(3):
        await entry.runtime_data.async_refresh()
    assert len(api.firmware_checks) == 1
    assert hass.states.get(FIRMWARE).state == "off"


async def test_the_version_is_kept_when_the_restarted_charger_does_not_say_it(
    hass, offered, started, rtcx
):
    async def _silent(_iot_id):
        return None

    # Silent from before the install, so every read after it goes unanswered.
    rtcx.async_firmware_version = _silent
    await _install(hass)
    await started.runtime_data.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(FIRMWARE)
    assert state.state != "unavailable"
    assert state.attributes["installed_version"] == "1.2.1"


async def test_a_command_that_errored_is_still_followed(hass, offered, started, rtcx):
    """A timed-out request may well have arrived; the charger's answer decides."""
    sent = rtcx.async_start_firmware_update

    async def _times_out(iot_id, **kw):
        await sent(iot_id, **kw)
        raise UgreenError("/client/thing/properties/set: timed out")

    rtcx.async_start_firmware_update = _times_out
    await _install(hass)
    assert rtcx.state["mcu_version"] == 58


async def test_any_error_in_a_progress_question_is_waited_through(hass, offered, started, rtcx):
    rtcx.upgrade_answers = [ValueError("not JSON"), (1, 50), (2, 100)]
    await _install(hass)
    assert rtcx.state["mcu_version"] == 58


async def test_silence_after_starting_is_settled_by_the_version(
    hass, offered, started, rtcx, monkeypatch
):
    """The new firmware may not answer the progress question at once.

    Settled by reading the version, not by waiting out the limit -- which is
    set long here so that waiting would show.
    """
    import time

    monkeypatch.setattr(
        "custom_components.ugreen_connect.coordinator.FIRMWARE_INSTALL_TIMEOUT", 30
    )
    answers = iter([(1, 50), (1, 99)])

    async def _status(_iot_id):
        answer = next(answers, None)
        if answer is None:
            rtcx.state = {**rtcx.state, "mcu_version": 58}
        return answer

    rtcx.async_upgrade_status = _status
    began = time.monotonic()
    await _install(hass)
    assert time.monotonic() - began < 10


async def test_a_done_left_from_before_is_not_believed(hass, offered, started, rtcx, monkeypatch):
    monkeypatch.setattr(
        "custom_components.ugreen_connect.coordinator.FIRMWARE_START_TIMEOUT", 0.05
    )

    async def _stale(_iot_id):
        return (2, 100)

    rtcx.async_upgrade_status = _stale
    with pytest.raises(HomeAssistantError) as err:
        await _install(hass)
    assert err.value.translation_key == "firmware_unconfirmed"


async def test_a_failure_left_from_before_does_not_stop_a_new_install(
    hass, offered, started, rtcx
):
    rtcx.upgrade_answers = [(3, 0), (1, 20), (1, 90), (2, 100)]
    await _install(hass)
    assert rtcx.state["mcu_version"] == 58


async def test_an_entry_reloaded_mid_install_leaves_the_charger_alone(
    hass, offered, started, rtcx
):
    coordinator = started.runtime_data
    coordinator.installs[DEVICE_CODE] = {"status": 1, "progress": 30}
    try:
        from unittest.mock import patch

        with (
            patch("custom_components.ugreen_connect.async_get_clientsession"),
            patch("custom_components.ugreen_connect.UgreenApi", return_value=offered),
            patch("custom_components.ugreen_connect.RtcxClient", return_value=rtcx),
        ):
            assert await hass.config_entries.async_reload(started.entry_id)
            await hass.async_block_till_done()
        polled = len(rtcx.polled)
        await started.runtime_data.async_refresh()
        assert len(rtcx.polled) == polled, "the reloaded entry polled a charger mid-install"
        # With no reading to show it is unavailable until the install ends --
        # which also keeps Install from being offered over it.
        state = hass.states.get(FIRMWARE)
        assert state.state == "unavailable" or state.attributes["in_progress"] is True
    finally:
        coordinator.installs.clear()
