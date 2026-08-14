"""Shared entity base: which device an entity belongs to, and its metadata."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import UgreenCoordinator, device_key

# extra.onlineStatus / extra.networkStatus are 1 when up, 0 when down.
ONLINE = 1


class UgreenDeviceEntity(CoordinatorEntity[UgreenCoordinator]):
    """Common plumbing for every platform.

    Deliberately not a ``SensorEntity``: the controls derive from this too, and
    a sensor refuses to be added with a config entity category.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: UgreenCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key

    @property
    def _device(self) -> dict[str, Any]:
        for device in self.coordinator.data.get("devices", []):
            if device_key(device) == self._key:
                return device
        return {}

    @property
    def _product(self) -> dict[str, Any]:
        product = self.coordinator.data.get("detail", {}).get(self._key)
        return product if isinstance(product, dict) else {}

    @property
    def _reading(self) -> dict[str, Any] | None:
        return (self.coordinator.data.get("power") or {}).get(self._key)

    @property
    def _iot_id(self) -> str | None:
        return (self._device.get("extra") or {}).get("iotId")

    @property
    def available(self) -> bool:
        return super().available and bool(self._device)

    @property
    def device_info(self) -> DeviceInfo:
        device = self._device
        info = DeviceInfo(
            identifiers={(DOMAIN, self._key)},
            manufacturer="UGREEN",
            name=device.get("deviceName") or f"UGREEN {self._key}",
            model=self._product.get("name") or device.get("deviceName"),
            model_id=self._product.get("productNo"),
            serial_number=self._key,
        )
        if firmware := (self._reading or {}).get("firmware"):
            info["sw_version"] = firmware
        if mac := device.get("deviceMac"):
            info["connections"] = {(CONNECTION_NETWORK_MAC, mac)}
        return info
