"""Number platform: the charger's screen brightness."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE
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
    """One brightness control per charger that reports a brightness."""
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
            if not reading or reading.get("brightness") is None:
                continue
            known.add(key)
            new.append(UgreenBrightness(coordinator, key))
        if new:
            async_add_entities(new)

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


class UgreenBrightness(UgreenDeviceEntity, NumberEntity):
    """Screen brightness, 0-100."""

    _attr_name = "Screen brightness"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:brightness-6"

    def __init__(self, coordinator: UgreenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_unique_id = f"{key}_brightness"

    @property
    def _iot_id(self) -> str | None:
        return (self._device.get("extra") or {}).get("iotId")

    @property
    def available(self) -> bool:
        return super().available and (self._reading or {}).get("brightness") is not None

    @property
    def native_value(self) -> float | None:
        return (self._reading or {}).get("brightness")

    async def async_set_native_value(self, value: float) -> None:
        if not (iot_id := self._iot_id):
            return
        await self.coordinator.rtcx.async_set_brightness(iot_id, int(value))
        # Show the new value at once; the next poll confirms it from the device.
        if reading := self._reading:
            reading["brightness"] = int(value)
        self.async_write_ha_state()
