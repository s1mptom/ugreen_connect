"""Start the integration for real, and put a poll wrong on purpose.

Two of these cover paths a charger will not reproduce on demand: a reply that
goes missing, and a model lookup that never answers. Both were reasoned about
when they were written and neither had ever been run.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from types import SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import flush_store

from custom_components.ugreen_connect.const import (
    CHARGING_MODES,
    DOMAIN,
    MODEL_LOOKUP_ATTEMPTS,
    SELECTABLE_MODES,
)
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


CHARGING_MODE_SELECT = "select.ugreen_nexode_pro_x783_charging_mode"


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
            "entity_id": CHARGING_MODE_SELECT,
            "option": "thermal_safe",
        },
        blocking=True,
    )
    assert rtcx.mode_writes == [(IOT_ID, 1, "X783")]


async def test_a_mode_that_cannot_be_set_is_still_reported(hass, started):
    """`unknown` said nothing about a charger happily running custom.

    A select whose current option is absent from its own list renders as
    `unknown`, which reads as a broken entity rather than as a mode the app
    put the charger in. The mode joins the options so it can be reported.
    """
    state = hass.states.get(CHARGING_MODE_SELECT)
    assert state.state == "custom"
    assert state.attributes["options"] == [*SELECTABLE_MODES, "custom"]


async def test_reporting_custom_does_not_make_it_settable(hass, started, rtcx):
    """Reported is not settable, and the refusal has to reach the caller.

    Returning quietly would tell a script its call worked when nothing was
    sent. Nothing may reach the charger either, which is what the empty write
    list says. The text is pinned as well as the key: the key alone would pass
    a message that names the mode by its raw key `custom` rather than by what
    the select shows.
    """
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": CHARGING_MODE_SELECT, "option": "custom"},
            blocking=True,
        )
    assert err.value.translation_key == "mode_not_selectable"
    assert str(err.value).startswith("Custom can only be set")
    assert rtcx.mode_writes == []


def test_custom_is_the_only_mode_the_refusal_can_mean():
    """The refusal names `custom` outright, in every language.

    A second mode outside the presets would be refused with a message about
    the wrong one, so adding it has to come back here.
    """
    assert set(CHARGING_MODES.values()) - set(SELECTABLE_MODES) == {"custom"}


@pytest.mark.parametrize(
    ("service", "data"),
    [("select_last", {}), ("select_next", {"cycle": False})],
    ids=["select_last", "select_next_without_cycle"],
)
async def test_stepping_onto_custom_is_refused_too(
    hass, started, rtcx, service, data
):
    """`select_last`, and `select_next` without cycling, land on `custom`.

    Both call `async_select_option` directly, and with the charger in custom
    the last option is `custom`. An automation using either to leave custom
    for a preset gets this refusal and no write. `select_next` with its
    default `cycle: true` wraps round to the first preset, and `select_first`
    and `select_previous` reach a preset too, so those still write.
    """
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "select", service, {"entity_id": CHARGING_MODE_SELECT, **data}, blocking=True
        )
    assert err.value.translation_key == "mode_not_selectable"
    assert rtcx.mode_writes == []


async def test_under_a_preset_the_options_are_the_presets(hass, started, rtcx):
    """The extra option belongs to the mode in force, and leaves with it."""
    rtcx.state = {**rtcx.state, "charging_mode": "priority", "custom": None}
    rtcx.stale = True          # as a write would, so the cached copy is re-read
    await started.runtime_data.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(CHARGING_MODE_SELECT)
    assert state.state == "priority"
    assert state.attributes["options"] == list(SELECTABLE_MODES)


CUSTOM_GROUPS = ("C1", "C2", "C3", "C4", "C5", "C6+A")


async def test_every_custom_group_gets_its_own_sensor(hass, started):
    """Six groups, six entities, six values -- and six distinct ids.

    A shared unique id collapses the six into one, which fails here by group
    name.
    """
    registry = er.async_get(hass)
    reading = started.runtime_data.data["power"][DEVICE_CODE]
    limits = {group["port"]: group["limit"] for group in reading["custom"]}

    seen = set()
    for group in CUSTOM_GROUPS:
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{DEVICE_CODE}_{group}_custom_limit"
        )
        assert entity_id is not None, f"no sensor for {group}"
        seen.add(entity_id)
        assert hass.states.get(entity_id).state == str(limits[group])

    assert len(seen) == len(CUSTOM_GROUPS), "the six share an id"


async def test_leaving_custom_mode_makes_its_sensors_unavailable(hass, started, rtcx):
    """Under a preset the block is that preset's own settings, not a layout.

    So the parser returns nothing and these have nothing to say. Unavailable
    rather than holding the last configuration, which would describe a mode the
    charger is no longer in.
    """
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{DEVICE_CODE}_C3_custom_limit"
    )
    assert hass.states.get(entity_id).state == "30"

    rtcx.state = {**rtcx.state, "charging_mode": "priority", "custom": None}
    rtcx.stale = True          # as a write would, so the cached copy is re-read
    await started.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


PRIORITY_PORTS = ("C1", "C2", "C3")


def _priority_switch(hass, port: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        "switch", DOMAIN, f"{DEVICE_CODE}_{port}_priority"
    )


async def _charger_now(hass, started, rtcx, **state) -> None:
    """Put the fake charger in another state and let a poll find it."""
    rtcx.state = {**rtcx.state, **state}
    rtcx.stale = True          # as a write would, so the cached copy is re-read
    await started.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_the_priority_ports_are_there_before_priority_is_chosen(hass, started):
    """Three switches from the start, quiet until the mode is running.

    The fixture's charger is in `custom`, where the byte they would read is
    C1's limit. They exist anyway, so choosing `priority` in the app or in the
    mode select brings them to life instead of making them appear.
    """
    for port in PRIORITY_PORTS:
        entity_id = _priority_switch(hass, port)
        assert entity_id == f"switch.ugreen_nexode_pro_x783_{port.lower()}_charged_first"
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


async def test_under_priority_each_switch_says_whether_its_port_goes_first(
    hass, started, rtcx
):
    await _charger_now(
        hass, started, rtcx, charging_mode="priority", custom=None, priority=["C1", "C3"]
    )
    states = {port: hass.states.get(_priority_switch(hass, port)).state for port in PRIORITY_PORTS}
    assert states == {"C1": "on", "C2": "off", "C3": "on"}


async def test_a_port_turned_on_joins_the_ones_already_first(hass, started, rtcx):
    """The charger takes the whole choice in one byte, so the write is the set.

    Sending only the port that changed would make it the only one first.
    """
    await _charger_now(
        hass, started, rtcx, charging_mode="priority", custom=None, priority=["C2"]
    )
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": _priority_switch(hass, "C1")}, blocking=True
    )
    await hass.async_block_till_done()

    assert rtcx.priority_writes == [(IOT_ID, ["C1", "C2"], "X783")]
    assert hass.states.get(_priority_switch(hass, "C1")).state == "on"
    assert hass.states.get(_priority_switch(hass, "C2")).state == "on"


async def test_all_three_can_go_first_at_once(hass, started, rtcx):
    await _charger_now(
        hass, started, rtcx, charging_mode="priority", custom=None, priority=["C1", "C2"]
    )
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": _priority_switch(hass, "C3")}, blocking=True
    )
    assert rtcx.priority_writes == [(IOT_ID, ["C1", "C2", "C3"], "X783")]


async def test_a_port_turned_off_leaves_the_others_first(hass, started, rtcx):
    await _charger_now(
        hass, started, rtcx, charging_mode="priority", custom=None, priority=["C1", "C3"]
    )
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": _priority_switch(hass, "C1")}, blocking=True
    )
    await hass.async_block_till_done()

    assert rtcx.priority_writes == [(IOT_ID, ["C3"], "X783")]
    assert hass.states.get(_priority_switch(hass, "C1")).state == "off"


async def test_the_last_port_first_cannot_be_turned_off(hass, started, rtcx):
    """An empty mask has never been sent to a charger.

    Refused where it is asked, with a reason, rather than sent to find out.
    """
    await _charger_now(
        hass, started, rtcx, charging_mode="priority", custom=None, priority=["C2"]
    )
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": _priority_switch(hass, "C2")}, blocking=True
        )
    assert err.value.translation_key == "priority_needs_a_port"
    assert rtcx.priority_writes == []


async def test_a_port_already_where_it_is_asked_to_be_sends_nothing(hass, started, rtcx):
    await _charger_now(
        hass, started, rtcx, charging_mode="priority", custom=None, priority=["C2"]
    )
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": _priority_switch(hass, "C2")}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": _priority_switch(hass, "C1")}, blocking=True
    )
    assert rtcx.priority_writes == []


async def test_two_presses_close_together_both_reach_the_charger(hass, started, rtcx):
    """C1 on and straight off again, the second pressed before the first is done.

    Each press sends the whole set, worked out from the last reading. Started
    side by side, both work from the reading before either, so the second finds
    C1 already off, sends nothing, and the charger keeps C1 -- the opposite of
    the last thing asked. Taken in turn, the second starts from what the first
    read back.
    """
    await _charger_now(
        hass, started, rtcx, charging_mode="priority", custom=None, priority=["C2"]
    )
    c1 = _priority_switch(hass, "C1")
    await asyncio.gather(
        hass.services.async_call("switch", "turn_on", {"entity_id": c1}, blocking=True),
        hass.services.async_call("switch", "turn_off", {"entity_id": c1}, blocking=True),
    )
    await hass.async_block_till_done()

    assert [ports for _, ports, _ in rtcx.priority_writes] == [["C1", "C2"], ["C2"]]
    assert hass.states.get(c1).state == "off"


async def test_outside_priority_the_switches_write_nothing(hass, started, rtcx):
    """Under another mode the byte is that mode's setting.

    Under `dc_turbo` it is the DC port's voltage, so a write here would change
    the voltage, or put the charger in `priority` behind its owner's back.
    """
    await _charger_now(hass, started, rtcx, charging_mode="dc_turbo", custom=None, priority=None)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": _priority_switch(hass, "C1")}, blocking=True
    )
    assert rtcx.priority_writes == []
    assert hass.states.get(_priority_switch(hass, "C1")).state == STATE_UNAVAILABLE



DC_VOLTAGE = "select.ugreen_nexode_pro_x783_dc_port_voltage"
DC_ALWAYS_ON = "switch.ugreen_nexode_pro_x783_dc_always_on"
IN_TURBO = {"charging_mode": "dc_turbo", "custom": None, "priority": None}


async def test_the_dc_settings_are_there_before_dc_turbo_is_chosen(hass, started):
    registry = er.async_get(hass)
    voltage = registry.async_get_entity_id("select", DOMAIN, f"{DEVICE_CODE}_dc_voltage")
    always_on = registry.async_get_entity_id("switch", DOMAIN, f"{DEVICE_CODE}_dc_always_on")
    assert (voltage, always_on) == (DC_VOLTAGE, DC_ALWAYS_ON)
    assert hass.states.get(DC_VOLTAGE).state == STATE_UNAVAILABLE
    assert hass.states.get(DC_ALWAYS_ON).state == STATE_UNAVAILABLE


async def test_under_dc_turbo_they_say_what_the_dc_port_is_set_to(hass, started, rtcx):
    await _charger_now(
        hass, started, rtcx, **IN_TURBO, dc_turbo={"voltage": 20, "always_on": False}
    )
    voltage = hass.states.get(DC_VOLTAGE)
    assert voltage.state == "20"
    assert voltage.attributes["options"] == ["12", "15", "20"]
    assert hass.states.get(DC_ALWAYS_ON).state == "off"


async def test_a_voltage_the_charger_does_not_report_is_unknown(hass, started, rtcx):
    await _charger_now(
        hass, started, rtcx, **IN_TURBO, dc_turbo={"voltage": None, "always_on": True}
    )
    assert hass.states.get(DC_VOLTAGE).state == "unknown"
    assert hass.states.get(DC_ALWAYS_ON).state == "on"


async def test_choosing_a_voltage_sends_only_the_voltage(hass, started, rtcx):
    await _charger_now(
        hass, started, rtcx, **IN_TURBO, dc_turbo={"voltage": 20, "always_on": True}
    )
    await hass.services.async_call(
        "select", "select_option", {"entity_id": DC_VOLTAGE, "option": "15"}, blocking=True
    )
    await hass.async_block_till_done()
    assert rtcx.turbo_writes == [{"voltage": 15}]
    assert hass.states.get(DC_VOLTAGE).state == "15"
    assert hass.states.get(DC_ALWAYS_ON).state == "on"


async def test_always_on_sends_only_always_on(hass, started, rtcx):
    await _charger_now(
        hass, started, rtcx, **IN_TURBO, dc_turbo={"voltage": 12, "always_on": False}
    )
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": DC_ALWAYS_ON}, blocking=True
    )
    await hass.async_block_till_done()
    assert rtcx.turbo_writes == [{"always_on": True}]
    assert hass.states.get(DC_ALWAYS_ON).state == "on"
    assert hass.states.get(DC_VOLTAGE).state == "12"


async def test_the_setting_already_there_sends_nothing(hass, started, rtcx):
    await _charger_now(
        hass, started, rtcx, **IN_TURBO, dc_turbo={"voltage": 12, "always_on": False}
    )
    await hass.services.async_call(
        "select", "select_option", {"entity_id": DC_VOLTAGE, "option": "12"}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": DC_ALWAYS_ON}, blocking=True
    )
    assert rtcx.turbo_writes == []


async def test_the_voltage_and_always_on_changed_together_both_land(hass, started, rtcx):
    """Two platforms, one block: they take turns with each other too.

    Each change goes out in the mode's whole frame. Started side by side, the
    second would start from the block before the first and put its byte back.
    """
    await _charger_now(
        hass, started, rtcx, **IN_TURBO, dc_turbo={"voltage": 20, "always_on": False}
    )
    await asyncio.gather(
        hass.services.async_call(
            "select", "select_option", {"entity_id": DC_VOLTAGE, "option": "12"}, blocking=True
        ),
        hass.services.async_call(
            "switch", "turn_on", {"entity_id": DC_ALWAYS_ON}, blocking=True
        ),
    )
    await hass.async_block_till_done()
    assert hass.states.get(DC_VOLTAGE).state == "12"
    assert hass.states.get(DC_ALWAYS_ON).state == "on"
    assert not started.runtime_data.mode_turns(DEVICE_CODE).locked(), "the queue was left held"


async def test_outside_dc_turbo_the_dc_settings_write_nothing(hass, started, rtcx):
    """Under `priority` the first byte is the port mask; 15 V there is C1 and C2."""
    await _charger_now(
        hass, started, rtcx, charging_mode="priority", custom=None, priority=["C2"], dc_turbo=None
    )
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": DC_ALWAYS_ON}, blocking=True
    )
    assert rtcx.turbo_writes == []
    assert hass.states.get(DC_VOLTAGE).state == STATE_UNAVAILABLE


async def test_the_debug_log_carries_nothing_of_the_household(hass, started, rtcx, caplog):
    """What people mapping a charger are asked to post.

    Debug logging on, a poll, a write, and a failure whose message the cloud
    wrote -- with the charger's unit code in it, which no line here would put
    there on purpose. None of the account, the unit code, the cloud id or the
    MAC may come out.
    """
    from custom_components.ugreen_connect.api import UgreenError
    from custom_components.ugreen_connect.logsafe import charger_tag

    caplog.set_level(logging.DEBUG, logger="custom_components.ugreen_connect")
    await started.runtime_data.async_refresh()
    await hass.services.async_call(
        "select", "select_option", {"entity_id": CHARGING_MODE_SELECT, "option": "thermal_safe"},
        blocking=True,
    )

    # Every identifier at once, in words the cloud wrote: no line here would put
    # them there, so only the net under the lines can take them out.
    async def _fails(*_args, **_kwargs):
        raise UgreenError(
            f"gateway refused {DEVICE_CODE} ({IOT_ID}) at EC:1A:C3:00:00:01 "
            "for someone@example.invalid"
        )

    rtcx.async_power = _fails
    await started.runtime_data.async_refresh()
    await hass.async_block_till_done()

    text = caplog.text
    assert "Live power unavailable" in text, "the failure has to have been logged"
    for secret in (DEVICE_CODE, IOT_ID, "EC:1A:C3:00:00:01", "someone@example.invalid"):
        assert secret not in text, f"{secret} is in the log"
    assert charger_tag(IOT_ID) in text, "a line still says which charger"


async def test_the_setup_form_hides_the_account_before_it_tries_it(hass, caplog, monkeypatch):
    """The first attempt is logged before any entry exists to learn it from.

    The cloud's answer to a failed login is logged at debug, and nothing stops
    the cloud from saying the address back.
    """
    import logging as logging_

    from custom_components.ugreen_connect import config_flow, logsafe
    from custom_components.ugreen_connect.api import UgreenError

    email, password = "new.owner@example.invalid", "correct-horse-battery-9"

    class _Api:
        def __init__(self, *_args, **_kwargs):
            pass

        async def login(self, *_args, **_kwargs):
            # Both, as the cloud might say them back.
            raise UgreenError(f"no account for {email} with {password}")

    # As on a fresh start: nothing installed until the form is filled in.
    make, scope = logging_.getLogRecordFactory(), logsafe._scope
    logsafe._scope = None
    monkeypatch.setattr(config_flow, "UgreenApi", _Api)
    caplog.set_level(logging.DEBUG, logger="custom_components.ugreen_connect")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": email, "password": password, "region": "europe"}
    )
    try:
        assert result["errors"] == {"base": "cannot_connect"}
        assert "Cannot connect to UGREEN cloud" in caplog.text
        assert email not in caplog.text
        assert password not in caplog.text
        assert logsafe._scope == "custom_components.ugreen_connect", "the form installs it"
    finally:
        logging_.setLogRecordFactory(make)
        logsafe._scope = scope


async def test_a_new_password_is_hidden_before_it_is_tried(hass, entry, caplog, monkeypatch):
    """Re-authenticating, the new password goes to the cloud before any entry
    has it -- and a refusal is logged at debug."""
    from custom_components.ugreen_connect import config_flow
    from custom_components.ugreen_connect.api import UgreenError

    password = "a-brand-new-password-7"

    class _Api:
        def __init__(self, *_args, **_kwargs):
            pass

        async def login(self, *_args, **_kwargs):
            raise UgreenError(f"refused {password}")

    monkeypatch.setattr(config_flow, "UgreenApi", _Api)
    entry.add_to_hass(hass)
    caplog.set_level(logging.DEBUG, logger="custom_components.ugreen_connect")
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": password}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert password not in caplog.text
    from custom_components.ugreen_connect import logsafe

    assert logsafe.scrub(password) == "<password>"


async def test_a_stored_block_under_a_bare_cloud_id_is_still_hidden(
    hass, entry, api, rtcx, hass_storage
):
    """A key with no colon in the mode store is a cloud id whole."""
    from unittest.mock import patch

    from custom_components.ugreen_connect import logsafe

    bare = "JuSTiZWwzabFoehKLgWT8Uoju"
    params_key = f"{DOMAIN}.mode_params.{entry.entry_id}"
    hass_storage[params_key] = {
        "version": 1, "minor_version": 1, "key": params_key, "data": {bare: "00" * 35},
    }
    entry.add_to_hass(hass)
    with (
        patch("custom_components.ugreen_connect.async_get_clientsession"),
        patch("custom_components.ugreen_connect.UgreenApi", return_value=api),
        patch("custom_components.ugreen_connect.RtcxClient", return_value=rtcx),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert logsafe.scrub(bare) == f"charger {logsafe.charger_tag(bare)}"


@pytest.mark.parametrize(
    ("field", "value"),
    [("path", "/config/www/pavels-family.jpg"), ("url", "https://cdn.example/me.jpg?Signature=abc")],
)
async def test_the_wallpaper_service_does_not_say_what_it_was_given(
    hass, started, monkeypatch, field, value
):
    """A path and a URL are the owner's own, and a URL can carry a token."""
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.ugreen_connect import services

    monkeypatch.setattr(hass.config, "is_allowed_path", lambda _path: True)

    class _Session:
        def get(self, *_args, **_kwargs):
            raise services.aiohttp.InvalidURL(value)

    monkeypatch.setattr(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession", lambda _hass: _Session()
    )
    device = next(
        d
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), started.entry_id)
        if (DOMAIN, DEVICE_CODE) in d.identifiers
    )
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN, "set_wallpaper", {"device_id": device.id, field: value}, blocking=True
        )
    assert value not in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__


