"""Choosing which of the account's chargers to add, and a 160W beside a 300W.

The account API names every charger on the account; which of them Home
Assistant shows is the owner's choice, made in the setup form and changed
under Configure. Each is read as its own model all the same.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ugreen_connect import async_remove_config_entry_device
from custom_components.ugreen_connect.const import DOMAIN
from tests_ha.conftest import DEVICE, DEVICE_CODE, IOT_ID, SECOND, SECOND_CODE, SECOND_IOT_ID

ACCOUNT = {"email": "someone@example.invalid", "password": "not a real one", "region": "europe"}


@contextmanager
def _clouds(api, rtcx):
    """Both clouds replaced, for the flow and for the entry it sets up."""
    with (
        patch("custom_components.ugreen_connect.async_get_clientsession"),
        patch("custom_components.ugreen_connect.UgreenApi", return_value=api),
        patch("custom_components.ugreen_connect.RtcxClient", return_value=rtcx),
        patch("custom_components.ugreen_connect.config_flow.async_get_clientsession"),
        patch("custom_components.ugreen_connect.config_flow.UgreenApi", return_value=api),
    ):
        yield


async def _set_up(hass, api, rtcx, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="an account", data=ACCOUNT, options=options or {})
    entry.add_to_hass(hass)
    with _clouds(api, rtcx):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _devices(hass, entry) -> set[str]:
    return {
        key
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        for domain, key in device.identifiers
        if domain == DOMAIN
    }


async def _log_in(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    return await hass.config_entries.flow.async_configure(result["flow_id"], dict(ACCOUNT))


async def test_two_chargers_are_offered_and_only_the_ticked_one_is_added(hass, api, rtcx):
    api.devices = [dict(DEVICE), dict(SECOND)]
    with _clouds(api, rtcx):
        result = await _log_in(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "chargers"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"chargers": [SECOND_CODE]}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.options["chargers"] == [SECOND_CODE]
    assert entry.options["offered_chargers"] == [DEVICE_CODE, SECOND_CODE]
    assert _devices(hass, entry) == {SECOND_CODE}
    assert IOT_ID not in rtcx.polled, "the charger left out was asked for a reading"


async def test_both_can_be_added(hass, api, rtcx):
    api.devices = [dict(DEVICE), dict(SECOND)]
    with _clouds(api, rtcx):
        result = await _log_in(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"chargers": [DEVICE_CODE, SECOND_CODE]}
        )
        await hass.async_block_till_done()
    assert _devices(hass, result["result"]) == {DEVICE_CODE, SECOND_CODE}


async def test_one_charger_is_added_without_asking(hass, api, rtcx):
    with _clouds(api, rtcx):
        result = await _log_in(hass)
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].options["chargers"] == [DEVICE_CODE]


async def test_none_ticked_is_refused(hass, api, rtcx):
    api.devices = [dict(DEVICE), dict(SECOND)]
    with _clouds(api, rtcx):
        result = await _log_in(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"chargers": []}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_charger"}


async def test_an_entry_from_before_the_choice_shows_every_charger(hass, api, rtcx):
    """No choice stored means every charger, as it always did."""
    api.devices = [dict(DEVICE), dict(SECOND)]
    entry = await _set_up(hass, api, rtcx)
    assert _devices(hass, entry) == {DEVICE_CODE, SECOND_CODE}


async def test_a_charger_bound_later_is_announced_and_not_added(hass, api, rtcx):
    api.devices = [dict(DEVICE), dict(SECOND)]
    entry = await _set_up(
        hass, api, rtcx, {"chargers": [DEVICE_CODE], "offered_chargers": [DEVICE_CODE]}
    )
    assert _devices(hass, entry) == {DEVICE_CODE}
    assert SECOND_IOT_ID not in rtcx.polled
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"new_charger_{entry.entry_id}")
    assert issue is not None
    assert issue.translation_placeholders == {"names": "UGREEN Nexode Pro X776"}


async def test_a_charger_left_out_on_purpose_is_not_announced(hass, api, rtcx):
    api.devices = [dict(DEVICE), dict(SECOND)]
    entry = await _set_up(
        hass,
        api,
        rtcx,
        {"chargers": [DEVICE_CODE], "offered_chargers": [DEVICE_CODE, SECOND_CODE]},
    )
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"new_charger_{entry.entry_id}") is None


def _options_input(**extra):
    return {
        "scan_interval": 5,
        "region": "europe",
        "nominal_voltage": 3.85,
        "efficiency": 90,
        "session_idle_end": 120,
        "debug_dump": False,
        **extra,
    }


async def test_unticking_a_charger_under_configure_removes_it(hass, api, rtcx):
    api.devices = [dict(DEVICE), dict(SECOND)]
    entry = await _set_up(hass, api, rtcx)
    assert _devices(hass, entry) == {DEVICE_CODE, SECOND_CODE}

    with _clouds(api, rtcx):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert "chargers" in result["data_schema"].schema
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _options_input(chargers=[DEVICE_CODE])
        )
        await hass.async_block_till_done()

    assert entry.options["chargers"] == [DEVICE_CODE]
    assert _devices(hass, entry) == {DEVICE_CODE}
    registry = er.async_get(hass)
    assert not [
        e for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.unique_id.startswith(SECOND_CODE)
    ], "its entities went with the device"


async def test_configure_refuses_to_untick_them_all(hass, api, rtcx):
    entry = await _set_up(hass, api, rtcx)
    with _clouds(api, rtcx):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _options_input(chargers=[])
        )
    assert result["errors"] == {"base": "no_charger"}


async def test_only_a_charger_gone_from_the_account_can_be_deleted(hass, api, rtcx):
    """One still on it would come back on the next poll."""
    api.devices = [dict(DEVICE), dict(SECOND)]
    entry = await _set_up(hass, api, rtcx)
    registry = dr.async_get(hass)
    second = registry.async_get_device_by_identifier((DOMAIN, SECOND_CODE), entry.entry_id)
    assert not await async_remove_config_entry_device(hass, entry, second)

    api.devices = [dict(DEVICE)]
    await entry.runtime_data.async_refresh()
    assert await async_remove_config_entry_device(hass, entry, second)


def _entity(hass, domain: str, unique_id: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)


async def test_the_160w_beside_a_300w_is_read_as_a_160w(hass, api, rtcx):
    """Its own presets, its port switches, its picture library."""
    api.devices = [dict(DEVICE), dict(SECOND)]
    await _set_up(hass, api, rtcx)

    mode = hass.states.get(_entity(hass, "select", f"{SECOND_CODE}_charging_mode"))
    assert mode.state == "custom"
    assert mode.attributes["options"] == ["adaptive_power", "thermal_safe", "priority", "custom"]
    # The 300W beside it keeps its own.
    other = hass.states.get(_entity(hass, "select", f"{DEVICE_CODE}_charging_mode"))
    assert "dc_turbo" in other.attributes["options"]

    outputs = {
        name: hass.states.get(_entity(hass, "switch", f"{SECOND_CODE}_{name}_output"))
        for name in ("C-Cable", "C1", "C2 & A")
    }
    assert {name: state.state for name, state in outputs.items()} == {
        "C-Cable": "on", "C1": "on", "C2 & A": "off",
    }
    assert outputs["C2 & A"].attributes["ports"] == ["C2", "A"]
    assert _entity(hass, "switch", f"{DEVICE_CODE}_C1_output") is None, "the 300W has none"

    assert _entity(hass, "select", f"{SECOND_CODE}_wallpaper") is not None


async def test_the_160w_s_port_switches_say_where_they_are_set(hass, api, rtcx):
    api.devices = [dict(DEVICE), dict(SECOND)]
    await _set_up(hass, api, rtcx)
    switch = _entity(hass, "switch", f"{SECOND_CODE}_C1_output")
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": switch}, blocking=True
        )
    assert err.value.translation_key == "port_output_app_only"


async def test_nothing_can_be_deleted_while_the_entry_is_not_loaded(hass, api, rtcx):
    """Which chargers the account has is then not known."""
    api.devices = [dict(DEVICE), dict(SECOND)]
    entry = await _set_up(hass, api, rtcx)
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier((DOMAIN, SECOND_CODE), entry.entry_id)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not await async_remove_config_entry_device(hass, entry, device)


async def test_the_new_charger_notice_goes_with_the_entry(hass, api, rtcx):
    api.devices = [dict(DEVICE), dict(SECOND)]
    entry = await _set_up(
        hass, api, rtcx, {"chargers": [DEVICE_CODE], "offered_chargers": [DEVICE_CODE]}
    )
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, f"new_charger_{entry.entry_id}") is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert issues.async_get_issue(DOMAIN, f"new_charger_{entry.entry_id}") is None


async def test_a_picture_is_not_put_on_a_160w_s_screen(hass, api, rtcx):
    """Its screensaver group is read and not written yet."""
    api.devices = [dict(DEVICE), dict(SECOND)]
    entry = await _set_up(hass, api, rtcx)
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier((DOMAIN, SECOND_CODE), entry.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "set_wallpaper",
            {"device_id": device.id, "image": "data:image/jpeg;base64,/9j/"},
            blocking=True,
        )
    assert err.value.translation_key == "not_writable_on_model"


async def test_a_port_s_power_sensor_names_its_port(hass, api, rtcx):
    """The cards read it, since the entity's own name is translated."""
    api.devices = [dict(DEVICE), dict(SECOND)]
    await _set_up(hass, api, rtcx)
    power = hass.states.get(_entity(hass, "sensor", f"{SECOND_CODE}_C-Cable_power"))
    assert power.attributes["port"] == "C-Cable"


