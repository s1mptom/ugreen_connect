"""Number platform: the charger's screen brightness."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import UgreenConfigEntry
from .coordinator import UgreenCoordinator, device_key
from .entity import UgreenDeviceEntity


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
        new: list[NumberEntity] = []
        for device in coordinator.data.get("devices", []):
            key = device_key(device)
            if key is None or key in known:
                continue
            reading = (coordinator.data.get("power") or {}).get(key)
            if not reading or reading.get("brightness") is None:
                continue
            known.add(key)
            new.append(UgreenBrightness(coordinator, key))
            if reading.get("sleep_time") is not None:
                new.append(UgreenSleepTime(coordinator, key))
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


class UgreenSleepTime(UgreenDeviceEntity, NumberEntity):
    """How long the screen stays awake, in minutes.

    The app offers 1, 5, 10, 30 and "Always On", which write exactly those
    minutes and zero -- so zero here means the screen never sleeps. Any value up
    to 255 is accepted, the presets are just what the app shows.
    """

    _attr_name = "Screen sleep timeout"
    _attr_native_min_value = 0
    _attr_native_max_value = 255
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:monitor-off"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: UgreenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_unique_id = f"{key}_sleep_time"


    @property
    def available(self) -> bool:
        return super().available and (self._reading or {}).get("sleep_time") is not None

    @property
    def native_value(self) -> float | None:
        return (self._reading or {}).get("sleep_time")

    async def async_set_native_value(self, value: float) -> None:
        if not (iot_id := self._iot_id):
            return
        await self.coordinator.rtcx.async_set_sleep_time(iot_id, int(value))
        if reading := self._reading:
            reading["sleep_time"] = int(value)
        self.async_write_ha_state()