async def test_idle_the_poll_slows_but_not_while_debug_logging(hass, started, caplog):
    """Mapping a setting is done with nothing plugged in, a few seconds apart."""
    coordinator = started.runtime_data
    coordinator._drawing = False
    # Said outright: asking for `caplog` puts the root logger at DEBUG here.
    caplog.set_level(logging.INFO, logger="custom_components.ugreen_connect")
    coordinator._reschedule(0)
    slow = coordinator.update_interval
    caplog.set_level(logging.DEBUG, logger="custom_components.ugreen_connect")
    coordinator._reschedule(0)
    assert coordinator.update_interval < slow
    assert coordinator.update_interval.total_seconds() == coordinator._target_period


async def test_the_state_is_read_once_a_minute(hass, started, rtcx):
    before = rtcx.state_reads
    for _ in range(3):
        await started.runtime_data.async_refresh()
    assert rtcx.state_reads == before


async def test_while_debug_logging_the_state_is_read_every_poll(hass, started, rtcx, caplog):
    """Somebody mapping their charger changes one setting in the app at a time.

    A minute between reads would put several of those into one diff.
    """
    caplog.set_level(logging.DEBUG, logger="custom_components.ugreen_connect")
    before = rtcx.state_reads
    for _ in range(3):
        await started.runtime_data.async_refresh()
    assert rtcx.state_reads == before + 3


