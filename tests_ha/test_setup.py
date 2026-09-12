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
from pytest_homeassistant_custom_component.common import flush_store

from custom_components.ugreen_connect.const import DOMAIN, MODEL_LOOKUP_ATTEMPTS
from tests_ha.conftest import DEVICE_CODE, IOT_ID

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
    them at a restart means the next mode change goes out empty, and the charger
    then either loses that mode's settings or refuses the change outright. The
    state is re-read every minute
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

    rtcx.mode_params = {f"{IOT_ID}:3": "02" + "00" * 34}
    await poll_the_charger_again()
    assert saves == [{f"{IOT_ID}:3": "02" + "00" * 34}]

    # The same answer again: nothing new to write down.
    await poll_the_charger_again()
    assert len(saves) == 1

    # Somebody moved the setting in the app.
    rtcx.mode_params = {f"{IOT_ID}:3": "05" + "00" * 34}
    await poll_the_charger_again()
    assert [s[f"{IOT_ID}:3"][:2] for s in saves] == ["02", "05"]


async def test_the_blocks_live_in_a_store_of_their_own_and_go_with_the_entry(
    hass, entry, api, rtcx, hass_storage
):
    """The wiring, not the logic: a real Store, its own key, loaded and removed.

    The promise this branch makes is that what was learned outlives the
    process. Everything under it can be right while the entry hands the client
    nothing, or writes into the models store, or leaves the file behind -- all
    of which the rest of the suite would report as passing.
    """
    from unittest.mock import patch

    params_key = f"{DOMAIN}.mode_params.{entry.entry_id}"
    models_key = f"{DOMAIN}.models.{entry.entry_id}"
    stored = {f"{IOT_ID}:3": "02" + "00" * 34}
    hass_storage[params_key] = {
        "version": 1,
        "minor_version": 1,
        "key": params_key,
        "data": dict(stored),
    }

    entry.add_to_hass(hass)
    with (
        patch("custom_components.ugreen_connect.async_get_clientsession"),
        patch("custom_components.ugreen_connect.UgreenApi", return_value=api),
        patch("custom_components.ugreen_connect.RtcxClient") as client_class,
    ):
        client_class.return_value = rtcx
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # What the file held was handed to the client, not merely loaded.
    assert client_class.call_args.kwargs["mode_params"] == stored

    # A block learned since is written back, under its own key -- the models
    # store is a different file and still holds what it holds.
    rtcx.mode_params = {f"{IOT_ID}:3": "05" + "00" * 34}
    entry.runtime_data._state.clear()
    await entry.runtime_data.async_refresh()
    # The save is delayed by a second; nothing reaches the file until it runs.
    await flush_store(entry.runtime_data._params_store)
    assert hass_storage[params_key]["data"] == {f"{IOT_ID}:3": "05" + "00" * 34}
    # The models store is a separate file, and writing one must not be writing
    # the other -- the same key for both would have them clobbering each other.
    await flush_store(entry.runtime_data._model_store)
    assert models_key in hass_storage
    assert hass_storage[models_key]["data"] != hass_storage[params_key]["data"]

    # And it goes when the account does.
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert params_key not in hass_storage
    assert models_key not in hass_storage


async def test_a_store_of_the_wrong_shape_does_not_stop_the_entry(
    hass, entry, api, rtcx, hass_storage
):
    """The file outlives this code, and two things downstream build a dict of it.

    A root that is not a dict used to raise out of the middle of setup, leaving
    the entry in error until something reloaded it -- and the reload raised the
    same way. For a cache that is relearned in a minute.
    """
    from unittest.mock import patch

    params_key = f"{DOMAIN}.mode_params.{entry.entry_id}"
    hass_storage[params_key] = {
        "version": 1,
        "minor_version": 1,
        "key": params_key,
        "data": [{f"{IOT_ID}:3": "02" + "00" * 34}],
    }

    entry.add_to_hass(hass)
    with (
        patch("custom_components.ugreen_connect.async_get_clientsession"),
        patch("custom_components.ugreen_connect.UgreenApi", return_value=api),
        patch("custom_components.ugreen_connect.RtcxClient", return_value=rtcx),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_selecting_a_mode_tells_the_client_which_charger_it_is(hass, started):
    """The model decides how long the parameter block is.

    Sending the X783's 35 bytes to a 160W lands on its screensaver group, so
    the client refuses a model it cannot measure -- which it can only do if the
    entity passes one. Nothing else in the suite reaches this entity, and the
    argument is invisible until the day a second model becomes writable.
    """
    rtcx = started.runtime_data.rtcx
    await hass.services.async_call(
        "select",
        "select_option",
        {
            "entity_id": "select.ugreen_nexode_pro_x783_charging_mode",
            "option": "thermal_safe",
        },
        blocking=True,
    )
    assert rtcx.mode_writes == [(IOT_ID, 1, "X783")]
