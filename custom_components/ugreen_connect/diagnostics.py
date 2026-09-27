"""Diagnostics support for UGREEN Connect."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import UgreenConfigEntry
from .coordinator import device_key
from .protocol import PUBLISHABLE_FRAMES

# The README asks owners of other chargers to attach this file to a public
# issue, so the bar it has to clear is not "no credentials" but "nothing that
# belongs to the household". The MAC, the cloud id, the unit's own code and the
# name of the Wi-Fi network it joined all identify the charger on somebody's
# desk while saying nothing about the model, so they go.
#
# `productSerialNo` deliberately stays. Despite the name it is the model code --
# 030002 is the 300W, 030007 the 160W -- and it is the one identifier a report
# about an unsupported charger is useless without.
TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    "accessToken",
    "refreshToken",
    "token",
    "sid",
    "deviceMac",
    "deviceUniqueCode",
    "iotId",
    "ssid",
}

# Redaction only ever looks at values, and these sections are keyed by the
# device code -- so the code redacted everywhere else would still be sitting
# here in plain sight, as the key.
KEYED_BY_DEVICE = ("detail", "power", "power_errors")


def _publishable(coordinator: Any, iot_id: str | None) -> dict[str, str]:
    """The frames this charger answered with that are safe to hand over.

    An allowlist rather than a list of exclusions: the file is written to be
    posted publicly, and a frame nobody has thought about should not travel by
    default. Two of the queries this client can send answer with the household's
    own details -- the Wi-Fi network name and the serial number, both as plain
    ASCII inside the bytes, where redaction by key name never reaches.
    """
    seen = (coordinator.rtcx.last_frames.get(iot_id) or {}) if iot_id else {}
    return {name: value for name, value in seen.items() if name in PUBLISHABLE_FRAMES}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UgreenConfigEntry
) -> dict[str, Any]:
    """Return the raw cloud payload with anything identifying removed."""
    coordinator = entry.runtime_data
    raw = coordinator.data or {}
    data = async_redact_data(raw, TO_REDACT)

    # A stand-in rather than a blanking, so that a charger can still be followed
    # from its entry in `devices` through to its readings.
    names = {
        key: f"device_{index}"
        for index, device in enumerate(raw.get("devices") or [])
        if (key := device_key(device))
    }
    for section in KEYED_BY_DEVICE:
        if isinstance(entries := data.get(section), dict):
            data[section] = {
                names.get(code, code): value for code, value in entries.items()
            }

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "data": data,
        # The raw frames behind the readings above, per charger and keyed by
        # the question that was asked. On a charger this integration has never
        # seen, the decoded values are only as good as offsets established on a
        # different one -- these bytes are what someone else can check them
        # against, and what turns "my ports are called P1" into a model in the
        # table.
        "frames": {
            names.get(key, key): _publishable(coordinator, iot_id)
            for key, iot_id in (
                (device_key(d), (d.get("extra") or {}).get("iotId"))
                for d in raw.get("devices") or []
            )
            if key is not None
        },
        # Each state reply that differed from the one before, with the bytes
        # that moved -- what someone mapping their charger's settings sends
        # back after changing them in the app one at a time. The same bodies
        # the frames above carry, so nothing in them needs redacting.
        "state_changes": {
            names.get(key, key): list(coordinator.rtcx.state_changes.get(iot_id) or [])
            for key, iot_id in (
                (device_key(d), (d.get("extra") or {}).get("iotId"))
                for d in raw.get("devices") or []
            )
            if key is not None and iot_id
        },
    }