@pytest.mark.parametrize(
    "carrying", [False, True], ids=["after_a_fresh_poll", "while_already_carried"]
)
async def test_a_missed_poll_after_a_confirmed_write_keeps_the_new_value(
    hass, started, rtcx, carrying
):
    """What a read-back confirmed has to survive the next reply going missing.

    A missed reply is ordinary, and what is carried across it is the retained
    reading, not the one on screen. If only the one on screen took the
    read-back, the first miss after a write would put the control back to its
    value from before the write, and the next good poll would move it forward
    again. The second case writes while a reading is already being carried,
    which is the one dropping the copy alone does not cover.
    """
    coordinator = started.runtime_data
    rtcx.state = {**rtcx.state, "charging_mode": "priority", "custom": None}
    rtcx.stale = True
    await coordinator.async_refresh()
    if carrying:
        rtcx.power_answers = False
        await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(CHARGING_MODE_SELECT).state == "priority"

    write = rtcx.async_set_charging_mode

    async def taken(iot_id, mode, model=None):
        await write(iot_id, mode, model)
        rtcx.state = {**rtcx.state, "charging_mode": "dc_turbo"}
        rtcx.stale = True

    rtcx.async_set_charging_mode = taken
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": CHARGING_MODE_SELECT, "option": "dc_turbo"},
        blocking=True,
    )
    assert hass.states.get(CHARGING_MODE_SELECT).state == "dc_turbo"

    rtcx.power_answers = False
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(CHARGING_MODE_SELECT).state == "dc_turbo"


