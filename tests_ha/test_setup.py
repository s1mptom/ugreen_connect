"""Start the integration for real, and put a poll wrong on purpose.

Two of these cover paths a charger will not reproduce on demand: a reply that
goes missing, and a model lookup that never answers. Both were reasoned about
when they were written and neither had ever been run.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.ugreen_connect.const import DOMAIN, MODEL_LOOKUP_ATTEMPTS
from tests_ha.conftest import DEVICE_CODE

PORT_NAMES = ("C1", "C2", "C3", "C4", "C5", "C6", "A1", "DC")


def _entity(hass, unique_id: str) -> str | None:
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id)


async def test_the_entry_loads(hass, started):
    assert started.state is ConfigEntryState.LOADED
    assert started.runtime_data is not None


async def test_the_charger_becomes_a_device(hass, started):
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), started.entry_id)
    assert [(DOMAIN, DEVICE_CODE)] in [list(d.identifiers) for d in devices]


@pytest.mark.parametrize("port", PORT_NAMES)
async def test_every_named_port_gets_its_entities(hass, started, port):
    """The names come from the model, which the account API answered for."""
    assert _entity(hass, f"{DEVICE_CODE}_{port}_power") is not None
    assert _entity(hass, f"{DEVICE_CODE}_{port}_protocol") is not None


async def test_a_reading_reaches_the_state_machine(hass, started):
    """Not "the coordinator holds it" -- what somebody's dashboard would show."""
    reading = started.runtime_data.data["power"][DEVICE_CODE]
    for port, values in reading["ports"].items():
        entity_id = _entity(hass, f"{DEVICE_CODE}_{port}_power")
        assert hass.states.get(entity_id).state == str(values["power"])
    total = _entity(hass, f"{DEVICE_CODE}_total_power")
    assert hass.states.get(total).state == str(reading["total"])


async def test_a_missed_reply_shows_the_last_reading_rather_than_nothing(
    hass, started, rtcx
):
    """The charger answers into one property, and anything else asking can take
    the reply. Blanking every entity for that cycle reads like the device fell
    off the shelf; nothing it last said is less true for being seconds old."""
    entity_id = _entity(hass, f"{DEVICE_CODE}_C1_power")
    before = hass.states.get(entity_id).state
    rtcx.power_answers = False
    await started.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == before
    reading = started.runtime_data.data["power"][DEVICE_CODE]
    assert reading["carried_for"] is not None


async def test_a_carried_reading_is_not_counted_as_charge(hass, started, rtcx):
    """A carried reading is the previous one shown again, not a measurement.

    Integrating it would invent energy across the gap it stands for.

    Asserted as the reason rather than as a total. A total is absolute: read at
    one moment and compared at another, it moves if anything else polls in
    between, and the test then passes or fails by machine rather than by
    behaviour -- the kind that gets disbelieved instead of investigated. What
    the coordinator promises is narrower and does not depend on timing at all:
    a reading it had to carry is never handed to the tracker.

    The spy goes in immediately before the poll that must carry, so nothing
    that happened earlier counts. Comparing the readings themselves would not
    work: a carried reading holds the very ports object the last good one did
    -- that is what makes it the previous reading rather than a new one -- so
    identity cannot tell the two apart. What can is that during this poll the
    tracker is not called at all.
    """
    coordinator = started.runtime_data
    handed: list[str] = []
    real_update = coordinator.sessions.update

    def _record(now, key, ports):
        handed.append(key)
        return real_update(now, key, ports)

    coordinator.sessions.update = _record
    try:
        rtcx.power_answers = False
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    finally:
        coordinator.sessions.update = real_update

    assert coordinator.data["power"][DEVICE_CODE]["carried_for"] is not None
    assert handed == [], f"the tracker was given a carried reading for {handed}"


async def test_it_gives_up_carrying_rather_than_carrying_forever(hass, started, rtcx):
    """Past the bound, unavailable is the honest answer again."""
    entity_id = _entity(hass, f"{DEVICE_CODE}_C1_power")
    rtcx.power_answers = False
    for _ in range(4):
        await started.runtime_data.async_refresh()
        await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


async def test_a_charger_nobody_can_name_gets_numbered_ports(
    hass, entry, api, rtcx, monkeypatch
):
    """Only the names are lost: the report's own length says how many ports it
    describes, so the readings survive a lookup that never answers."""
    from unittest.mock import patch

    api.product_answers = False
    entry.add_to_hass(hass)
    with (
        patch("custom_components.ugreen_connect.async_get_clientsession"),
        patch("custom_components.ugreen_connect.UgreenApi", return_value=api),
        patch("custom_components.ugreen_connect.RtcxClient", return_value=rtcx),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        for _ in range(MODEL_LOOKUP_ATTEMPTS):
            await entry.runtime_data.async_refresh()
            await hass.async_block_till_done()

    assert _entity(hass, f"{DEVICE_CODE}_P1_power") is not None
    assert _entity(hass, f"{DEVICE_CODE}_P8_power") is not None
    assert _entity(hass, f"{DEVICE_CODE}_C1_power") is None


async def test_it_unloads_again(hass, started):
    assert await hass.config_entries.async_unload(started.entry_id)
    await hass.async_block_till_done()


async def test_a_mode_s_parameters_are_written_down_when_they_move(
    hass, started, rtcx
):
    """And only then.

    The parameters can only be learned while their mode is running, so losing
    them at a restart means the next mode change goes out empty and resets
    whatever that mode was configured with. The state is re-read every minute
    and answers the same nearly every time, so saving on each one would rewrite
    an unchanged file all day -- often onto a memory card.
    """
    coordinator = started.runtime_data
    saves: list[dict[str, str]] = []
    coordinator._params_store = SimpleNamespace(
        async_delay_save=lambda data, _delay: saves.append(data())
    )

    async def poll_the_charger_again():
        # The state has its own minute-long timer, and every refresh inside it
        # answers from the last reply without asking. Emptying that is what
        # makes these three polls three reads rather than one.
        coordinator._state.clear()
        await coordinator.async_refresh()

    rtcx.mode_params = {"device-1:3": "02" + "00" * 34}
    await poll_the_charger_again()
    assert saves == [{"device-1:3": "02" + "00" * 34}]

    # The same answer again: nothing new to write down.
    await poll_the_charger_again()
    assert len(saves) == 1

    # Somebody moved the setting in the app.
    rtcx.mode_params = {"device-1:3": "05" + "00" * 34}
    await poll_the_charger_again()
    assert [s["device-1:3"][:2] for s in saves] == ["02", "05"]
