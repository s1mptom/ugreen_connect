"""Update platform: the charger's firmware.

Read-only. The device is told to install by the cloud, not by us, and that path
was never observed, so this reports rather than acts.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import UgreenConfigEntry
from .coordinator import UgreenCoordinator, device_key
from .entity import UgreenDeviceEntity


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
            if not reading or not reading.get("firmware"):
                continue
            known.add(key)
            new.append(UgreenFirmware(coordinator, key))
        if new:
            async_add_entities(new)

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


class UgreenFirmware(UgreenDeviceEntity, UpdateEntity):
    """Installed firmware, and whether the cloud is offering a newer one."""

    _attr_translation_key = "firmware"
    _attr_supported_features = UpdateEntityFeature(0)

    def __init__(self, coordinator: UgreenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_unique_id = f"{key}_firmware"

    @property
    def available(self) -> bool:
        return super().available and bool((self._reading or {}).get("firmware"))

    @property
    def installed_version(self) -> str | None:
        return (self._reading or {}).get("firmware")

    @property
    def latest_version(self) -> str | None:
        # No offer means the charger is current -- saying so requires reporting
        # the installed version, since a null here reads as "unknown" instead.
        offered = ((self._reading or {}).get("ota") or {}).get("available")
        return offered or self.installed_version

    @property
    def in_progress(self) -> bool:
        progress = ((self._reading or {}).get("ota") or {}).get("progress")
        return progress is not None and 0 < progress < 100

    @property
    def update_percentage(self) -> int | None:
        if not self.in_progress:
            return None
        return ((self._reading or {}).get("ota") or {}).get("progress")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ota = (self._reading or {}).get("ota") or {}
        return {"module": ota.get("module"), "size": ota.get("size")}
