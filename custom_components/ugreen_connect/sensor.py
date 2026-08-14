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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import UgreenConfigEntry
from .const import DOMAIN, HANDSHAKE_PROTOCOL, X783_PORTS
from .coordinator import UgreenCoordinator, device_key
from .entity import ONLINE, UgreenDeviceEntity

# The report always carries all eight slots.
MEASUREMENTS: dict[str, tuple[SensorDeviceClass, str, int]] = {
    "power": (SensorDeviceClass.POWER, UnitOfPower.WATT, 1),
    "voltage": (SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT, 1),
    "current": (SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE, 1),
}

# Every port the report carries gets entities, DC included: which sockets a
# given model actually has is not something this can know, and a port nobody
# uses simply reads zero.
ALWAYS_PORTS: tuple[str, ...] = X783_PORTS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UgreenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up connectivity and, where available, live power sensors."""
    coordinator = entry.runtime_data
    known: set[str] = set()
    known_ports: set[tuple[str, str]] = set()

    # A port only reveals itself by drawing power, but once it has, its entities
    # should stay put -- otherwise unplugging a cable makes them vanish on the
    # next restart, taking their history with them. The registry remembers.
    registry = er.async_get(hass)
    seen_before = {
        (key, port)
        for key in {device_key(d) for d in coordinator.data.get("devices", [])}
        if key
        for port in X783_PORTS
        if registry.async_get_entity_id("sensor", DOMAIN, f"{key}_{port}_power")
    }

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
            # Every real port of the device gets its entities up front, so the
            # dashboard shows the full layout from the start rather than waiting
            # for a port to happen to be drawing power during a poll. DC is the
            # exception: it only matters when something is actually plugged in.
            for port in X783_PORTS:
                values = reading["ports"].get(port) or {}
                live = any(v for k, v in values.items() if k in MEASUREMENTS)
                always = port in ALWAYS_PORTS
                if (
                    (not live and not always and (key, port) not in seen_before)
                    or (key, port) in known_ports
                ):
                    continue
                known_ports.add((key, port))
                new.extend(
                    UgreenPortSensor(coordinator, key, port, kind)
                    for kind in MEASUREMENTS
                )
                new.append(UgreenPortProtocolSensor(coordinator, key, port))
        if new:
            async_add_entities(new)

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


class UgreenStatusSensor(UgreenDeviceEntity, SensorEntity):
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


class UgreenPortSensor(UgreenDeviceEntity, SensorEntity):
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
        # Named through a placeholder so a translation only has to give the
        # word, not one entry per port.
        self._attr_translation_key = f"port_{kind}"
        self._attr_translation_placeholders = {"port": port}
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


class UgreenPortProtocolSensor(UgreenDeviceEntity, SensorEntity):
    """Fast-charge protocol a port negotiated with whatever is plugged into it."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = sorted(set(HANDSHAKE_PROTOCOL.values()) | {"unknown"})
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: UgreenCoordinator, key: str, port: str) -> None:
        super().__init__(coordinator, key)
        self._port = port
        self._attr_translation_key = "port_protocol"
        self._attr_translation_placeholders = {"port": port}
        self._attr_unique_id = f"{key}_{port}_protocol"

    @property
    def available(self) -> bool:
        return super().available and self._reading is not None

    @property
    def native_value(self) -> str | None:
        reading = self._reading
        if not reading:
            return None
        return (reading["ports"].get(self._port) or {}).get("protocol")


class UgreenTotalPowerSensor(UgreenDeviceEntity, SensorEntity):
    """Combined output of every port."""

    _attr_translation_key = "total_power"
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
            "firmware": reading.get("firmware"),
            "ssid": reading.get("ssid"),
        }
