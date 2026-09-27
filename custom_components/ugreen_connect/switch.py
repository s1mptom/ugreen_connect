"""Switch platform: the charger's screensaver, and the ports its priority mode charges first."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import UgreenConfigEntry
from .const import DOMAIN
from .coordinator import UgreenCoordinator, device_key
from .entity import UgreenDeviceEntity, cloud_errors
from .protocol import PRIORITY_PORTS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UgreenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known: set[tuple[str, str]] = set()

    @callback
    def _add_new_devices() -> None:
        new: list[SwitchEntity] = []
        for device in coordinator.data.get("devices", []):
            key = device_key(device)
            if key is None:
                continue
            reading = (coordinator.data.get("power") or {}).get(key)
            if not reading:
                continue
            if reading.get("screensaver") is not None and (key, "screensaver") not in known:
                known.add((key, "screensaver"))
                new.append(UgreenScreensaver(coordinator, key))
            # On any model whose state carries the field at all, whatever mode
            # is running: the switches are unavailable outside `priority` and
            # come alive when it is chosen, rather than appearing only after
            # the charger has been seen in it.
            if "priority" in reading and (key, "priority") not in known:
                known.add((key, "priority"))
                turns = asyncio.Lock()
                new.extend(
                    UgreenPriorityPort(coordinator, key, port, turns) for port in PRIORITY_PORTS
                )
        if new:
            async_add_entities(new)

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


class UgreenScreensaver(UgreenDeviceEntity, SwitchEntity):
    """Whether the screen shows the screensaver when idle."""

    _attr_translation_key = "screensaver"
    _attr_icon = "mdi:monitor-shimmer"

    def __init__(self, coordinator: UgreenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_unique_id = f"{key}_screensaver"

    @property
    def available(self) -> bool:
        return super().available and (self._reading or {}).get("screensaver") is not None

    @property
    def is_on(self) -> bool | None:
        return (self._reading or {}).get("screensaver")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        reading = self._reading or {}
        return {
            "theme": reading.get("screensaver_theme"),
            "wallpaper": reading.get("wallpaper"),
        }

    async def _async_set(self, enabled: bool) -> None:
        reading = self._reading or {}
        iot_id = self._iot_id
        if not iot_id:
            return
        # The command carries the whole screensaver block, so the theme and the
        # chosen wallpaper have to be sent back unchanged or they get wiped.
        self._require_writable("screensaver")
        with cloud_errors():
            await self.coordinator.rtcx.async_set_screensaver(
                iot_id,
                enabled,
                reading.get("screensaver_theme", 0),
                reading.get("screensaver_flag", 0),
                reading.get("wallpaper"),
            )
        await self.coordinator.async_read_back(self._key, iot_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


class UgreenPriorityPort(UgreenDeviceEntity, SwitchEntity):
    """Whether one of C1..C3 is charged first while the priority mode runs.

    One switch a port rather than one control for the set, because that is the
    shape of the choice: the app lets any of the three be on, all three
    together included, and each is a yes or a no. Available only in `priority`
    -- under another mode the byte they read is that mode's own setting.

    The three of a charger take turns. Each writes the whole set, worked out
    from the last reading, so two presses close together -- C1 on, then C1 off
    again -- would otherwise both start from the reading before either: the
    second finds C1 already off and sends nothing, and the charger is left with
    the first. Waiting for the one before it, read back, gives the second the
    set the first left.
    """

    _attr_translation_key = "priority_port"
    _attr_icon = "mdi:priority-high"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: UgreenCoordinator, key: str, port: str, turns: asyncio.Lock
    ) -> None:
        super().__init__(coordinator, key)
        self._port = port
        self._turns = turns
        self._attr_translation_placeholders = {"port": port}
        self._attr_unique_id = f"{key}_{port}_priority"

    @property
    def _ports(self) -> list[str] | None:
        return (self._reading or {}).get("priority")

    @property
    def available(self) -> bool:
        return super().available and self._ports is not None

    @property
    def is_on(self) -> bool | None:
        ports = self._ports
        return None if ports is None else self._port in ports

    async def _async_set(self, on: bool) -> None:
        async with self._turns:
            await self._async_set_now(on)

    async def _async_set_now(self, on: bool) -> None:
        ports = self._ports
        iot_id = self._iot_id
        if ports is None or not iot_id:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="priority_not_running",
            )
        wanted = {*ports, self._port} if on else set(ports) - {self._port}
        if wanted == set(ports):
            return
        if not wanted:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="priority_needs_a_port",
            )
        self._require_writable("priority")
        with cloud_errors():
            await self.coordinator.rtcx.async_set_priority_ports(
                iot_id,
                [port for port in PRIORITY_PORTS if port in wanted],
                self.coordinator.model_for(self._key),
            )
        await self.coordinator.async_read_back(self._key, iot_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)
