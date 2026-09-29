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
