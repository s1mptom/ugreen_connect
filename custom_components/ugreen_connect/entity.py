"""Shared entity base: which device an entity belongs to, and its metadata."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import UgreenError
from .const import DOMAIN
from .coordinator import UgreenCoordinator, device_key
from .protocol import state_writable

# extra.onlineStatus / extra.networkStatus are 1 when up, 0 when down.
ONLINE = 1


@contextmanager
def cloud_errors() -> Iterator[None]:
    """Say what went wrong, rather than letting a trace say it badly.

    A polling failure is the coordinator's business and belongs in the log. A
    failure while somebody is pressing a button is theirs -- and Home Assistant
    only shows it to them if it arrives as a HomeAssistantError. Anything else
    is logged as an unexpected exception and reaches the person as a notice
    that something went wrong somewhere. The messages this wraps are usually
    the actionable kind: the cloud is refusing requests, the account is signed
    in somewhere else.
    """
    try:
        yield
    except UgreenError as err:
        raise HomeAssistantError(str(err)) from err


class UgreenDeviceEntity(CoordinatorEntity[UgreenCoordinator]):
    """Common plumbing for every platform.

    Deliberately not a ``SensorEntity``: the controls derive from this too, and
    a sensor refuses to be added with a config entity category.
    """

    _attr_has_entity_name = True
    # The port an entity is about, where it is about one. Published as an
    # attribute, because that is how the dashboard cards find a port's
    # entities: the entity id is made from the translated name --
    # `sensor.<device>_c1_leistung` on a German install -- and is the owner's
    # to rename besides. The registry's translation key says which kind of
    # entity it is; this says which port.
    _port: str | None = None

    def __init__(self, coordinator: UgreenCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key

    @property
    def capability_attributes(self) -> dict[str, Any] | None:
        # A capability rather than an extra attribute, which Home Assistant
        # drops while an entity is unavailable: a charger offline, or a
        # priority switch outside `priority`, still has to be recognisable
        # as that port's. Added to what the platform declares, never instead
        # of it -- a sensor's state class travels the same way.
        capabilities = super().capability_attributes
        if not self._port:
            return capabilities
        return {**(capabilities or {}), "port": self._port}

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

    def _require_writable(self, field: str) -> None:
        """Refuse to set what has only ever been read on this model.

        Reading a byte and writing it are separate permissions here, because
        the commands are not symmetrical: brightness is set by a command
        carrying one byte, so knowing where to read it is knowing how to set
        it, while the charging mode's command carries the whole parameter
        block -- and that block is a different length on a different charger.

        Showing a value that cannot yet be set is better than hiding it, and
        far better than sending a frame nobody has tried.
        """
        model = self.coordinator.model_for(self._key)
        if field in state_writable(model):
            return
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="not_writable_on_model",
            translation_placeholders={"model": model or "?"},
        )

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
