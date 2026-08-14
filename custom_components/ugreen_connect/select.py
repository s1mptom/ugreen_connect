"""Select platform: charging mode and wallpaper."""

from __future__ import annotations

import asyncio

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import UgreenConfigEntry
from .const import (
    CHARGING_MODES,
    CLOCK_STYLES,
    PICTURE_SETTLE_SECONDS,
    SELECTABLE_MODES,
    SLEEP_OPTIONS,
    TIME_FORMATS,
)
from .coordinator import UgreenCoordinator, device_key
from .entity import UgreenDeviceEntity

MODE_VALUE = {name: value for value, name in CHARGING_MODES.items()}
CLOCK_STYLE_VALUE = {name: value for value, name in CLOCK_STYLES.items()}
TIME_FORMAT_VALUE = {name: value for value, name in TIME_FORMATS.items()}


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
            if reading.get("sleep_time") is not None and (key, "sleep") not in known:
                known.add((key, "sleep"))
                new.append(UgreenSleepTime(coordinator, key))
            if reading.get("charging_mode") and (key, "mode") not in known:
                known.add((key, "mode"))
                new.append(UgreenChargingMode(coordinator, key))
            if reading.get("wallpapers") and (key, "wallpaper") not in known:
                known.add((key, "wallpaper"))
                new.append(UgreenWallpaper(coordinator, key))
            # The clock options only exist alongside the screensaver state.
            if reading.get("screensaver_theme") is not None:
                if (key, "clock_style") not in known:
                    known.add((key, "clock_style"))
                    new.append(UgreenClockStyle(coordinator, key))
                if (key, "time_format") not in known:
                    known.add((key, "time_format"))
                    new.append(UgreenTimeFormat(coordinator, key))
        if new:
            async_add_entities(new)

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


class UgreenChargingMode(UgreenDeviceEntity, SelectEntity):
    """Which power-sharing preset the charger runs."""

    _attr_translation_key = "charging_mode"
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

    _attr_translation_key = "wallpaper"
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
        """Everything on the device, plus anything the account offers.

        The two sets only partly overlap: the charger holds the pictures it has
        downloaded, while the library moves on without it.
        """
        reading = self._reading or {}
        on_device = list(reading.get("wallpapers") or [])
        offered = [w["id"] for w in reading.get("wallpaper_list") or [] if w.get("id")]
        seen = dict.fromkeys([*on_device, *offered])
        return [self.NONE, *seen]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # The card renders previews from this; the device only knows ids.
        return {"wallpapers": (self._reading or {}).get("wallpaper_list") or []}

    @property
    def current_option(self) -> str | None:
        return (self._reading or {}).get("wallpaper") or self.NONE

    async def async_select_option(self, option: str) -> None:
        reading = self._reading or {}
        iot_id = self._iot_id
        if not iot_id:
            return
        wallpaper = None if option == self.NONE else option

        # A picture the charger has never downloaded has to be handed over
        # first, or the screensaver would point at something it does not have
        # and the screen would simply keep what it was showing.
        if wallpaper and wallpaper not in (reading.get("wallpapers") or []):
            offer = next(
                (w for w in reading.get("wallpaper_list") or [] if w.get("id") == wallpaper),
                None,
            )
            if offer and offer.get("url"):
                await self.coordinator.rtcx.async_set_picture(
                    iot_id, offer["url"], offer.get("size") or 0, wallpaper,
                    stock=bool(offer.get("stock")),
                )
                await asyncio.sleep(PICTURE_SETTLE_SECONDS)

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


class _UgreenScreensaverOption(UgreenDeviceEntity, SelectEntity):
    """Base for the clock options carried in the screensaver frame.

    Each writes back the current screensaver on/off, both option bytes and the
    wallpaper, changing only its own byte -- otherwise setting one would reset
    the others.
    """

    async def _send(self, *, theme: int | None = None, flag: int | None = None) -> None:
        reading = self._reading or {}
        iot_id = self._iot_id
        if not iot_id:
            return
        await self.coordinator.rtcx.async_set_screensaver(
            iot_id,
            bool(reading.get("screensaver", True)),
            reading.get("screensaver_theme", 0) if theme is None else theme,
            reading.get("screensaver_flag", 0) if flag is None else flag,
            reading.get("wallpaper"),
        )
        if theme is not None:
            reading["screensaver_theme"] = theme
        if flag is not None:
            reading["screensaver_flag"] = flag
        self.async_write_ha_state()


class UgreenClockStyle(_UgreenScreensaverOption):
    """Which of the two clock faces the screensaver draws."""

    _attr_translation_key = "clock_style"
    _attr_icon = "mdi:clock-outline"
    _attr_options = list(CLOCK_STYLES.values())

    def __init__(self, coordinator: UgreenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_unique_id = f"{key}_clock_style"

    @property
    def available(self) -> bool:
        return super().available and (self._reading or {}).get("screensaver_flag") is not None

    @property
    def current_option(self) -> str | None:
        return CLOCK_STYLES.get((self._reading or {}).get("screensaver_flag"))

    async def async_select_option(self, option: str) -> None:
        if option in CLOCK_STYLE_VALUE:
            await self._send(flag=CLOCK_STYLE_VALUE[option])


class UgreenTimeFormat(_UgreenScreensaverOption):
    """12- or 24-hour clock on the screensaver."""

    _attr_translation_key = "time_format"
    _attr_icon = "mdi:clock-digital"
    _attr_options = list(TIME_FORMATS.values())

    def __init__(self, coordinator: UgreenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_unique_id = f"{key}_time_format"

    @property
    def available(self) -> bool:
        return super().available and (self._reading or {}).get("screensaver_theme") is not None

    @property
    def current_option(self) -> str | None:
        return TIME_FORMATS.get((self._reading or {}).get("screensaver_theme"))

    async def async_select_option(self, option: str) -> None:
        if option in TIME_FORMAT_VALUE:
            await self._send(theme=TIME_FORMAT_VALUE[option])


class UgreenSleepTime(UgreenDeviceEntity, SelectEntity):
    """How long the screen stays awake, offering the app's own choices.

    The device counts plain minutes and takes any value, but matching the app
    keeps the two in step; "always_on" is the zero it writes for never sleeping.
    """

    _attr_translation_key = "screen_off_time"
    _attr_icon = "mdi:monitor-off"
    _attr_options = list(SLEEP_OPTIONS)

    def __init__(self, coordinator: UgreenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_unique_id = f"{key}_sleep_time_select"

    @property
    def available(self) -> bool:
        return super().available and (self._reading or {}).get("sleep_time") is not None

    @property
    def current_option(self) -> str | None:
        minutes = (self._reading or {}).get("sleep_time")
        # A value set outside the app has no matching choice; report nothing
        # rather than pretend it is one of them.
        return next((name for name, m in SLEEP_OPTIONS.items() if m == minutes), None)

    async def async_select_option(self, option: str) -> None:
        iot_id = self._iot_id
        if not iot_id or option not in SLEEP_OPTIONS:
            return
        await self.coordinator.rtcx.async_set_sleep_time(iot_id, SLEEP_OPTIONS[option])
        if reading := self._reading:
            reading["sleep_time"] = SLEEP_OPTIONS[option]
        self.async_write_ha_state()
