"""The UGREEN Connect integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import UgreenApi, UgreenAuthError, UgreenError
from .const import (
    CONF_DEBUG_DUMP,
    CONF_REGION,
    DEFAULT_LANGUAGE,
    DEFAULT_REGION,
    MODEL_STORE_KEY,
    MODEL_STORE_VERSION,
    PARAMS_STORE_KEY,
    PARAMS_STORE_VERSION,
    REGIONS,
)
from .coordinator import UgreenCoordinator
from .frontend import async_register_card
from .image_proxy import async_register_view
from .rtcx import RtcxClient
from .services import async_register

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.UPDATE,
]

type UgreenConfigEntry = ConfigEntry[UgreenCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: UgreenConfigEntry) -> bool:
    """Log in and start polling."""
    region = entry.data.get(CONF_REGION, DEFAULT_REGION)
    session = async_get_clientsession(hass)
    api = UgreenApi(
        session,
        REGIONS.get(region, REGIONS[DEFAULT_REGION]),
        DEFAULT_LANGUAGE,
        region,
    )

    try:
        await api.login(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
    except UgreenAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except UgreenError as err:
        raise ConfigEntryNotReady(str(err)) from err

    # What each charging mode was last seen running with. Read before the
    # client is built, so the first mode change after a restart carries the
    # parameters that mode had rather than empty ones.
    params_store: Store[dict[str, str]] = Store(
        hass, PARAMS_STORE_VERSION, f"{PARAMS_STORE_KEY}.{entry.entry_id}"
    )
    # Shaped here rather than trusted: the file outlives this code and two
    # things downstream build a dict from it, so a root that is not one has to
    # stop at the door instead of raising out of the middle of setup.
    loaded_params = await params_store.async_load()
    stored_params: dict[str, str] = loaded_params if isinstance(loaded_params, dict) else {}

    # Telemetry lives behind a second cloud. Setting it up must not block the
    # entry, since the inventory sensors work without it.
    rtcx = RtcxClient(session, api, mode_params=stored_params)
    try:
        await rtcx.async_login()
    except UgreenError as err:
        _LOGGER.warning("RTCX gateway unavailable, live power disabled: %s", err)

    # What each charger was last found to be. Read before the first poll, so a
    # charger already known is named from the start rather than waited for.
    store: Store[dict[str, str]] = Store(
        hass, MODEL_STORE_VERSION, f"{MODEL_STORE_KEY}.{entry.entry_id}"
    )
    coordinator = UgreenCoordinator(
        hass,
        entry,
        api,
        rtcx,
        debug_dump=entry.data.get(CONF_DEBUG_DUMP, False),
        models=await store.async_load() or {},
        model_store=store,
        params_store=params_store,
        mode_params=stored_params,
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await async_register(hass)
    await async_register_card(hass)
    async_register_view(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # The poll interval is fixed when the coordinator is built, so a changed
    # option only takes effect once the entry is set up again.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: UgreenConfigEntry) -> None:
    """Anything written to the entry is a choice, so it always takes a reload.

    The region and the debug flag are read out of `entry.data` when this is set
    up and nowhere else; the poll interval out of `entry.options` when the
    coordinator is built. None of them can change without one.
    """
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: UgreenConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: UgreenConfigEntry) -> None:
    """Take what was remembered with the account it belongs to."""
    store: Store[dict[str, str]] = Store(
        hass, MODEL_STORE_VERSION, f"{MODEL_STORE_KEY}.{entry.entry_id}"
    )
    await store.async_remove()
    params_store: Store[dict[str, str]] = Store(
        hass, PARAMS_STORE_VERSION, f"{PARAMS_STORE_KEY}.{entry.entry_id}"
    )
    await params_store.async_remove()
