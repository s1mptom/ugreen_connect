"""Serve wallpaper previews from Home Assistant rather than from the CDN.

The links UGREEN hands out for a picture are signed and **expire after about ten
minutes**, which is why the phone app re-reads the list every time it opens the
wallpaper screen. A dashboard card cannot do that: it points an ``<img>`` at a
URL once and leaves it there, so the browser fetches whatever link was current
when the card was built -- and by then the signature is usually dead. That is
why the owner's own picture showed as a broken image while the charger and the
app were both perfectly happy.

So the card is given a Home Assistant URL instead, and this view fetches the
picture on its behalf: the signed link is resolved at the moment it is needed,
and refreshed once if it has gone stale. The bytes are then kept for a while,
because these pictures never change under a given id.
"""

from __future__ import annotations

import logging
import time

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

URL_BASE = f"/api/{DOMAIN}/wallpaper"
# The CDN answers 403 to anything that does not look like the app.
CDN_USER_AGENT = "okhttp/4.12.0"
# A picture is immutable under its id, so this only bounds how long a replaced
# custom picture could linger.
CACHE_SECONDS = 900
_REGISTERED = f"{DOMAIN}_image_view"


def wallpaper_path(entry_id: str, image_id: str) -> str:
    """The address the card should use for a picture."""
    return f"{URL_BASE}/{entry_id}/{image_id}"


def async_register_view(hass: HomeAssistant) -> None:
    """Register the view once, however many chargers are set up."""
    if hass.data.get(_REGISTERED):
        return
    hass.data[_REGISTERED] = True
    hass.http.register_view(UgreenWallpaperView())


class UgreenWallpaperView(HomeAssistantView):
    """Hand out a wallpaper by its six-character id."""

    url = f"{URL_BASE}/{{entry_id}}/{{image_id}}"
    name = f"api:{DOMAIN}:wallpaper"
    # Home Assistant's own auth applies; the card fetches with the user's token.
    requires_auth = True

    def __init__(self) -> None:
        self._cache: dict[str, tuple[bytes, str, float]] = {}

    async def get(
        self, request: web.Request, entry_id: str, image_id: str
    ) -> web.StreamResponse:
        hass: HomeAssistant = request.app["hass"]
        now = time.time()

        if (hit := self._cache.get(image_id)) and now - hit[2] < CACHE_SECONDS:
            return web.Response(body=hit[0], content_type=hit[1])

        entry = hass.config_entries.async_get_entry(entry_id)
        coordinator = getattr(entry, "runtime_data", None) if entry else None
        if coordinator is None:
            return web.Response(status=404, text="Unknown charger")

        # One retry with a freshly fetched link: a signature that has expired
        # since the card was drawn is the ordinary case here, not an error.
        for refresh in (False, True):
            url = await coordinator.async_wallpaper_url(image_id, refresh=refresh)
            if url is None:
                if refresh:
                    return web.Response(status=404, text="Unknown picture")
                continue
            try:
                session = async_get_clientsession(hass)
                async with session.get(
                    url, headers={"User-Agent": CDN_USER_AGENT}
                ) as response:
                    if response.status == 200:
                        body = await response.read()
                        kind = response.headers.get("Content-Type", "image/jpeg")
                        self._cache[image_id] = (body, kind, now)
                        return web.Response(body=body, content_type=kind)
                    if not refresh:
                        continue
                    _LOGGER.debug("Wallpaper %s: CDN said %s", image_id, response.status)
                    return web.Response(status=502, text="The picture could not be fetched")
            except Exception as err:  # noqa: BLE001 - a preview is never worth raising over
                _LOGGER.debug("Wallpaper %s failed: %s", image_id, err)
                if refresh:
                    return web.Response(status=502, text="The picture could not be fetched")

        return web.Response(status=404, text="Unknown picture")
