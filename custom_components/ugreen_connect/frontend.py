"""Serve the dashboard card that ships with this integration.

Registering it here means installing the integration is enough -- there is no
second HACS entry to add, and no resource to wire up by hand.
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
    _LOGGER.debug("Serving %s", CARD_URL)
