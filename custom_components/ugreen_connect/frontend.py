"""Serve the dashboard cards that ship with this integration.

Registering them here means installing the integration is enough -- there is no
second HACS entry to add, and no resource to wire up by hand.

Two things are needed for a Lovelace card to load:

1. the JS has to be served -- done with a static path, and
2. the frontend has to be told to load it.

For (2) we register a Lovelace *resource*. ``add_extra_js_url`` looks simpler,
but it does not reliably make storage-mode dashboards load the module, whereas a
resource does.

The two are not additive: doing both leaves the same module loaded twice, once
by the script tag in the page and once by Lovelace's own loader, and the cards
then come up undefined often enough to see -- four of four missing on a cold
load, with "Custom element doesn't exist" in their place and no error anywhere
else. So the script tag is a fallback rather than a belt: it is added only when
there is no resource collection to add to, which is YAML mode, where the user
declares resources themselves.
"""

from __future__ import annotations

import logging
import os

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# One resource per card. The module they share, `ugreen-ui.js`, is not listed:
# the cards import it by relative path, so the browser fetches it once by
# itself, and a resource for it would only make the frontend load it again.
CARD_FILES: tuple[str, ...] = (
    "ugreen-charger-card.js",
    "ugreen-energy-card.js",
    "ugreen-ports-card.js",
    "ugreen-power-card.js",
    "ugreen-wallpaper-card.js",
)
CARD_FILE = CARD_FILES[-1]  # kept for anything still asking for the first card
WWW_URL = f"/{DOMAIN}"
CARD_URL = f"{WWW_URL}/{CARD_FILE}"
_REGISTERED = f"{DOMAIN}_card_registered"


async def async_register_card(hass: HomeAssistant) -> None:
    """Expose the cards' JS and load them into the frontend, once."""
    if hass.data.get(_REGISTERED):
        return
    hass.data[_REGISTERED] = True

    folder = os.path.join(os.path.dirname(__file__), "www")
    missing = [name for name in CARD_FILES if not os.path.exists(os.path.join(folder, name))]
    if missing:
        _LOGGER.warning("Card files missing from %s: %s", folder, ", ".join(missing))
        return

    # The whole folder, not a path per card: the cards import their shared
    # module by relative url, and that module is only fetchable if the folder
    # it sits in is served.
    await hass.http.async_register_static_paths(
        [StaticPathConfig(WWW_URL, folder, cache_headers=False)]
    )
    for name in CARD_FILES:
        url = f"{WWW_URL}/{name}"
        if not await _register_resource(hass, url):
            add_extra_js_url(hass, url)
    _LOGGER.debug("Serving %s from %s", ", ".join(CARD_FILES), WWW_URL)


async def _register_resource(hass: HomeAssistant, url: str) -> bool:
    """Add the card to Lovelace's resource list if it is not already there.

    Returns whether the resource list will load this card, so the caller can
    fall back to a script tag when it will not.

    Only storage-mode Lovelace exposes a writable resource collection; in
    YAML mode there is nothing to do here and the user lists resources in
    their own config, so any failure is downgraded to a debug line.
    """
    lovelace = hass.data.get("lovelace")
    resources = getattr(lovelace, "resources", None)
    if resources is None:
        _LOGGER.debug("Lovelace resources unavailable; falling back to a script tag")
        return False

    try:
        if not resources.loaded:
            await resources.async_load()
            resources.loaded = True
        # async_items() may not exist on the YAML collection.
        items = resources.async_items() if hasattr(resources, "async_items") else []
        # An earlier release registered this same file under a different query
        # string, and matching the url whole left both entries in the list. The
        # browser then loads the module twice, and the second definition of the
        # element throws -- a red error in the console, with the card drawn by
        # whichever copy won. Anything pointing at this file that is not the url
        # wanted goes.
        stale = [
            item
            for item in items
            if item.get("url") != url
            and (item.get("url") or "").split("?", 1)[0] == url.split("?", 1)[0]
        ]
        for item in stale:
            if not hasattr(resources, "async_delete_item"):
                break
            await resources.async_delete_item(item["id"])
            _LOGGER.debug("Removed stale Lovelace resource %s", item.get("url"))
        if any(item.get("url") == url for item in items):
            return True
        if not hasattr(resources, "async_create_item"):
            return False  # YAML mode: read-only
        await resources.async_create_item({"res_type": "module", "url": url})
        _LOGGER.debug("Registered Lovelace resource %s", url)
        return True
    except Exception as err:  # noqa: BLE001 - never let this break setup
        _LOGGER.warning("Could not register Lovelace resource %s: %s", url, err)
        return False
