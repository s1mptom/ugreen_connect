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
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, MAX_PICTURE_BYTES

_LOGGER = logging.getLogger(__name__)

URL_BASE = f"/api/{DOMAIN}/wallpaper"
# The CDN answers 403 to anything that does not look like the app.
CDN_USER_AGENT = "okhttp/4.12.0"
# A picture is immutable under its id, so this only bounds how long a replaced
# custom picture could linger.
CACHE_SECONDS = 900
_REGISTERED = f"{DOMAIN}_image_view"


async def read_capped(stream, limit: int) -> bytes | None:
    """The whole body, or None once it runs past `limit` bytes.

    A loop, because one `read(n)` hands back only what has arrived so far --
    a picture cut short at the first chunk, not a picture capped at `n`.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in stream.iter_chunked(64 * 1024):
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


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
        # By entry and picture: an id is only ever served for the account it
        # belongs to.
        self._cache: dict[tuple[str, str], tuple[bytes, str, float]] = {}

    async def get(
        self, request: web.Request, entry_id: str, image_id: str
    ) -> web.StreamResponse:
        hass: HomeAssistant = request.app["hass"]
        now = time.time()

        if (hit := self._cache.get((entry_id, image_id))) and now - hit[2] < CACHE_SECONDS:
            return web.Response(body=hit[0], content_type=hit[1])

        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
            return web.Response(status=404, text="Unknown charger")
        coordinator = entry.runtime_data
        # Only a picture this account has. Any other id used to send the
        # request on to the cloud for a fresh library, every time -- a way for
        # any signed-in user to make this integration hammer UGREEN's API.
        if not coordinator.knows_wallpaper(image_id):
            return web.Response(status=404, text="Unknown picture")

        # One retry with a freshly fetched link: a signature that has expired
        # since the card was drawn is the ordinary case here, not an error.
        for refresh in (False, True):
            url = await coordinator.async_wallpaper_url(image_id, refresh=refresh)
            if url is None:
                if refresh:
                    return web.Response(status=404, text="Unknown picture")
                continue
            # The link is the cloud's to give, and this hands back whatever it
            # leads to: so a picture over https, from where it says, of a
            # picture's size -- never a redirect onward, which could point this
            # machine at its own network.
            if not url.startswith("https://"):
                _LOGGER.debug("Wallpaper %s: not an https link", image_id)
                return web.Response(status=502, text="The picture could not be fetched")
            try:
                session = async_get_clientsession(hass)
                async with session.get(
                    url, headers={"User-Agent": CDN_USER_AGENT}, allow_redirects=False
                ) as response:
                    if response.status == 200:
                        kind = response.content_type or ""
                        if not kind.startswith("image/"):
                            _LOGGER.debug("Wallpaper %s: CDN sent %s", image_id, kind or "no type")
                            return web.Response(status=502, text="The picture could not be fetched")
                        body = await read_capped(response.content, MAX_PICTURE_BYTES)
                        if body is None:
                            _LOGGER.debug("Wallpaper %s: larger than a picture", image_id)
                            return web.Response(status=502, text="The picture could not be fetched")
                        self._cache[(entry_id, image_id)] = (body, kind, now)
                        return web.Response(body=body, content_type=kind)
                    if not refresh:
                        continue
                    _LOGGER.debug("Wallpaper %s: CDN said %s", image_id, response.status)
                    return web.Response(status=502, text="The picture could not be fetched")
            except Exception as err:  # noqa: BLE001 - a preview is never worth raising over
                # The kind only: the error's text has the signed link in it.
                _LOGGER.debug("Wallpaper %s failed: %s", image_id, type(err).__name__)
                if refresh:
                    return web.Response(status=502, text="The picture could not be fetched")

        return web.Response(status=404, text="Unknown picture")
