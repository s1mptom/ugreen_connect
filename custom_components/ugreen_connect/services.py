"""The `set_wallpaper` service: put your own picture on the charger's screen.

The charger downloads its wallpaper from UGREEN's storage rather than being sent
the bytes, so this follows the same three steps the app does -- reserve a slot,
upload, register -- then points the screensaver at the newly stored picture.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import Any

import voluptuous as vol
from homeassistant.const import CONF_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import DOMAIN, WALLPAPER_SIZE
from .coordinator import device_key

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_WALLPAPER = "set_wallpaper"
ATTR_PATH = "path"
ATTR_URL = "url"

SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_ID): cv.string,
        vol.Exclusive(ATTR_PATH, "source"): cv.string,
        vol.Exclusive(ATTR_URL, "source"): cv.url,
    }
)


def _to_wallpaper(raw: bytes) -> bytes:
    """Cover-crop to the screen's 560x170 and encode as JPEG.

    The screen is unusually wide, so scaling to fit would letterbox badly; the
    picture is scaled to cover and the centre is kept.
    """
    try:
        from PIL import Image  # noqa: PLC0415 - optional, only needed here
    except ImportError as err:  # pragma: no cover
        raise HomeAssistantError("Pillow is required to resize the picture") from err

    width, height = WALLPAPER_SIZE
    image = Image.open(io.BytesIO(raw))
    image = image.convert("RGB")

    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        (max(width, round(image.width * scale)), max(height, round(image.height * scale))),
        Image.LANCZOS,
    )
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    cropped = resized.crop((left, top, left + width, top + height))

    out = io.BytesIO()
    cropped.save(out, format="JPEG", quality=90)
    return out.getvalue()


async def async_register(hass: HomeAssistant) -> None:
    """Register the service once, however many chargers are set up."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_WALLPAPER):
        return

    async def _handle(call: ServiceCall) -> None:
        device = dr.async_get(hass).async_get(call.data[CONF_DEVICE_ID])
        if device is None:
            raise HomeAssistantError("Unknown device")

        entry_id = next(iter(device.config_entries), None)
        entry = hass.config_entries.async_get_entry(entry_id) if entry_id else None
        coordinator = getattr(entry, "runtime_data", None)
        if coordinator is None:
            raise HomeAssistantError("That device does not belong to UGREEN Connect")

        key = next(
            (i for domain, i in device.identifiers if domain == DOMAIN),
            None,
        )
        record = next(
            (d for d in coordinator.data.get("devices", []) if device_key(d) == key),
            None,
        )
        if record is None:
            raise HomeAssistantError("That charger is not in the account's device list")

        if path := call.data.get(ATTR_PATH):
            if not hass.config.is_allowed_path(path):
                raise HomeAssistantError(f"{path} is outside allowlist_external_dirs")
            raw = await hass.async_add_executor_job(_read, path)
        elif url := call.data.get(ATTR_URL):
            raw = await _fetch(hass, url)
        else:
            raise HomeAssistantError("Give either path or url")

        image = await hass.async_add_executor_job(_to_wallpaper, raw)
        file_name = f"ha_wallpaper_{int(time.time())}.jpg"
        device_code = record["deviceUniqueCode"]
        product_serial = record["productSerialNo"]

        await coordinator.api.upload_wallpaper(
            image, file_name, device_code, product_serial
        )

        # The charger knows pictures by a six-character id the API only reveals
        # once the upload is registered, so read the library back to find it.
        wallpaper_id = None
        for _ in range(3):
            await asyncio.sleep(2)
            for item in await coordinator.api.get_wallpapers(device_code, product_serial):
                if item.get("fileName") == file_name:
                    wallpaper_id = item.get("fileNameMd5")
                    break
            if wallpaper_id:
                break
        if not wallpaper_id:
            raise HomeAssistantError("Uploaded, but the cloud never listed the picture")

        reading = (coordinator.data.get("power") or {}).get(key) or {}
        await coordinator.rtcx.async_set_screensaver(
            record["extra"]["iotId"],
            True,
            reading.get("screensaver_theme", 0),
            reading.get("screensaver_flag", 0),
            wallpaper_id,
        )
        _LOGGER.info("Wallpaper %s is now on %s", wallpaper_id, device.name)
        await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_SET_WALLPAPER, _handle, schema=SCHEMA)


def _read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


async def _fetch(hass: HomeAssistant, url: str) -> bytes:
    from homeassistant.helpers.aiohttp_client import (  # noqa: PLC0415
        async_get_clientsession,
    )

    async with async_get_clientsession(hass).get(url, timeout=30) as resp:
        if resp.status != 200:
            raise HomeAssistantError(f"Could not fetch {url}: HTTP {resp.status}")
        return await resp.read()
