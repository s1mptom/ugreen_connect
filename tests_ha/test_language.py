"""What the cards find a charger's entities by, on an install that is not English.

Home Assistant makes entity ids out of names in the language it was set up in,
so a German install has `sensor.<device>_gesamtleistung` where the cards looked
for `_total_power` (#45). The cards now go by the registry's translation key,
and for a port's entities by the port, which each of them names in an
attribute. These say that both are there.
"""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er

from custom_components.ugreen_connect.const import DOMAIN
from tests_ha.conftest import DEVICE_CODE
from tests_ha.test_chargers import _set_up

# Every kind of entity that is about one port, as its translation key.
PER_PORT = {
    "port_power", "port_voltage", "port_current", "port_protocol",
    "session_energy", "session_charge", "energy_total",
    "charging", "charging_event", "priority_port", "port_output", "custom_limit",
}


async def test_a_german_install_names_its_ids_in_german(hass, api, rtcx):
    hass.config.language = "de"
    entry = await _set_up(hass, api, rtcx)
    registry = er.async_get(hass)
    ids = [e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)]
    assert not any(i.endswith("_total_power") for i in ids), "the premise: no English ids"
    assert any(i.endswith("_gesamtleistung") for i in ids)


async def test_every_port_entity_names_its_port(hass, api, rtcx):
    hass.config.language = "de"
    entry = await _set_up(hass, api, rtcx)
    registry = er.async_get(hass)
    seen = set()
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.translation_key not in PER_PORT:
            continue
        state = hass.states.get(entity.entity_id)
        if state is None:
            continue
        seen.add(entity.translation_key)
        assert state.attributes.get("port"), f"{entity.entity_id} does not say its port"
    # The kinds a charger in `custom` with sessions and a priority block has.
    assert {"port_power", "port_voltage", "charging", "charging_event", "energy_total"} <= seen


async def test_the_charger_s_own_entities_name_no_port(hass, api, rtcx):
    entry = await _set_up(hass, api, rtcx)
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.translation_key in PER_PORT or entity.platform != DOMAIN:
            continue
        state = hass.states.get(entity.entity_id)
        if state is not None:
            assert "port" not in state.attributes, entity.entity_id


async def test_the_port_goes_beside_what_the_platform_declares(hass, api, rtcx):
    """A capability of ours must not replace a sensor's state class."""
    entry = await _set_up(hass, api, rtcx)
    registry = er.async_get(hass)
    kinds = {}
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        state = hass.states.get(entity.entity_id)
        if state is not None and state.attributes.get("port") == "C1":
            kinds[entity.translation_key] = state.attributes
    assert kinds["port_power"]["state_class"] == "measurement"
    assert kinds["energy_total"]["state_class"] == "total_increasing"
    assert "started" in kinds["charging_event"]["event_types"]


async def test_an_unavailable_port_entity_still_names_its_port(hass, api, rtcx):
    """A charger offline is exactly when the cards must still say which port is which."""
    entry = await _set_up(hass, api, rtcx)
    rtcx.power_answers = False
    for _ in range(4):
        await entry.runtime_data.async_refresh()
    power = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{DEVICE_CODE}_C1_power")
    state = hass.states.get(power)
    assert state.state == "unavailable"
    assert state.attributes["port"] == "C1"
