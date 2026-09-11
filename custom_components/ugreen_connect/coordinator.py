"""Polling coordinator for UGREEN Connect."""

from __future__ import annotations

import json
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import UgreenApi, UgreenAuthError, UgreenError
from .const import (
    CONF_IDLE_END,
    DEBUG_DUMP_FILE,
    DEFAULT_IDLE_END,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_STATE_INTERVAL,
    DOMAIN,
    IDLE_SCAN_FACTOR,
    IDLE_SCAN_MAX,
    MIN_POLL_GAP,
    MODEL_LOOKUP_ATTEMPTS,
    RETAIN_MISSES,
    RETAIN_SECONDS,
    SESSION_GAP_FACTOR,
    STATIC_INFO_INTERVAL,
    WALLPAPER_LIST_INTERVAL,
    WALLPAPER_MISS_INTERVAL,
)
from .protocol import QUERY_GET_WIFI_SSID
from .rtcx import RtcxClient
from .session import MAX_GAP, SessionTracker
from .session import _drawing as port_drawing

_LOGGER = logging.getLogger(__name__)


class UgreenCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch the account's devices and, for each, whatever detail the cloud gives."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: UgreenApi,
        rtcx: RtcxClient,
        *,
        debug_dump: bool = True,
        models: dict[str, str] | None = None,
        model_store: Store[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
            config_entry=entry,
        )
        # What the option means to the user: one reading every N seconds. The
        # interval handed to the coordinator is only the gap that is left after
        # a poll, and `_reschedule` keeps the two in step.
        self._target_period = float(
            entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        self.api = api
        self.rtcx = rtcx
        # Readings are only continuous with each other if they keep to the poll
        # period, so what counts as a hole has to follow the configured interval
        # rather than a fixed number of seconds.
        self.sessions = SessionTracker(
            max_gap=max(MAX_GAP, self._target_period * SESSION_GAP_FACTOR),
            idle_end=entry.options.get(CONF_IDLE_END, DEFAULT_IDLE_END) * 60,
        )
        self._debug_dump = debug_dump
        self._dumped = False
        self._power_errors: dict[str, str] = {}
        # Whether the last poll found any port drawing, which decides how soon
        # the next one is due.
        self._drawing = True
        # The screen settings, per charger, and when they were last read.
        self._state: dict[str, tuple[dict[str, Any], float]] = {}
        # The last reading that arrived whole, per charger, and how many polls
        # have come up empty since.
        self._good: dict[str, tuple[dict[str, Any], float]] = {}
        self._misses: dict[str, int] = {}
        self._static: dict[str, tuple[dict[str, Any], float]] = {}
        self._wallpaper_cache: dict[str, tuple[list[dict[str, Any]], float]] = {}
        self._wallpaper_missed: dict[str, float] = {}
        self._products: dict[str, Any] = {}
        # What the account API answered when asked which model a charger is.
        # A key here means it has answered; the value may still be None, which
        # is a model with no port table rather than a model not yet known.
        #
        # Seeded from what was learned before, so a charger already known is
        # known again at once: nothing to wait for on a cold start, and nothing
        # for a bad minute on that endpoint to take away. The waiting only ever
        # happens when somebody first adds a charger -- the one moment it
        # cannot be avoided, and the one moment there is no history to lose.
        self._models: dict[str, str | None] = dict(models or {})
        self._model_store = model_store
        self._model_tries: dict[str, int] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        started = time.monotonic()
        try:
            return await self._async_poll()
        finally:
            self._reschedule(time.monotonic() - started)

    def _reschedule(self, elapsed: float) -> None:
        """Keep a steady poll *period*, not a steady gap between polls.

        The coordinator counts its interval from the moment a poll finishes, so
        the real period is interval + however long the poll took -- and a poll
        here is never quick: it writes a query into the charger's ``PT_data``
        and then waits for the device to answer. Asking every 5 s therefore
        produced a reading only every ~9 s.

        Subtracting the time already spent makes the configured value mean what
        it looks like it means: a reading every N seconds. `MIN_POLL_GAP` keeps
        a slow or silent device from turning that into back-to-back requests,
        which is the one way this could make things worse rather than better.
        """
        period = self._target_period
        if not self._drawing:
            period = min(period * IDLE_SCAN_FACTOR, IDLE_SCAN_MAX)
            # ...unless the owner already asked for something slower.
            period = max(period, self._target_period)
        gap = max(MIN_POLL_GAP, period - elapsed)
        wanted = timedelta(seconds=gap)
        if self.update_interval != wanted:
            self.update_interval = wanted

    async def _async_poll(self) -> dict[str, Any]:
        try:
            devices = await self.api.get_devices()
        except UgreenAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except UgreenError as err:
            raise UpdateFailed(str(err)) from err

        # Product metadata rarely changes, so it is fetched once and kept --
        # but only once it has actually arrived. An empty answer used to be
        # cached like any other, which stopped the retry and left the charger
        # numbered for the life of the process.
        for device in devices:
            serial = device.get("productSerialNo")
            key = device_key(device)
            # Asked for whenever the payload is missing, not whenever the model
            # is unknown. Those came apart when the model started being
            # remembered across restarts: `_models` arrives already filled from
            # the store, so keying this on it meant the lookup was never made
            # again, `_products` stayed empty, and `detail` -- which the device
            # page's model and the writable-field checks read -- went with it.
            if key is None or key in self._products:
                continue
            # The attempts are still bounded; a cold store is what resets them.
            if self._model_tries.get(key, 0) >= MODEL_LOOKUP_ATTEMPTS:
                continue
            product: Any = None
            if serial:
                try:
                    product = await self.api.get_product_model(serialNo=serial)
                except UgreenError as err:
                    _LOGGER.debug("product model for %s failed: %s", serial, err)
            # The payload is whatever the cloud put in `data`, which has been
            # seen as something other than a mapping -- and a model read out of
            # a list would take the whole poll down with an AttributeError that
            # nothing here catches.
            if isinstance(product, dict):
                self._products[key] = product
                self._models[key] = product.get("productNo")
                self._remember()
                continue
            tries = self._model_tries[key] = self._model_tries.get(key, 0) + 1
            # Only ever for a charger nobody has named. One whose model came
            # back from the store keeps it: a lookup failing now says nothing
            # about what it was, and renumbering its ports would strand its
            # history over a bad minute on an endpoint.
            if (tries >= MODEL_LOOKUP_ATTEMPTS or not serial) and key not in self._models:
                _LOGGER.warning(
                    "No model for %s after %d attempts; its ports will be "
                    "numbered rather than named",
                    key, tries,
                )
                self._models[key] = None

        # Live readings come from a different cloud (the RTCX gateway) and are
        # per-device, so a failure there must not take the inventory down with
        # it -- the connectivity sensors stay useful either way.
        power: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for device in devices:
            key = device_key(device)
            iot_id = (device.get("extra") or {}).get("iotId")
            if key is None or not iot_id:
                continue
            if key not in self._models:
                # Not "this model has no table" -- "nobody has told us yet".
                # Those arrive at the parser as the same `None`, and only one of
                # them should produce P1..Pn. Waiting a poll costs a few
                # seconds; guessing costs a duplicate set of entities that keeps
                # the history of neither.
                _LOGGER.debug("waiting for the model of %s before naming ports", key)
                continue
            try:
                # productNo is the account API's name for the model, and it is
                # what decides how many ports the report has and what they are
                # called.
                model = self._models[key]
                power[key] = await self.rtcx.async_power(iot_id, model)
                if power[key] is None:
                    errors[key] = "device returned no usable PT_data frame"
                    power[key] = self._carry(key)
                else:
                    power[key].update(await self._device_state(key, iot_id))
                    power[key].update(await self._static_info(key, iot_id))
                    power[key]["ota"] = self.rtcx.ota_state()
                    # A picture uploaded from the phone app is on the charger the
                    # moment it is chosen, while the library was last read up to
                    # a quarter of an hour ago and has never heard of it. Seeing
                    # an id that cannot be named is the signal to look again,
                    # rather than leaving the card blank until the timer comes
                    # round.
                    power[key]["wallpaper_list"] = await self._name_current(
                        device, key, await self._wallpapers(device),
                        power[key].get("wallpaper"),
                    )
                    # Only a reading with everything in it is worth carrying
                    # into a poll that comes back empty.
                    self._good[key] = (power[key], time.time())
                    self._misses[key] = 0
            except UgreenError as err:
                # Warn rather than debug: without this the entities simply never
                # appear, with nothing anywhere saying why.
                if self._power_errors.get(key) != str(err):
                    _LOGGER.warning("Live power unavailable for %s: %s", key, err)
                errors[key] = str(err)
                power[key] = self._carry(key)
        self._power_errors = errors
        # A poll that failed says nothing about whether anything is charging, so
        # an outage keeps the fast rate rather than quietly slowing down exactly
        # when someone is watching for the charger to come back. A carried
        # reading is a failed poll wearing the last answer's clothes, and counts
        # the same way.
        #
        # Asked per port, with session.py's floors, rather than of the total.
        # These chargers do not report zero: a full phone still draws the 0.1 A
        # quantum, and a total of 1.8 W is as true as any other number -- so a
        # truthy sum means "switched on", not "charging", and the slow rate
        # would never arrive on the chargers that idle all night. The floors
        # next door were measured rather than guessed, and a per-port threshold
        # cannot be applied to a sum anyway: a bare cable's stray 0.3 A at
        # 0.0 W disappears into it completely.
        self._drawing = not power or any(
            reading is None
            or reading.get("carried_for")
            or any(
                port_drawing(values)
                for values in (reading.get("ports") or {}).values()
            )
            for reading in power.values()
        )

        # Only readings that actually arrived are folded in: a failed poll has to
        # leave every session untouched, or an outage would read as an unplug. A
        # carried reading is the previous one shown again rather than a new
        # measurement, so it counts as not having arrived -- integrating it
        # would invent energy across exactly the gap where none was measured.
        stamp = time.time()
        for key, reading in power.items():
            if reading and not reading.get("carried_for"):
                self.sessions.update(stamp, key, reading["ports"])

        data = {
            "devices": devices,
            "detail": self._products,
            "power": power,
            "power_errors": errors,
        }

        if self._debug_dump:
            await self.hass.async_add_executor_job(self._write_dump, data)

        return data

    def _remember(self) -> None:
        """Keep what has been learned, so the next start already knows it.

        Only real answers are written. A charger the API has no model for is
        left to ask again next time, since "we have not been told" is a state
        that can still change -- unlike the model of a charger, which cannot.
        """
        if self._model_store is None:
            return
        self._model_store.async_delay_save(
            lambda: {key: name for key, name in self._models.items() if name}, 1
        )
    async def _device_state(self, key: str, iot_id: str) -> dict[str, Any]:
        """The screen settings and the charging mode, on their own slow timer.

        They only change when someone opens the app, and asking costs a round
        trip of its own -- so asking beside every wattage doubles the traffic
        for an answer that is the same one poll after poll.

        Except when this has just written to the charger: then the copy is known
        to be out of date, and waiting out the timer would mean watching one's
        own change take a minute to appear.
        """
        cached, fetched_at = self._state.get(key, ({}, 0.0))
        recent = cached and time.time() - fetched_at < DEVICE_STATE_INTERVAL
        if recent and not self.rtcx.state_is_stale(iot_id):
            return cached
        state = await self.rtcx.async_device_state(iot_id)
        if state is None:
            # A reply that did not arrive says nothing about what the settings
            # are; the last ones that did are still the best answer.
            return cached
        self.rtcx.state_was_read(iot_id)
        self._state[key] = (state, time.time())
        return state

    def _carry(self, key: str) -> dict[str, Any] | None:
        """The last reading that did arrive, while it is still worth showing.

        A reply going missing is ordinary rather than exceptional: the charger
        answers into one cloud property, and anything else asking at the same
        moment can take the answer meant for this poll. Blanking the charger
        for a cycle reads like the device fell off the shelf.

        Held only briefly, and never quietly -- past RETAIN_SECONDS, or after
        RETAIN_MISSES in a row, unavailable is the honest answer again.
        """
        reading, arrived = self._good.get(key, (None, 0.0))
        self._misses[key] = misses = self._misses.get(key, 0) + 1
        age = time.time() - arrived
        if reading is None or misses > RETAIN_MISSES or age > RETAIN_SECONDS:
            return None
        # Everything downstream tells a carried reading from a fresh one by
        # this: the session tracker refuses to integrate it, and diagnostics
        # say how old it is.
        return reading | {"carried_for": round(age, 1)}

    async def _static_info(self, key: str, iot_id: str) -> dict[str, Any]:
        """Firmware version and SSID -- cached, since each costs a round trip to
        the device and neither changes between polls."""
        cached, fetched_at = self._static.get(key, ({}, 0.0))
        if cached and time.time() - fetched_at < STATIC_INFO_INTERVAL:
            return cached
        info = {
            "firmware": await self.rtcx.async_firmware_version(iot_id),
            "ssid": await self.rtcx.async_text_query(iot_id, QUERY_GET_WIFI_SSID),
        }
        # Keep whatever was already known if the device declined to answer.
        info = {k: v if v is not None else cached.get(k) for k, v in info.items()}
        self._static[key] = (info, time.time())
        return info

    async def _wallpapers(self, device: dict[str, Any]) -> list[dict[str, Any]]:
        """The pictures available for this charger, with preview URLs.

        The dashboard card needs somewhere to point an <img> at, and the device
        itself only ever names pictures by a six-character id. Links to uploads
        are signed and expire, so this is re-read on a timer rather than cached
        for the session.
        """
        key = device_key(device) or ""
        cached, fetched_at = self._wallpaper_cache.get(key, ([], 0.0))
        if cached and time.time() - fetched_at < WALLPAPER_LIST_INTERVAL:
            return cached
        try:
            items = await self.api.get_wallpapers(
                device["deviceUniqueCode"], device["productSerialNo"]
            )
        except (UgreenError, KeyError) as err:
            _LOGGER.debug("wallpaper list for %s failed: %s", key, err)
            return cached
        listed = [
            {
                "id": item.get("fileNameMd5"),
                "url": item.get("url"),
                "name": item.get("fileName"),
                "size": item.get("fileSize"),
                "stock": item.get("stock", True),
            }
            for item in items
            if item.get("fileNameMd5")
        ]
        self._wallpaper_cache[key] = (listed, time.time())
        return listed

    async def _name_current(
        self,
        device: dict[str, Any],
        key: str,
        listed: list[dict[str, Any]],
        current: str | None,
    ) -> list[dict[str, Any]]:
        """Re-read the library when the charger shows a picture it does not list.

        Not every unknown id can be found -- a custom picture that has since been
        replaced in the library stays on the charger but is gone from the
        account -- so the look-up is rate limited, or a picture like that would
        have this fetching the library on every single poll.
        """
        if not current or any(item.get("id") == current for item in listed):
            return listed
        if time.time() - self._wallpaper_missed.get(key, 0.0) < WALLPAPER_MISS_INTERVAL:
            return listed
        self._wallpaper_missed[key] = time.time()
        self._wallpaper_cache.pop(key, None)
        return await self._wallpapers(device)

    async def async_wallpaper_url(self, image_id: str, *, refresh: bool = False) -> str | None:
        """The signed link for one picture, optionally re-read from the account.

        Links live about ten minutes, so whatever was cached for the card is
        usually past it by the time a browser asks. `refresh` throws the cached
        list away and fetches the library again, which is what the image view
        does before giving up on a picture.
        """
        if refresh:
            self._wallpaper_cache.clear()
            for device in self.data.get("devices", []):
                reading = (self.data.get("power") or {}).get(device_key(device) or "")
                if reading is not None:
                    reading["wallpaper_list"] = await self._wallpapers(device)
        for reading in (self.data.get("power") or {}).values():
            for item in (reading or {}).get("wallpaper_list") or []:
                if item.get("id") == image_id and item.get("url"):
                    return item["url"]
        return None

    def _write_dump(self, data: dict[str, Any]) -> None:
        """Write one raw snapshot so the entity layer can be built from real data."""
        path = self.hass.config.path(DEBUG_DUMP_FILE)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
        except OSError as err:
            _LOGGER.warning("Could not write %s: %s", path, err)
        else:
            _LOGGER.info("Wrote raw UGREEN cloud snapshot to %s", path)


def device_key(device: dict[str, Any]) -> str | None:
    """Stable per-device identifier.

    `deviceUniqueCode` is the serial the cloud keys everything on; `iotId` is the
    Alibaba-style `<productKey><deviceName>` pair and serves as a fallback.
    """
    if value := device.get("deviceUniqueCode"):
        return str(value)
    if value := (device.get("extra") or {}).get("iotId"):
        return str(value)
    return None
