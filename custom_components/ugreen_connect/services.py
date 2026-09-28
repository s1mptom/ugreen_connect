"""The `set_wallpaper` service: put your own picture on the charger's screen.

The charger downloads its wallpaper from UGREEN's storage rather than being sent
the bytes, so this follows the same three steps the app does -- reserve a slot,
upload, register -- then points the screensaver at the newly stored picture.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import logging
import time

import aiohttp
import voluptuous as vol
from homeassistant.const import CONF_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, WALLPAPER_SIZE
from .coordinator import charger_keys, device_key
from .logsafe import charger_tag
from .protocol import state_writable

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_WALLPAPER = "set_wallpaper"
ATTR_PATH = "path"
ATTR_URL = "url"
ATTR_IMAGE = "image"

SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_ID): cv.string,
        vol.Exclusive(ATTR_PATH, "source"): cv.string,
        vol.Exclusive(ATTR_URL, "source"): cv.url,
        # Base64, optionally as a data: URL -- this is how the dashboard card
        # hands over a picture it cropped in the browser.
        vol.Exclusive(ATTR_IMAGE, "source"): cv.string,
    }
)


def _to_wallpaper(raw: bytes) -> bytes:
    """Cover-crop to the screen's 560x170, then store it the way the charger
    expects: turned a quarter turn.

    Every picture in the account's library, the built-in ones included, is held
    as 170x560 -- the screen's image rotated anticlockwise -- and the app turns
    it back when it displays one. Uploading a landscape file instead puts the
    picture on the screen lying on its side.

    Scaling to fit rather than to cover would letterbox badly at this aspect
    ratio, so the picture is scaled to cover and the centre kept.
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
    # PIL rotates anticlockwise for a positive angle, which is the direction the
    # cloud stores these in.
    cropped.rotate(90, expand=True).save(out, format="JPEG", quality=90)
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

        key = next(iter(charger_keys(device.identifiers)), None)
        record = next(
            (d for d in coordinator.data.get("devices", []) if device_key(d) == key),
            None,
        )
        if record is None:
            raise HomeAssistantError("That charger is not in the account's device list")
        # Putting a picture on the screen writes the screensaver group, which
        # is only settable where it has been set and read back. On the 160W it
        # is read and not written yet, and a frame shaped for the 300W is not
        # something to try on it.
        model = coordinator.model_for(key)
        if not {"wallpaper", "screensaver"} <= state_writable(model):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_writable_on_model",
                translation_placeholders={"model": model or "?"},
            )

        if path := call.data.get(ATTR_PATH):
            if not hass.config.is_allowed_path(path):
                # Not the path: it is the owner's own, and errors are logged.
                raise HomeAssistantError("that path is outside allowlist_external_dirs")
            raw = await hass.async_add_executor_job(_read, path)
        elif url := call.data.get(ATTR_URL):
            raw = await _fetch(hass, url)
        elif encoded := call.data.get(ATTR_IMAGE):
            raw = _decode(encoded)
        else:
            raise HomeAssistantError("Give one of path, url or image")

        image = await hass.async_add_executor_job(_to_wallpaper, raw)
        file_name = f"ha_wallpaper_{int(time.time())}.jpg"
        device_code = record["deviceUniqueCode"]
        product_serial = record["productSerialNo"]

        await coordinator.api.upload_wallpaper(
            image, file_name, device_code, product_serial
        )

        # The charger knows pictures by a six-character id, and the signed URL
        # it must download from is only issued once the upload is registered --
        # so read the library back to get both.
        entry_for_file = None
        for _ in range(3):
            await asyncio.sleep(2)
            for item in await coordinator.api.get_wallpapers(device_code, product_serial):
                if item.get("fileName") == file_name:
                    entry_for_file = item
                    break
            if entry_for_file:
                break
        if not entry_for_file:
            raise HomeAssistantError("Uploaded, but the cloud never listed the picture")

        wallpaper_id = entry_for_file.get("fileNameMd5")
        iot_id = record["extra"]["iotId"]

        # Handing over the id alone is not enough: the charger has to be told to
        # fetch the file, or the screensaver would point at a picture it has
        # never seen.
        await coordinator.rtcx.async_set_picture(
            iot_id,
            entry_for_file["url"],
            entry_for_file.get("fileSize") or len(image),
            wallpaper_id,
        )
        await asyncio.sleep(5)

        reading = (coordinator.data.get("power") or {}).get(key) or {}
        await coordinator.rtcx.async_set_screensaver(
            iot_id,
            True,
            reading.get("screensaver_theme", 0),
            reading.get("screensaver_flag", 0),
            wallpaper_id,
        )
        # The tag rather than the device's name, which its owner may have
        # given their own name to.
        _LOGGER.info("Wallpaper %s is now on charger %s", wallpaper_id, charger_tag(iot_id))
        await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_SET_WALLPAPER, _handle, schema=SCHEMA)


def _decode(encoded: str) -> bytes:
    payload = encoded.split(",", 1)[-1] if encoded.startswith("data:") else encoded
    try:
        return base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error) as err:
        raise HomeAssistantError("image is not valid base64") from err


def _read(path: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as err:
        # Not chained: the original names the path, which is the owner's own.
        raise HomeAssistantError(
            f"Could not read the picture: {err.strerror or type(err).__name__}"
        ) from None


async def _fetch(hass: HomeAssistant, url: str) -> bytes:
    from homeassistant.helpers.aiohttp_client import (  # noqa: PLC0415
        async_get_clientsession,
    )

    # Never the URL, which can carry a token of its own -- and aiohttp puts it
    # in the message of every error it raises, so those are not chained.
    try:
        async with async_get_clientsession(hass).get(url, timeout=30) as resp:
            if resp.status != 200:
                raise HomeAssistantError(f"Could not fetch the picture: HTTP {resp.status}")
            return await resp.read()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise HomeAssistantError(f"Could not fetch the picture: {type(err).__name__}") from None
