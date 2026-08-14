"""Select platform: charging mode and wallpaper."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import UgreenConfigEntry
from .const import CHARGING_MODES, SELECTABLE_MODES
from .coordinator import UgreenCoordinator, device_key
from .entity import UgreenDeviceEntity

MODE_VALUE = {name: value for value, name in CHARGING_MODES.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UgreenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known: set[tuple[str, str]] = set()

    @callback
    def _add_new_devices() -> None:
        new: list[SelectEntity] = []
        for device in coordinator.data.get("devices", []):
            key = device_key(device)
            if key is None:
                continue
            reading = (coordinator.data.get("power") or {}).get(key)
            if not reading:
                continue
            if reading.get("charging_mode") and (key, "mode") not in known:
                known.add((key, "mode"))
                new.append(UgreenChargingMode(coordinator, key))
            if reading.get("wallpapers") and (key, "wallpaper") not in known:
                known.add((key, "wallpaper"))
                new.append(UgreenWallpaper(coordinator, key))
        if new:
            async_add_entities(new)

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


class UgreenChargingMode(UgreenDeviceEntity, SelectEntity):
    """Which power-sharing preset the charger runs."""

    _attr_name = "Charging mode"
    _attr_icon = "mdi:ev-station"
    _attr_options = list(SELECTABLE_MODES)

    def __init__(self, coordinator: UgreenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_unique_id = f"{key}_charging_mode"

    @property
    def available(self) -> bool:
        return super().available and (self._reading or {}).get("charging_mode") is not None

    @property
    def current_option(self) -> str | None:
        mode = (self._reading or {}).get("charging_mode")
        # "custom" is a real device state but not something this can set, so it
        # is reported and simply absent from the options.
        return mode if mode in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        iot_id = self._iot_id
        if not iot_id or option not in MODE_VALUE:
            return
        await self.coordinator.rtcx.async_set_charging_mode(iot_id, MODE_VALUE[option])
        if reading := self._reading:
            reading["charging_mode"] = option
        self.async_write_ha_state()


class UgreenWallpaper(UgreenDeviceEntity, SelectEntity):
    """Which stored picture the screen shows.

    The device knows its wallpapers only by six-character ids, and adding new
    ones means uploading through UGREEN's cloud, so this picks among what is
    already on the charger.
    """

    _attr_name = "Wallpaper"
    _attr_icon = "mdi:image"

    NONE = "none"

    def __init__(self, coordinator: UgreenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_unique_id = f"{key}_wallpaper"

    @property
    def available(self) -> bool:
        return super().available and bool((self._reading or {}).get("wallpapers"))

    @property
    def options(self) -> list[str]:
        return [self.NONE, *((self._reading or {}).get("wallpapers") or [])]

    @property
    def current_option(self) -> str | None:
        return (self._reading or {}).get("wallpaper") or self.NONE

    async def async_select_option(self, option: str) -> None:
        reading = self._reading or {}
        iot_id = self._iot_id
        if not iot_id:
            return
        wallpaper = None if option == self.NONE else option
        # Same block as the screensaver switch: send the current flags back so
        # picking a picture does not also turn the screensaver off.
        await self.coordinator.rtcx.async_set_screensaver(
            iot_id,
            bool(reading.get("screensaver", True)),
            reading.get("screensaver_theme", 0),
            reading.get("screensaver_flag", 0),
            wallpaper,
        )
        reading["wallpaper"] = wallpaper
        self.async_write_ha_state()