async def test_the_160w_shows_its_priority_ports_and_does_not_set_them(hass, api, rtcx):
    """C-Cable and C1, the two its owner has picked in the app; read-only for now."""
    api.devices = [dict(DEVICE), dict(SECOND)]
    rtcx.second_state = {**rtcx.second_state, "charging_mode": "priority", "priority": ["C1"]}
    await _set_up(hass, api, rtcx)

    first = {
        port: hass.states.get(_entity(hass, "switch", f"{SECOND_CODE}_{port}_priority"))
        for port in ("C-Cable", "C1")
    }
    assert {port: state.state for port, state in first.items()} == {"C-Cable": "off", "C1": "on"}
    assert _entity(hass, "switch", f"{SECOND_CODE}_C2_priority") is None, "no bit named for C2"
    assert first["C1"].attributes["settable"] is False
    x783 = hass.states.get(_entity(hass, "switch", f"{DEVICE_CODE}_C1_priority"))
    assert x783.attributes["settable"] is True

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": first["C-Cable"].entity_id}, blocking=True
        )
    assert err.value.translation_key == "not_writable_on_model"
    assert not rtcx.priority_writes


async def test_the_160w_s_byte_33_is_auto_rotate_not_a_clock_style(hass, api, rtcx):
    api.devices = [dict(DEVICE), dict(SECOND)]
    await _set_up(hass, api, rtcx)

    rotate = hass.states.get(_entity(hass, "switch", f"{SECOND_CODE}_auto_rotate"))
    assert rotate.state == "off"
    # Its clock style is byte 32, its own two faces; its hours are not read.
    style = hass.states.get(_entity(hass, "select", f"{SECOND_CODE}_clock_style"))
    assert style.state == "top_right"
    assert style.attributes["options"] == ["top_right", "centred"]
    assert _entity(hass, "select", f"{SECOND_CODE}_time_format") is None
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "select", "select_option",
            {"entity_id": style.entity_id, "option": "centred"}, blocking=True,
        )
    assert err.value.translation_key == "not_writable_on_model"
    # The 300W keeps its clock style and has no auto-rotate.
    assert _entity(hass, "select", f"{DEVICE_CODE}_clock_style") is not None
    assert _entity(hass, "switch", f"{DEVICE_CODE}_auto_rotate") is None

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": rotate.entity_id}, blocking=True
        )
    assert err.value.translation_key == "auto_rotate_app_only"


async def test_the_160w_s_custom_limits_are_shown_without_protocols(hass, api, rtcx):
    """Two limits a byte each; its protocol boxes are not mapped, so not claimed."""
    api.devices = [dict(DEVICE), dict(SECOND)]
    rtcx.second_state = {
        **rtcx.second_state,
        "custom": [{"port": "C-Cable", "limit": 70}, {"port": "C1", "limit": 30}],
    }
    await _set_up(hass, api, rtcx)
    limits = {
        port: hass.states.get(_entity(hass, "sensor", f"{SECOND_CODE}_{port}_custom_limit"))
        for port in ("C-Cable", "C1")
    }
    assert {port: s.state for port, s in limits.items()} == {"C-Cable": "70", "C1": "30"}
    assert limits["C1"].attributes["port"] == "C1"
    assert "protocols" not in limits["C1"].attributes
    assert "protocol_mask" not in limits["C1"].attributes
