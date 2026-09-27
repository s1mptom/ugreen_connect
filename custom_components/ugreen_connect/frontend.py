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

And a third thing, learned the hard way: the address has to change when the
file does. Home Assistant serves a static path without ``Cache-Control``, which
leaves the browser to decide how long a module stays good, and Lovelace loads
cards with ``import()`` after the page is up -- a request a hard reload does not
reliably send again. So an updated card could sit behind the old one through
any number of Cmd+Shift+R. The folder is therefore served under a fingerprint of
its contents, ``/ugreen_connect/<fingerprint>/``; the cards import each other
by relative path, so every one of them inherits it without being told.
"""

from __future__ import annotations

import hashlib
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
    "ugreen-dashboard-card.js",
    "ugreen-energy-card.js",
    "ugreen-ports-card.js",
    "ugreen-power-card.js",
    "ugreen-sessions-card.js",
    "ugreen-wallpaper-card.js",
)
CARD_FILE = CARD_FILES[-1]  # kept for anything still asking for the first card
WWW_URL = f"/{DOMAIN}"
CARD_URL = f"{WWW_URL}/{CARD_FILE}"
FOLDER = os.path.join(os.path.dirname(__file__), "www")
_SERVED = f"{DOMAIN}_www_served"
_RESOURCES = f"{DOMAIN}_card_resources"


def fingerprint(folder: str = FOLDER) -> str:
    """A short name for exactly these files, which changes when any of them does.

    Every script in the folder counts, the shared module included: a change to
    `ugreen-ui.js` alone is a change to every card that imports it.
    """
    digest = hashlib.sha256()
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".js"):
            continue
        digest.update(name.encode())
        with open(os.path.join(folder, name), "rb") as handle:
            digest.update(handle.read())
    return digest.hexdigest()[:12]


def _scan(folder: str) -> tuple[list[str], str]:
    """What is missing, and the fingerprint of what is there -- in one trip to
    the disk, off the event loop."""
    missing = [name for name in CARD_FILES if not os.path.exists(os.path.join(folder, name))]
    return missing, ("" if missing else fingerprint(folder))


async def async_register_card(hass: HomeAssistant) -> None:
    """Expose the cards' JS and load them into the frontend.

    Safe to call again, and worth calling again: a release that adds or changes
    a card reaches a running installation as new files plus a reload of the
    entry, and it is only picked up if the second call looks at what the files
    are now rather than at whether the first call happened.
    """
    missing, stamp = await hass.async_add_executor_job(_scan, FOLDER)
    if missing:
        _LOGGER.warning("Card files missing from %s: %s", FOLDER, ", ".join(missing))
        return

    # Two paths onto the same folder. The fingerprinted one is what the
    # resources point at, and since its address names its contents it can be
    # cached for as long as the browser likes. The bare one is kept for
    # anybody who listed a card by hand before the fingerprint existed; it is
    # served uncached, because its address says nothing about what is behind
    # it. A path registered twice gets a second route rather than an error,
    # which is pointless rather than harmful, so each is registered once.
    served: set[str] = hass.data.setdefault(_SERVED, set())
    base = f"{WWW_URL}/{stamp}"
    wanted = [
        StaticPathConfig(url, FOLDER, cache_headers=cached)
        for url, cached in ((base, True), (WWW_URL, False))
        if url not in served
    ]
    if wanted:
        await hass.http.async_register_static_paths(wanted)
        served.update(config.url_path for config in wanted)

    done: set[str] = hass.data.setdefault(_RESOURCES, set())
    for name in CARD_FILES:
        url = f"{base}/{name}"
        if url in done:
            continue
        if not await _register_resource(hass, url):
            add_extra_js_url(hass, url)
        done.add(url)
    _LOGGER.debug("Serving %s from %s", ", ".join(CARD_FILES), base)


def _same_card(url: str, wanted: str) -> bool:
    """Whether a resource is this integration's copy of the same card.

    Ours if it lives under the integration's own path, the same card if it ends
    in the same file name -- whatever fingerprint, version query or none it was
    registered with.
    """
    path = (url or "").split("?", 1)[0]
    name = wanted.split("?", 1)[0].rsplit("/", 1)[-1]
    return path.startswith(f"{WWW_URL}/") and path.rsplit("/", 1)[-1] == name


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
        # Every earlier address for this card goes: an older release's version
        # query, the bare path from before the fingerprint, last week's
        # fingerprint. Left beside the current one, the browser loads the
        # module twice -- and the copy that wins is whichever arrives first,
        # which after an update is the stale one.
        stale = [
            item
            for item in items
            if item.get("url") != url and _same_card(item.get("url"), url)
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
