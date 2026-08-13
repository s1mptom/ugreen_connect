"""Sensor platform for UGREEN Connect.

Two kinds of entity are published: the account's device inventory with its
online state, and -- for chargers that answer the RTCX gateway's binary
``PT_data`` protocol -- live voltage, current and power for every port.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import UgreenConfigEntry
from .const import DOMAIN, X783_PORTS
from .coordinator import UgreenCoordinator, device_key

# extra.onlineStatus / extra.networkStatus are 1 when up, 0 when down.
ONLINE = 1

# The report always carries all eight slots; ports the hardware does not have
# simply read zero forever, so only those seen powered are worth an entity.
MEASUREMENTS: dict[str, tuple[SensorDeviceClass, str, int]] = {
    "power": (SensorDeviceClass.POWER, UnitOfPower.WATT, 1),
    "voltage": (SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT, 1),
    "current": (SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE, 1),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UgreenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up connectivity and, where available, live power sensors."""
    coordinator = entry.runtime_data
    known: set[str] = set()
    known_ports: set[tuple[str, str]] = set()

    @callback
    def _add_new_devices() -> None:
        new: list[SensorEntity] = []
        for device in coordinator.data.get("devices", []):
            key = device_key(device)
            if key is None:
                continue
            if key not in known:
                known.add(key)
                new.append(UgreenStatusSensor(coordinator, key))

            reading = (coordinator.data.get("power") or {}).get(key)
            if not reading:
                continue
            if (key, "total") not in known_ports:
                known_ports.add((key, "total"))
                new.append(UgreenTotalPowerSensor(coordinator, key))
            # A port is only worth an entity once it has actually shown a
            # voltage; the report pads unused slots with zeroes.
            for port in X783_PORTS:
                values = reading["ports"].get(port) or {}
                if not any(values.values()) or (key, port) in known_ports:
                    continue
                known_ports.add((key, port))
                new.extend(
                    UgreenPortSensor(coordinator, key, port, kind)
                    for kind in MEASUREMENTS
                )
        if new:
            async_add_entities(new)

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


class UgreenDeviceEntity(CoordinatorEntity[UgreenCoordinator], SensorEntity):
    """Shared plumbing: which device this entity belongs to, and its metadata."""

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
        if mac := device.get("deviceMac"):
            info["connections"] = {(CONNECTION_NETWORK_MAC, mac)}
        return info

    @property
    def _reading(self) -> dict[str, Any] | None:
        return (self.coordinator.data.get("power") or {}).get(self._key)


class UgreenStatusSensor(UgreenDeviceEntity):
    """Cloud connectivity state of one bound UGREEN device."""

    _attr_translation_key = "status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["online", "offline"]
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: UgreenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_unique_id = f"{key}_status"

    @property
    def native_value(self) -> str:
        extra = self._device.get("extra") or {}
        return "online" if extra.get("onlineStatus") == ONLINE else "offline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        device = self._device
        extra = device.get("extra") or {}
        return {
            "device_type": device.get("deviceType"),
            "product_serial_no": device.get("productSerialNo"),
            "product_key": self._product.get("productKey"),
            "iot_id": extra.get("iotId"),
            "network_connected": extra.get("networkStatus") == ONLINE,
            "mac": device.get("deviceMac"),
        }


class UgreenPortSensor(UgreenDeviceEntity):
    """Voltage, current or power of a single charging port."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: UgreenCoordinator, key: str, port: str, kind: str
    ) -> None:
        super().__init__(coordinator, key)
        self._port = port
        self._kind = kind
        device_class, unit, digits = MEASUREMENTS[kind]
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_suggested_display_precision = digits
        self._attr_name = f"{port} {kind}"
        self._attr_unique_id = f"{key}_{port}_{kind}"

    @property
    def available(self) -> bool:
        return super().available and self._reading is not None

    @property
    def native_value(self) -> float | None:
        reading = self._reading
        if not reading:
            return None
        return (reading["ports"].get(self._port) or {}).get(self._kind)


class UgreenTotalPowerSensor(UgreenDeviceEntity):
    """Combined output of every port."""

    _attr_name = "Total power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: UgreenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_unique_id = f"{key}_total_power"

    @property
    def available(self) -> bool:
        return super().available and self._reading is not None

    @property
    def native_value(self) -> float | None:
        reading = self._reading
        return reading["total"] if reading else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        reading = self._reading or {}
        return {
            "work_mode": reading.get("work_mode"),
            "updated": reading.get("updated"),
        }