async def test_a_poll_that_began_before_a_write_does_not_republish_the_old_state(
    hass, started, rtcx
):
    """A poll holds a reading built across its awaits, and a write lands in one.

    The poll reads the charger's settings early and then goes on asking about
    other things. A control written in that window publishes what the charger
    answered, through its read-back -- and the poll, finishing afterwards with
    the settings it read before the write, would put the old value back for a
    poll. That is the same wrong value on screen this read-back exists to
    prevent, arriving from the other side.

    The stale flag cannot be the guard here: the read-back's own state read
    clears it, so by the time the poll finishes there is nothing left to see.
    """
    coordinator = started.runtime_data
    rtcx.state = {**rtcx.state, "charging_mode": "priority", "custom": None}
    rtcx.stale = True
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(CHARGING_MODE_SELECT).state == "priority"

    # Hold the next poll after it has read the state, on an await of the kind
    # that does not queue behind the charger's one conversation.
    reached, release = asyncio.Event(), asyncio.Event()
    static_info = coordinator._static_info

    async def held(key, iot_id):
        reached.set()
        await release.wait()
        return await static_info(key, iot_id)

    coordinator._static_info = held
    poll = asyncio.get_running_loop().create_task(coordinator.async_refresh())
    await asyncio.wait_for(reached.wait(), 10)

    write = rtcx.async_set_charging_mode

    async def taken(iot_id, mode, model=None):
        await write(iot_id, mode, model)
        rtcx.state = {**rtcx.state, "charging_mode": "dc_turbo"}
        rtcx.stale = True

    rtcx.async_set_charging_mode = taken
    await asyncio.wait_for(
        hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": CHARGING_MODE_SELECT, "option": "dc_turbo"},
            blocking=True,
        ),
        10,
    )
    assert hass.states.get(CHARGING_MODE_SELECT).state == "dc_turbo"

    coordinator._static_info = static_info
    release.set()
    await asyncio.wait_for(poll, 10)
    await hass.async_block_till_done()
    assert hass.states.get(CHARGING_MODE_SELECT).state == "dc_turbo"

    # And the retained reading with it: a reply going missing next must not
    # carry the settings from before the write either.
    rtcx.power_answers = False
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(CHARGING_MODE_SELECT).state == "dc_turbo"


