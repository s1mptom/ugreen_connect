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
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import logsafe
from .api import UgreenApi, UgreenAuthError, UgreenError
from .const import (
    CONF_CHARGERS,
    CONF_DEBUG_DUMP,
    CONF_EFFICIENCY,
    CONF_IDLE_END,
    CONF_NOMINAL_VOLTAGE,
    CONF_OFFERED,
    CONF_REGION,
    DEFAULT_EFFICIENCY,
    DEFAULT_IDLE_END,
    DEFAULT_LANGUAGE,
    DEFAULT_NOMINAL_VOLTAGE,
    DEFAULT_REGION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    REGIONS,
)
from .coordinator import device_key

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
        vol.Required(
            CONF_NOMINAL_VOLTAGE, default=DEFAULT_NOMINAL_VOLTAGE
        ): NumberSelector(
            NumberSelectorConfig(
                min=1, max=30, step=0.05, unit_of_measurement="V",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Required(CONF_EFFICIENCY, default=DEFAULT_EFFICIENCY): NumberSelector(
            NumberSelectorConfig(
                min=50, max=100, step=1, unit_of_measurement="%",
                mode=NumberSelectorMode.SLIDER,
            )
        ),
        vol.Required(CONF_IDLE_END, default=DEFAULT_IDLE_END): NumberSelector(
            NumberSelectorConfig(
                min=5, max=1440, step=5, unit_of_measurement="min",
                mode=NumberSelectorMode.BOX,
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
        vol.Required(
            CONF_NOMINAL_VOLTAGE, default=DEFAULT_NOMINAL_VOLTAGE
        ): NumberSelector(
            NumberSelectorConfig(
                min=1, max=30, step=0.05, unit_of_measurement="V",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Required(CONF_EFFICIENCY, default=DEFAULT_EFFICIENCY): NumberSelector(
            NumberSelectorConfig(
                min=50, max=100, step=1, unit_of_measurement="%",
                mode=NumberSelectorMode.SLIDER,
            )
        ),
        vol.Required(CONF_IDLE_END, default=DEFAULT_IDLE_END): NumberSelector(
            NumberSelectorConfig(
                min=5, max=1440, step=5, unit_of_measurement="min",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Required(CONF_DEBUG_DUMP, default=False): bool,
    }
)


def charger_selector(devices: list[dict[str, Any]]) -> SelectSelector:
    """The account's chargers as a list to tick, one row each.

    Named as the app names them. Two chargers with the same name -- a pair of
    X783s nobody renamed -- also get the end of their unit code, so the rows
    can be told apart. That is shown in the form only, never logged.
    """
    names = [d.get("deviceName") or "UGREEN" for d in devices]
    options = [
        SelectOptionDict(
            value=str(device_key(d)),
            label=f"{name} (…{str(device_key(d))[-4:]})" if names.count(name) > 1 else name,
        )
        for d, name in zip(devices, names, strict=True)
    ]
    return SelectSelector(
        SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.LIST)
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
            # Before anything can be logged about the attempt (see logsafe).
            logsafe.install(__package__)
            logsafe.remember(email, "<account>")
            logsafe.remember(user_input[CONF_PASSWORD], "<password>")
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
                self._data = {
                    CONF_EMAIL: email,
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_REGION: region,
                    CONF_DEBUG_DUMP: user_input[CONF_DEBUG_DUMP],
                }
                # Home Assistant's own division: `data` is what the connection
                # needs, `options` everything else. These three tune the
                # session sensors and are read from `options` throughout, so
                # collecting them here and writing them to neither is how
                # somebody who set 77% silently got 90%. Cast as the options
                # dialog casts: a number selector hands back floats either
                # way, and these sit beside its writes.
                self._options = {
                    CONF_NOMINAL_VOLTAGE: float(user_input[CONF_NOMINAL_VOLTAGE]),
                    CONF_EFFICIENCY: int(user_input[CONF_EFFICIENCY]),
                    CONF_IDLE_END: int(user_input[CONF_IDLE_END]),
                }
                # Which chargers to add, when there is a choice. A list that
                # cannot be had now is no reason to stop: the entry is made
                # with every charger, as entries were before the choice.
                try:
                    devices = await api.get_devices()
                except UgreenError as err:
                    _LOGGER.debug("Could not list the account's chargers: %s", err)
                    devices = []
                self._chargers = [d for d in devices if device_key(d)]
                if len(self._chargers) > 1:
                    return await self.async_step_chargers()
                keys = [str(device_key(d)) for d in self._chargers]
                if keys:
                    self._options |= {CONF_CHARGERS: keys, CONF_OFFERED: keys}
                return self.async_create_entry(
                    title=email, data=self._data, options=self._options
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_chargers(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The account's chargers, to add one, several or all of them.

        Each is read as its own model, which the account API names -- nothing
        to choose there. Every charger is ticked to start with, since adding
        all of them is the likely wish.
        """
        errors: dict[str, str] = {}
        keys = [str(device_key(d)) for d in self._chargers]
        if user_input is not None:
            chosen = [key for key in keys if key in user_input.get(CONF_CHARGERS, [])]
            if not chosen:
                errors["base"] = "no_charger"
            else:
                return self.async_create_entry(
                    title=self._data[CONF_EMAIL],
                    data=self._data,
                    options=self._options | {CONF_CHARGERS: chosen, CONF_OFFERED: keys},
                )
        return self.async_show_form(
            step_id="chargers",
            data_schema=vol.Schema(
                {vol.Required(CONF_CHARGERS, default=keys): charger_selector(self._chargers)}
            ),
            errors=errors,
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
            # The new password, before the cloud can say it back (see logsafe).
            logsafe.remember(user_input[CONF_PASSWORD], "<password>")
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
        # The whole account as the running entry last saw it, so a charger that
        # was left out can be added here. Not loaded, there is no list to offer
        # and the field is left off.
        coordinator = getattr(entry, "runtime_data", None)
        devices: list[dict[str, Any]] = list(getattr(coordinator, "account_devices", []))
        # On a submit, what the form showed -- not the account as it is now: a
        # charger bound while the form was open was never offered.
        offered = getattr(self, "_offered", None) or [str(device_key(d)) for d in devices]

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
            chosen: list[str] | None = None
            if devices and CONF_CHARGERS in user_input:
                chosen = [key for key in offered if key in user_input[CONF_CHARGERS]]
                if not chosen:
                    errors["base"] = "no_charger"
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
                options = {
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_NOMINAL_VOLTAGE: float(user_input[CONF_NOMINAL_VOLTAGE]),
                    CONF_EFFICIENCY: int(user_input[CONF_EFFICIENCY]),
                    CONF_IDLE_END: int(user_input[CONF_IDLE_END]),
                }
                # The choice of chargers, if it was on the form; what it was
                # before, if it was not.
                if chosen is not None:
                    options |= {CONF_CHARGERS: chosen, CONF_OFFERED: offered}
                else:
                    options |= {
                        key: entry.options[key]
                        for key in (CONF_CHARGERS, CONF_OFFERED)
                        if key in entry.options
                    }
                return self.async_create_entry(data=options)

        current = {
            CONF_SCAN_INTERVAL: entry.options.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
            ),
            CONF_NOMINAL_VOLTAGE: entry.options.get(
                CONF_NOMINAL_VOLTAGE, DEFAULT_NOMINAL_VOLTAGE
            ),
            CONF_EFFICIENCY: entry.options.get(CONF_EFFICIENCY, DEFAULT_EFFICIENCY),
            CONF_IDLE_END: entry.options.get(CONF_IDLE_END, DEFAULT_IDLE_END),
            CONF_REGION: entry.data.get(CONF_REGION, DEFAULT_REGION),
            CONF_DEBUG_DUMP: entry.data.get(CONF_DEBUG_DUMP, False),
        }
        self._offered = offered
        schema = OPTIONS_SCHEMA
        if devices:
            schema = schema.extend({vol.Required(CONF_CHARGERS): charger_selector(devices)})
            current[CONF_CHARGERS] = [
                key for key in offered if key in entry.options.get(CONF_CHARGERS, offered)
            ]
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, current),
            errors=errors,
        )
