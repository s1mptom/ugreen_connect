"""Serve the dashboard card that ships with this integration.

Registering it here means installing the integration is enough -- there is no
second HACS entry to add, and no resource to wire up by hand.

Two things are needed for a Lovelace card to load:

1. the JS has to be served -- done with a static path, and
2. the frontend has to be told to load it.

For (2) we register a Lovelace *resource*. ``add_extra_js_url`` looks simpler,
but it does not reliably make storage-mode dashboards load the module, whereas a
resource does. It is kept as a harmless fallback for YAML-mode setups, where the
resource collection is read-only and the user declares resources themselves.
"""

from __future__ import annotations

import logging
import os

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_FILE = "ugreen-wallpaper-card.js"
CARD_URL = f"/{DOMAIN}/{CARD_FILE}"
_REGISTERED = f"{DOMAIN}_card_registered"


async def async_register_card(hass: HomeAssistant) -> None:
    """Expose the card's JS and load it into the frontend, once."""
    if hass.data.get(_REGISTERED):
        return
    hass.data[_REGISTERED] = True

    path = os.path.join(os.path.dirname(__file__), "www", CARD_FILE)
    if not os.path.exists(path):
        _LOGGER.warning("Card file missing at %s", path)
        return

    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, path, cache_headers=False)]
    )
    add_extra_js_url(hass, CARD_URL)
    await _register_resource(hass, CARD_URL)
    _LOGGER.debug("Serving %s", CARD_URL)


async def _register_resource(hass: HomeAssistant, url: str) -> None:
    """Add the card to Lovelace's resource list if it is not already there.

    Only storage-mode Lovelace exposes a writable resource collection; in
    YAML mode there is nothing to do here and the user lists resources in
    their own config, so any failure is downgraded to a debug line.
    """
    lovelace = hass.data.get("lovelace")
    resources = getattr(lovelace, "resources", None)
    if resources is None:
        _LOGGER.debug("Lovelace resources unavailable; relying on extra_js_url")
        return

    try:
        if not resources.loaded:
            await resources.async_load()
            resources.loaded = True
        # async_items() may not exist on the YAML collection.
        items = resources.async_items() if hasattr(resources, "async_items") else []
        if any(item.get("url") == url for item in items):
            return
        if not hasattr(resources, "async_create_item"):
            return  # YAML mode: read-only
        await resources.async_create_item({"res_type": "module", "url": url})
        _LOGGER.debug("Registered Lovelace resource %s", url)
    except Exception as err:  # noqa: BLE001 - never let this break setup
        _LOGGER.warning("Could not register Lovelace resource %s: %s", url, err)