async def test_the_settings_are_taken_after_the_debug_dump_as_well(hass, started, rtcx):
    """The dump is an await like any other, and it runs after the reading is built.

    With the debug dump switched on, `_async_poll` hands the assembled reading
    to a thread and waits for it. A write finishing in that window publishes
    what the charger said, and the poll would then return the reading it built
    before -- so the settings are taken from the cache below the dump, not
    above it.
    """
    coordinator = started.runtime_data
    rtcx.state = {**rtcx.state, "charging_mode": "priority", "custom": None}
    rtcx.stale = True
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(CHARGING_MODE_SELECT).state == "priority"

    writing, release = threading.Event(), threading.Event()

    def dump(_data):
        writing.set()
        assert release.wait(10), "the test never let the dump finish"

    coordinator._debug_dump = True
    coordinator._write_dump = dump
    poll = asyncio.get_running_loop().create_task(coordinator.async_refresh())
    async with asyncio.timeout(10):
        while not writing.is_set():
            await asyncio.sleep(0)

    write = rtcx.async_set_charging_mode

    async def taken(iot_id, mode, model=None):
        await write(iot_id, mode, model)
        rtcx.state = {**rtcx.state, "charging_mode": "dc_turbo"}
        rtcx.stale = True

    rtcx.async_set_charging_mode = taken
    await asyncio.wait_for(
        hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": CHARGING_MODE_SELECT, "option": "dc_turbo"},
            blocking=True,
        ),
        10,
    )
    assert hass.states.get(CHARGING_MODE_SELECT).state == "dc_turbo"

    coordinator._debug_dump = False
    release.set()
    await asyncio.wait_for(poll, 10)
    await hass.async_block_till_done()
    assert hass.states.get(CHARGING_MODE_SELECT).state == "dc_turbo"


