"""Switch platform: the charger's screensaver."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import UgreenConfigEntry
from .coordinator import UgreenCoordinator, device_key
from .sensor import UgreenDeviceEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UgreenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_devices() -> None:
        new = []
        for device in coordinator.data.get("devices", []):
            key = device_key(device)
            if key is None or key in known:
                continue
            reading = (coordinator.data.get("power") or {}).get(key)
            if not reading or reading.get("screensaver") is None:
                continue
            known.add(key)
            new.append(UgreenScreensaver(coordinator, key))
        if new:
            async_add_entities(new)

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


class UgreenScreensaver(UgreenDeviceEntity, SwitchEntity):
    """Whether the screen shows the screensaver when idle."""

    _attr_name = "Screensaver"
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
        iot_id = (self._device.get("extra") or {}).get("iotId")
        if not iot_id:
            return
        # The command carries the whole screensaver block, so the theme and the
        # chosen wallpaper have to be sent back unchanged or they get wiped.
        await self.coordinator.rtcx.async_set_screensaver(
            iot_id,
            enabled,
            reading.get("screensaver_theme", 0),
            reading.get("screensaver_flag", 0),
            reading.get("wallpaper"),
        )
        reading["screensaver"] = enabled
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)
