"""Config flow for UGREEN Connect."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
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
    DOMAIN,
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
        vol.Required(CONF_DEBUG_DUMP, default=True): bool,
    }
)


class UgreenConnectConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the UgreenConnect account and verify it against the cloud."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry_data: Mapping[str, Any] | None = None

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