async def test_a_picture_the_poll_did_not_list_is_still_offered(hass, started, rtcx):
    """The wallpaper list is built before the correction, and stays as it was.

    `wallpaper_list` comes from the account's library, fetched during the poll
    from the wallpaper the poll read; the correction moves `wallpaper` under it
    without rebuilding it. What keeps that from reading as a broken entity is
    that the ids on the device are a state key too, so they are corrected in
    the same breath: the picture is an option, and the select shows it. Only
    the previews in the attributes are a poll behind, and the next poll fetches
    the library again for an id it cannot name.
    """
    coordinator = started.runtime_data
    reached, release = asyncio.Event(), asyncio.Event()
    static_info = coordinator._static_info

    async def held(key, iot_id):
        reached.set()
        await release.wait()
        return await static_info(key, iot_id)

    coordinator._static_info = held
    poll = asyncio.get_running_loop().create_task(coordinator.async_refresh())
    await asyncio.wait_for(reached.wait(), 10)

    # A control is written while the poll waits, and the charger comes back
    # showing a picture chosen in the app meanwhile.
    async def moved_on(*_args, **_kwargs):
        rtcx.state = {
            **rtcx.state,
            "screensaver": True,
            "wallpaper": "ABCDEF",
            "wallpapers": ["31F207", "ABCDEF"],
        }
        rtcx.stale = True

    rtcx.async_set_screensaver = moved_on
    await asyncio.wait_for(
        hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": "switch.ugreen_nexode_pro_x783_screensaver"},
            blocking=True,
        ),
        10,
    )

    coordinator._static_info = static_info
    release.set()
    await asyncio.wait_for(poll, 10)
    await hass.async_block_till_done()

    wallpaper = hass.states.get("select.ugreen_nexode_pro_x783_wallpaper")
    assert wallpaper.state == "ABCDEF"
    assert "ABCDEF" in wallpaper.attributes["options"]


