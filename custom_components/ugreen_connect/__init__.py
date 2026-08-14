"""The UGREEN Connect integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import UgreenApi, UgreenAuthError, UgreenError
from .const import CONF_DEBUG_DUMP, CONF_REGION, DEFAULT_LANGUAGE, DEFAULT_REGION, REGIONS
from .coordinator import UgreenCoordinator
from .rtcx import RtcxClient
from .services import async_register

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
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

    # Telemetry lives behind a second cloud. Setting it up must not block the
    # entry, since the inventory sensors work without it.
    rtcx = RtcxClient(session, api)
    try:
        await rtcx.async_login()
    except UgreenError as err:
        _LOGGER.warning("RTCX gateway unavailable, live power disabled: %s", err)

    coordinator = UgreenCoordinator(
        hass, entry, api, rtcx, debug_dump=entry.data.get(CONF_DEBUG_DUMP, True)
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await async_register(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: UgreenConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
