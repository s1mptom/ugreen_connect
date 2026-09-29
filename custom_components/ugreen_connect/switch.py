"""Switch platform: the screensaver, priority ports, DC Always On and the 160W's port outputs."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import UgreenConfigEntry
from .const import DOMAIN
from .coordinator import UgreenCoordinator, device_key
from .entity import UgreenDeviceEntity, cloud_errors
from .protocol import PRIORITY_PORTS, port_output_groups


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UgreenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known: set[tuple[str, str]] = set()

    @callback
    def _add_new_devices() -> None:
        new: list[SwitchEntity] = []
        for device in coordinator.data.get("devices", []):
            key = device_key(device)
            if key is None:
                continue
            reading = (coordinator.data.get("power") or {}).get(key)
            if not reading:
                continue
            if reading.get("screensaver") is not None and (key, "screensaver") not in known:
                known.add((key, "screensaver"))
                new.append(UgreenScreensaver(coordinator, key))
            # On any model whose state carries the field at all, whatever mode
            # is running: the switches are unavailable outside `priority` and
            # come alive when it is chosen, rather than appearing only after
            # the charger has been seen in it.
            if "priority" in reading and (key, "priority") not in known:
                known.add((key, "priority"))
                new.extend(UgreenPriorityPort(coordinator, key, port) for port in PRIORITY_PORTS)
            if "dc_turbo" in reading and (key, "dc_turbo") not in known:
                known.add((key, "dc_turbo"))
                new.append(UgreenDcAlwaysOn(coordinator, key))
            if "port_outputs" in reading and (key, "port_outputs") not in known:
                known.add((key, "port_outputs"))
                new.extend(
                    UgreenPortOutput(coordinator, key, name, ports)
                    for name, ports, _ in port_output_groups(coordinator.model_for(key))
                )
        if new:
            async_add_entities(new)

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


class UgreenScreensaver(UgreenDeviceEntity, SwitchEntity):
    """Whether the screen shows the screensaver when idle."""

    _attr_translation_key = "screensaver"
    _attr_icon = "mdi:monitor-shimmer"

    def __init__(self, coordinator: UgreenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_unique_id = f"{key}_screensaver"

    @property
    def available(self) -> bool:
        return super().available and (self._reading or {}).get("screensaver") is not None

    @property
    def is_on(self) -> bool | None:
        return (self._reading or {}).get("screensaver")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        reading = self._reading or {}
        return {
            "theme": reading.get("screensaver_theme"),
            "wallpaper": reading.get("wallpaper"),
        }

    async def _async_set(self, enabled: bool) -> None:
        reading = self._reading or {}
        iot_id = self._iot_id
        if not iot_id:
            return
        # The command carries the whole screensaver block, so the theme and the
        # chosen wallpaper have to be sent back unchanged or they get wiped.
        self._require_writable("screensaver")
        with cloud_errors():
            await self.coordinator.rtcx.async_set_screensaver(
                iot_id,
                enabled,
                reading.get("screensaver_theme", 0),
                reading.get("screensaver_flag", 0),
                reading.get("wallpaper"),
            )
        await self.coordinator.async_read_back(self._key, iot_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


class UgreenPriorityPort(UgreenDeviceEntity, SwitchEntity):
    """Whether one of C1..C3 is charged first while the priority mode runs.

    One switch a port rather than one control for the set, because that is the
    shape of the choice: the app lets any of the three be on, all three
    together included, and each is a yes or a no. Available only in `priority`
    -- under another mode the byte they read is that mode's own setting.

    The three of a charger take turns, with every other write in the mode's
    frame (`UgreenCoordinator.mode_turns`). Each writes the whole set, worked
    out from the last reading, so two presses close together -- C1 on, then C1
    off again -- would otherwise both start from the reading before either: the
    second finds C1 already off and sends nothing, and the charger is left with
    the first. Waiting for the one before it, read back, gives the second the
    set the first left.
    """

    _attr_translation_key = "priority_port"
    _attr_icon = "mdi:priority-high"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: UgreenCoordinator, key: str, port: str) -> None:
        super().__init__(coordinator, key)
        self._port = port
        self._attr_translation_placeholders = {"port": port}
        self._attr_unique_id = f"{key}_{port}_priority"

    @property
    def _ports(self) -> list[str] | None:
        return (self._reading or {}).get("priority")

    @property
    def available(self) -> bool:
        return super().available and self._ports is not None

    @property
    def is_on(self) -> bool | None:
        ports = self._ports
        return None if ports is None else self._port in ports

    async def _async_set(self, on: bool) -> None:
        async with self.coordinator.mode_turns(self._key):
            await self._async_set_now(on)

    async def _async_set_now(self, on: bool) -> None:
        ports = self._ports
        iot_id = self._iot_id
        if ports is None or not iot_id:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="priority_not_running",
            )
        wanted = {*ports, self._port} if on else set(ports) - {self._port}
        if wanted == set(ports):
            return
        if not wanted:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="priority_needs_a_port",
            )
        self._require_writable("priority")
        with cloud_errors():
            await self.coordinator.rtcx.async_set_priority_ports(
                iot_id,
                [port for port in PRIORITY_PORTS if port in wanted],
                self.coordinator.model_for(self._key),
            )
        await self.coordinator.async_read_back(self._key, iot_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


class UgreenDcAlwaysOn(UgreenDeviceEntity, SwitchEntity):
    """Whether DC turbo keeps the DC port live with nothing plugged in.

    The second byte of `dc_turbo`'s block, so available only while that mode
    runs; under another mode the byte is that mode's own.
    """

    _attr_translation_key = "dc_always_on"
    _attr_icon = "mdi:power-plug-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: UgreenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_unique_id = f"{key}_dc_always_on"

    @property
    def _turbo(self) -> dict[str, Any] | None:
        return (self._reading or {}).get("dc_turbo")

    @property
    def available(self) -> bool:
        return super().available and self._turbo is not None

    @property
    def is_on(self) -> bool | None:
        turbo = self._turbo
        return None if turbo is None else turbo["always_on"]

    async def _async_set(self, on: bool) -> None:
        async with self.coordinator.mode_turns(self._key):
            turbo = self._turbo
            iot_id = self._iot_id
            if turbo is None or not iot_id:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="dc_turbo_not_running",
                )
            if turbo["always_on"] == on:
                return
            self._require_writable("dc_turbo")
            with cloud_errors():
                await self.coordinator.rtcx.async_set_dc_turbo(
                    iot_id, self.coordinator.model_for(self._key), always_on=on
                )
            await self.coordinator.async_read_back(self._key, iot_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


class UgreenPortOutput(UgreenDeviceEntity, SwitchEntity):
    """Whether one of the 160W's ports, or pair of them, is switched on.

    Shown and not set, as this integration shows what it reads and cannot yet
    write: the app switches them with SET_PORT_CONTROL, and nobody has watched
    that frame go out. The ports a switch covers are an attribute, so a card can
    find the switch for a port -- C2 and A share one.
    """

    _attr_translation_key = "port_output"
    _attr_icon = "mdi:power-socket"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: UgreenCoordinator, key: str, name: str, ports: tuple[str, ...]
    ) -> None:
        super().__init__(coordinator, key)
        self._group = name
        self._port = name
        self._attr_translation_placeholders = {"port": name}
        self._attr_unique_id = f"{key}_{name}_output"
        self._attr_extra_state_attributes = {"ports": list(ports)}

    @property
    def _outputs(self) -> dict[str, bool] | None:
        return (self._reading or {}).get("port_outputs")

    @property
    def available(self) -> bool:
        return super().available and self._group in (self._outputs or {})

    @property
    def is_on(self) -> bool | None:
        return (self._outputs or {}).get(self._group)

    async def _async_refuse(self) -> None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="port_output_app_only",
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_refuse()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_refuse()