class _Resources:
    """Lovelace's resource collection, as much of it as frontend.py touches."""

    def __init__(self, items):
        self.loaded = True
        self._items = list(items)
        self.deleted: list[str] = []
        self._next = 0

    def async_items(self):
        return list(self._items)

    async def async_create_item(self, item):
        # A counter rather than the list's length: once an item is deleted, the
        # length names an id that is still in use, and deleting that one would
        # take two. Home Assistant's own collection hands out uuids.
        self._next += 1
        self._items.append({"id": f"new{self._next}", **item})

    async def async_delete_item(self, item_id):
        self.deleted.append(item_id)
        self._items = [item for item in self._items if item["id"] != item_id]


async def test_one_resource_per_card_even_after_the_url_changes(hass):
    """Two resources for one file load the module twice, and the stale one wins.

    Three addresses this card has had: a version query from an early release,
    the bare path from before the fingerprint, and the fingerprinted one now.
    Left side by side, the browser fetched each, and whichever copy defined the
    element first was the card on screen -- after an update, the old one.

    Every card the integration ships gets a resource; the module they import
    does not, because they fetch it themselves by relative path. Somebody
    else's card under a different path is left alone.
    """
    from custom_components.ugreen_connect.frontend import (
        CARD_FILES,
        WWW_URL,
        _register_resource,
        fingerprint,
    )

    base = f"{WWW_URL}/{fingerprint()}"
    resources = _Resources(
        [
            {
                "id": "query",
                "url": f"{WWW_URL}/ugreen-wallpaper-card.js?v=0.10.0",
                "type": "module",
            },
            {"id": "bare", "url": f"{WWW_URL}/ugreen-ports-card.js", "type": "module"},
            {"id": "other", "url": "/local/somebody-elses-card.js", "type": "module"},
        ]
    )
    hass.data["lovelace"] = SimpleNamespace(resources=resources)

    for name in CARD_FILES:
        await _register_resource(hass, f"{base}/{name}")

    urls = [item["url"] for item in resources.async_items()]
    assert urls == [
        "/local/somebody-elses-card.js",
        *[f"{base}/{name}" for name in CARD_FILES],
    ]
    assert sorted(resources.deleted) == ["bare", "query"]
    assert not any("ugreen-ui.js" in url for url in urls), (
        "the shared module is imported by the cards, not loaded on its own"
    )

    # Run again, as every restart does: still one each, and nothing deleted twice.
    for name in CARD_FILES:
        await _register_resource(hass, f"{base}/{name}")
    assert [item["url"] for item in resources.async_items()] == urls
    assert sorted(resources.deleted) == ["bare", "query"]


