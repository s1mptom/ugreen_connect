"""Update platform: the charger's firmware.

What is offered comes from the account API, asked with the MCU version the
charger reports -- the question the UGREEN app asks. Installing is the app's
own path, watched whole on an X783 going from 1.2.1 to 1.2.3, and is offered
only on models where it was.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import UgreenConfigEntry
from .coordinator import UgreenCoordinator, device_key
from .entity import UgreenDeviceEntity
from .protocol import FIRMWARE_INSTALL_MODELS

# Home Assistant shows at most this much of the summary.
SUMMARY_LIMIT = 255


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
    """Installed firmware, what the cloud offers, and installing it."""

    _attr_translation_key = "firmware"

    def __init__(self, coordinator: UgreenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_unique_id = f"{key}_firmware"

    @property
    def _offer(self) -> dict[str, Any] | None:
        return (self._reading or {}).get("firmware_offer")

    @property
    def supported_features(self) -> UpdateEntityFeature:
        features = UpdateEntityFeature.RELEASE_NOTES
        if self.coordinator.model_for(self._key) in FIRMWARE_INSTALL_MODELS:
            features |= UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
        return features

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
        offer = self._offer
        return offer["version"] if offer else self.installed_version

    @property
    def release_summary(self) -> str | None:
        notes = (self._offer or {}).get("notes")
        return notes[:SUMMARY_LIMIT] if notes else None

    async def async_release_notes(self) -> str | None:
        return (self._offer or {}).get("notes")

    @property
    def in_progress(self) -> bool:
        return self._key in self.coordinator.installs

    @property
    def update_percentage(self) -> int | None:
        install = self.coordinator.installs.get(self._key)
        # Nothing before the charger says it has started: it is still fetching
        # the file, and a 0% bar reads as stuck.
        return install["progress"] if install and install["progress"] else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        offer = self._offer or {}
        return {"size": offer.get("size"), "published": offer.get("published")}

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        # Only ever the version on offer: without SPECIFIC_VERSION, Home
        # Assistant refuses any other before this is called.
        await self.coordinator.async_install_firmware(self._key, self._device)
