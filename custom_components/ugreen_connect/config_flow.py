"""Config flow for UGREEN Connect."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import UgreenApi, UgreenAuthError, UgreenError
from .const import (
    CONF_DEBUG_DUMP,
    CONF_REGION,
    DEFAULT_LANGUAGE,
    DEFAULT_REGION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    REGIONS,
)

_LOGGER = logging.getLogger(__name__)

EMAIL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL))
PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): EMAIL_SELECTOR,
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
        vol.Required(CONF_REGION, default=DEFAULT_REGION): SelectSelector(
            SelectSelectorConfig(
                options=list(REGIONS),
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="region",
            )
        ),
        # Off by default: it writes the raw cloud payload, device ids included,
        # next to configuration.yaml on every refresh.
        vol.Required(CONF_DEBUG_DUMP, default=False): bool,
    }
)


OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): NumberSelector(
            NumberSelectorConfig(
                min=MIN_SCAN_INTERVAL,
                max=MAX_SCAN_INTERVAL,
                step=1,
                unit_of_measurement="s",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Required(CONF_REGION, default=DEFAULT_REGION): SelectSelector(
            SelectSelectorConfig(
                options=list(REGIONS),
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="region",
            )
        ),
        vol.Required(CONF_DEBUG_DUMP, default=False): bool,
    }
)


class UgreenConnectConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the UgreenConnect account and verify it against the cloud."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry_data: Mapping[str, Any] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> UgreenOptionsFlow:
        return UgreenOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            region = user_input[CONF_REGION]
            api = UgreenApi(
                async_get_clientsession(self.hass), REGIONS[region], DEFAULT_LANGUAGE
            )
            try:
                await api.login(email, user_input[CONF_PASSWORD])
            except UgreenAuthError:
                errors["base"] = "invalid_auth"
            except UgreenError as err:
                _LOGGER.debug("Cannot connect to UGREEN cloud: %s", err)
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(email.lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=email,
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_REGION: region,
                        CONF_DEBUG_DUMP: user_input[CONF_DEBUG_DUMP],
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        self._reauth_entry_data = entry_data
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            region = entry.data[CONF_REGION]
            api = UgreenApi(
                async_get_clientsession(self.hass), REGIONS[region], DEFAULT_LANGUAGE
            )
            try:
                await api.login(entry.data[CONF_EMAIL], user_input[CONF_PASSWORD])
            except UgreenAuthError:
                errors["base"] = "invalid_auth"
            except UgreenError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )


class UgreenOptionsFlow(OptionsFlow):
    """Change how often the cloud is polled, and which region it is asked.

    Region lives here as well as in the initial form because an account moved to
    another server would otherwise mean deleting the entry and setting it up
    again; the credentials are re-checked against the new region before the
    change is kept.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self.config_entry

        if user_input is not None:
            region = user_input[CONF_REGION]
            if region != entry.data.get(CONF_REGION, DEFAULT_REGION):
                api = UgreenApi(
                    async_get_clientsession(self.hass), REGIONS[region], DEFAULT_LANGUAGE
                )
                try:
                    await api.login(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
                except UgreenAuthError:
                    errors["base"] = "invalid_auth"
                except UgreenError as err:
                    _LOGGER.debug("Cannot reach region %s: %s", region, err)
                    errors["base"] = "cannot_connect"
            if not errors:
                # Region and the dump flag are read from `data`, so they are
                # written back there and only the interval lives in options.
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={
                        **entry.data,
                        CONF_REGION: region,
                        CONF_DEBUG_DUMP: user_input[CONF_DEBUG_DUMP],
                    },
                )
                return self.async_create_entry(
                    data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
                )

        current = {
            CONF_SCAN_INTERVAL: entry.options.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
            ),
            CONF_REGION: entry.data.get(CONF_REGION, DEFAULT_REGION),
            CONF_DEBUG_DUMP: entry.data.get(CONF_DEBUG_DUMP, False),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(OPTIONS_SCHEMA, current),
            errors=errors,
        )