async def test_the_cards_are_served_even_when_the_charger_is_not_there(hass, entry):
    """A cloud that is down at boot must not take the dashboard with it.

    Registering the cards used to be the last thing setup did, after the login
    and the first poll. A charger unreachable at boot raises
    ConfigEntryNotReady long before that line, so the folder the cards are
    served from was never registered -- while the Lovelace resources written on
    an earlier run still pointed into it. The browser then fetched six 404s and
    drew "Configuration error" in place of every card, and kept drawing it
    until the page was loaded again after a retry went through.

    Which is a whole dashboard broken by a charger being off its shelf.
    """
    from unittest.mock import patch

    from homeassistant.setup import async_setup_component

    from custom_components.ugreen_connect.api import UgreenError
    from custom_components.ugreen_connect.frontend import CARD_FILES, WWW_URL, fingerprint

    class _Down:
        async def login(self, *_args):
            raise UgreenError("the cloud is not answering")

    # Lovelace first, so the stub below is what the integration finds rather
    # than what the real component installs on its way up.
    assert await async_setup_component(hass, "lovelace", {})
    resources = _Resources([])
    hass.data["lovelace"] = SimpleNamespace(resources=resources)
    entry.add_to_hass(hass)
    with (
        patch("custom_components.ugreen_connect.async_get_clientsession"),
        patch("custom_components.ugreen_connect.UgreenApi", return_value=_Down()),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert [item["url"] for item in resources.async_items()] == [
        f"{WWW_URL}/{fingerprint()}/{name}" for name in CARD_FILES
    ]
    assert any(WWW_URL in str(route) for route in hass.http.app.router.resources()), (
        "the folder the cards import from has to be served, not just listed"
    )


async def test_a_card_added_by_an_update_is_registered_on_the_next_reload(hass):
    """Updating the integration is new files plus a reload, not a restart.

    The guard used to be "has this run before", which is true from the first
    boot onwards -- so a card that arrived with the update stayed out of the
    resource list until Home Assistant itself was restarted, and its config
    error looked like the update had shipped broken.
    """
    from homeassistant.setup import async_setup_component

    from custom_components.ugreen_connect import frontend
    from custom_components.ugreen_connect.frontend import (
        WWW_URL,
        async_register_card,
        fingerprint,
    )

    assert await async_setup_component(hass, "http", {})
    resources = _Resources([])
    hass.data["lovelace"] = SimpleNamespace(resources=resources)

    before = frontend.CARD_FILES
    try:
        frontend.CARD_FILES = before[:-1]  # the release before the last card
        await async_register_card(hass)
        assert len(resources.async_items()) == len(before) - 1

        frontend.CARD_FILES = before  # the update, and a reload of the entry
        await async_register_card(hass)
    finally:
        frontend.CARD_FILES = before

    assert [item["url"] for item in resources.async_items()] == [
        f"{WWW_URL}/{fingerprint()}/{name}" for name in before
    ]


async def test_a_card_that_cannot_be_registered_does_not_stop_the_charger(
    hass, entry, api, rtcx
):
    """The cards run first now, which is a new way for setup to fail.

    Whatever goes wrong while serving a file -- a frontend that is not up, a
    path that is somehow taken -- leaves a dashboard the user has to build by
    hand. Letting it out of here would leave them a charger that does not load
    at all, which is worse by every measure.
    """
    from unittest.mock import patch

    entry.add_to_hass(hass)
    with (
        patch("custom_components.ugreen_connect.async_get_clientsession"),
        patch("custom_components.ugreen_connect.UgreenApi", return_value=api),
        patch("custom_components.ugreen_connect.RtcxClient", return_value=rtcx),
        patch(
            "custom_components.ugreen_connect.async_register_card",
            side_effect=RuntimeError("the frontend is having a day"),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert _entity(hass, f"{DEVICE_CODE}_C1_power") is not None


async def test_a_changed_card_gets_a_new_address(hass, tmp_path, monkeypatch):
    """The address names the contents, so a changed file cannot hide behind
    the browser's copy of the old one.

    Home Assistant serves the folder without `Cache-Control`, and Lovelace
    loads cards with `import()` once the page is up -- a request a hard reload
    does not reliably repeat. An updated ports card sat behind its old self
    through several Cmd+Shift+R before this. Now the edit moves the url, the
    reload re-registers the resource at the new one, and the old one goes.
    """
    import shutil

    from homeassistant.setup import async_setup_component

    from custom_components.ugreen_connect import frontend

    folder = tmp_path / "www"
    shutil.copytree(frontend.FOLDER, folder)
    monkeypatch.setattr(frontend, "FOLDER", str(folder))

    assert await async_setup_component(hass, "http", {})
    resources = _Resources([])
    hass.data["lovelace"] = SimpleNamespace(resources=resources)

    await frontend.async_register_card(hass)
    first = frontend.fingerprint(str(folder))
    ports = f"{frontend.WWW_URL}/{first}/ugreen-ports-card.js"
    assert ports in [item["url"] for item in resources.async_items()]

    # The shared module alone changes: every card that imports it is new.
    shared = folder / "ugreen-ui.js"
    shared.write_text(shared.read_text() + "\n// changed\n")
    second = frontend.fingerprint(str(folder))
    assert second != first

    await frontend.async_register_card(hass)
    urls = [item["url"] for item in resources.async_items()]
    assert f"{frontend.WWW_URL}/{second}/ugreen-ports-card.js" in urls
    assert ports not in urls, "the old address was left for the browser to prefer"
    assert len(urls) == len(frontend.CARD_FILES)
