"""Polling coordinator for UGREEN Connect."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import logsafe
from .api import UgreenApi, UgreenAuthError, UgreenError
from .const import (
    CONF_CHARGERS,
    CONF_IDLE_END,
    CONF_OFFERED,
    DEBUG_DUMP_FILE,
    DEFAULT_IDLE_END,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_STATE_INTERVAL,
    DOMAIN,
    FIRMWARE_CHECK_INTERVAL,
    FIRMWARE_INSTALL_TIMEOUT,
    FIRMWARE_POLL_SECONDS,
    FIRMWARE_RETRY_INTERVAL,
    FIRMWARE_START_TIMEOUT,
    FIRMWARE_VERIFY_SECONDS,
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
from .protocol import (
    QUERY_GET_WIFI_SSID,
    UPGRADE_DONE,
    UPGRADE_FAILED,
    UPGRADE_IDLE,
    UPGRADE_RUNNING,
)
from .rtcx import RtcxClient
from .session import MAX_GAP, SessionTracker, drawing

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
        params_store: Store[dict[str, str]] | None = None,
        mode_params: dict[str, str] | None = None,
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
        self._params_store = params_store
        # What the store already holds, so an unchanged poll writes nothing.
        self._saved_params: dict[str, str] = dict(mode_params or {})
        # One queue per charger for every write that goes out in the charging
        # mode's frame: the mode itself, the priority ports, the DC turbo
        # settings. Each sends a whole block worked out from the last reading,
        # so two started side by side both begin from the reading before
        # either, and the second undoes the first -- a voltage change and an
        # Always On toggle together would leave only one of them. In turn, each
        # starts from what the one before it read back.
        self._mode_turns: dict[str, asyncio.Lock] = {}
        # How each charger is named in the log: its tag, never its unit code.
        self._tags: dict[str, str] = {}
        # Every charger on the account, added or not, as the device list gave
        # it last. The options form offers these.
        self.account_devices: list[dict[str, Any]] = []
        # The firmware the account API offered each charger, which MCU version
        # it was asked about, and when to ask again.
        self._offers: dict[str, tuple[dict[str, Any] | None, int, float]] = {}
        # Chargers installing firmware right now, and how far along each is.
        # While one is here nothing else is asked of it -- the app, too, sends
        # it nothing but the progress question until it is done. Kept in
        # hass.data rather than on this object, so an entry reloaded mid-install
        # -- any options change does that -- comes back knowing the charger is
        # busy instead of polling it and offering the install again.
        self.installs: dict[str, dict[str, int]] = hass.data.setdefault(
            f"{DOMAIN}_installs", {}
        )

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
        # Idle, the rate drops -- but not while debug logging is on: somebody
        # mapping a setting changes one in the app every few seconds, usually
        # with nothing plugged in, and a poll every half minute would put
        # several changes into one diff.
        if not self._drawing and not _LOGGER.isEnabledFor(logging.DEBUG):
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

        # Every charger's own identifiers, the moment they are known, so no
        # line from here on carries them (see logsafe) -- the ones left out
        # included, whose ids are in the same list.
        for device in devices:
            key = device_key(device)
            tag = logsafe.remember_charger(
                (device.get("extra") or {}).get("iotId"), key, device.get("deviceMac")
            )
            if key and tag:
                self._tags[key] = tag

        # The whole account, for the options form to offer; then only the
        # chargers that were added, so the rest are never asked anything and
        # get no entities.
        self.account_devices = [d for d in devices if device_key(d)]
        devices = self._added(self.account_devices)

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
                    self._tag(key), tries,
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
            if key in self.installs:
                power[key] = self._installing(key)
                continue
            if key not in self._models:
                # Not "this model has no table" -- "nobody has told us yet".
                # Those arrive at the parser as the same `None`, and only one of
                # them should produce P1..Pn. Waiting a poll costs a few
                # seconds; guessing costs a duplicate set of entities that keeps
                # the history of neither.
                _LOGGER.debug("waiting for the model of %s before naming ports", self._tag(key))
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
                    power[key].update(await self._device_state(key, iot_id, model))
                    power[key].update(await self._static_info(key, iot_id))
                    power[key]["firmware_offer"] = await self._firmware_offer(
                        device, key, power[key].get("mcu_version")
                    )
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
                    # A copy, not the dict itself, so what a missed poll
                    # carries changes only on purpose: async_read_back, which
                    # confirms a setting, updates both.
                    self._good[key] = (dict(power[key]), time.time())
                    self._misses[key] = 0
            except UgreenError as err:
                # Warn rather than debug: without this the entities simply never
                # appear, with nothing anywhere saying why.
                if self._power_errors.get(key) != str(err):
                    _LOGGER.warning("Live power unavailable for %s: %s", self._tag(key), err)
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
                drawing(values)
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
            # `is None` rather than truthiness: the age is a rounded number and
            # a carry fast enough rounds to 0.0, which is false. A reading the
            # coordinator had to carry would then be handed to the tracker as
            # if it were a measurement -- the one thing this line exists to
            # prevent -- and the faster the machine, the likelier it is.
            if reading and reading.get("carried_for") is None:
                self.sessions.update(stamp, key, reading["ports"])

        data = {
            "devices": devices,
            "detail": self._products,
            "power": power,
            "power_errors": errors,
        }

        if self._debug_dump:
            await self.hass.async_add_executor_job(self._write_dump, data)

        # The settings are taken from the cache once more, after every await
        # this poll makes -- the debug dump above included, which is why this
        # sits here rather than beside the reading it corrects. Everything was
        # assembled across those awaits, and a control written during one of
        # them has already published what the charger answered, through its
        # read-back; this reading, built before that write, would put the old
        # value back for a poll. The stale flag is no help: the read-back's own
        # read of the state clears it.
        #
        # The cache holds whichever read landed last, this poll's or the
        # read-back's -- `_device_state` writes it in the same step as the read
        # that returned, which is what keeps "last" meaning last -- so taking
        # the settings from it again cannot go backwards. `data["power"]` is
        # this same dict, so the update reaches what is published; a dump
        # written just above can be a settings tick behind it, and records what
        # the poll read.
        for key, reading in power.items():
            if reading is None:
                continue
            settled, _ = self._state.get(key, ({}, 0.0))
            if settled:
                reading.update(settled)
                # And the retained reading, for the same reason async_read_back
                # updates it: it is what a missed reply carries.
                if (retained := self._good.get(key)) is not None:
                    retained[0].update(settled)

        return data

    def _added(self, devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The chargers this entry was set up with, and word of any new one.

        One bound to the account after the choice was made is not added by
        itself -- adding it is the owner's call, as it was for the rest -- but
        it is not kept quiet either: a repair issue says it is there and where
        to add it, and goes once it is added or the choice is saved without it.
        """
        options = self.config_entry.options
        added = options.get(CONF_CHARGERS)
        if added is None:
            return devices
        offered = set(options.get(CONF_OFFERED, added))
        new = [d for d in devices if device_key(d) not in offered]
        issue = f"new_charger_{self.config_entry.entry_id}"
        if new:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="new_charger",
                translation_placeholders={
                    "names": ", ".join(d.get("deviceName") or "UGREEN" for d in new)
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue)
        return [d for d in devices if device_key(d) in added]

    def _tag(self, key: str) -> str:
        """This charger as the log names it."""
        return self._tags.get(key, "a charger")

    def mode_turns(self, key: str) -> asyncio.Lock:
        """The queue this charger's charging-mode writes wait in, one at a time."""
        return self._mode_turns.setdefault(key, asyncio.Lock())

    def model_for(self, key: str) -> str | None:
        """What this charger is, as far as anyone has been told.

        The one source. `detail` carries the same name, but the product payload
        is fetched afresh every process and the attempts are bounded, so after
        a few failures it is empty while this still holds what was learned last
        time and written down. Reading the model from there would have a
        charger name its ports and read its screen at one model's offsets while
        refusing every write on the grounds that nobody knows the model.
        """
        return self._models.get(key)

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

    def _remember_params(self) -> None:
        """Keep the parameter blocks the chargers have been seen running with.

        Only when they have actually moved. The state is re-read every minute
        and answers the same almost every time, so saving on each one would
        rewrite an unchanged file all day -- on hardware that is often a
        memory card.

        Synchronous, and that is load-bearing rather than incidental. This runs
        between a state read returning and `_device_state` caching what it
        returned, so an await here would let an older read overtake a newer one
        into the cache -- and the poll's correction, which takes the settings
        from that cache last thing, would then publish the older one. A Store
        save is exactly the thing that grows an await later, so it is pinned by
        a test rather than left to be noticed.
        """
        if self._params_store is None:
            return
        snapshot = self.rtcx.mode_params_snapshot()
        if snapshot == self._saved_params:
            return
        self._saved_params = snapshot
        self._params_store.async_delay_save(lambda: snapshot, 1)

    async def _device_state(
        self, key: str, iot_id: str, model: str | None
    ) -> dict[str, Any]:
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
        # Except while debug logging is on, too. That is when somebody is
        # mapping a charger -- one setting changed in the app, then which byte
        # moved -- and a minute between reads would put several changes into
        # one diff. Every poll costs a second round trip, for as long as the
        # logging lasts.
        mapping = _LOGGER.isEnabledFor(logging.DEBUG)
        if recent and not self.rtcx.state_is_stale(iot_id) and not mapping:
            return cached
        state = await self.rtcx.async_device_state(iot_id, model)
        # The read may have learned this mode's parameter block, which has to
        # outlive the process: a mode's parameters can only be learned while
        # that mode is running, so a restart that forgets them sends the next
        # mode change out empty.
        self._remember_params()
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

    async def async_read_back(self, key: str, iot_id: str) -> None:
        """Publish what the charger did with a write, not what it was asked.

        A setting can be declined without anything saying so: the frame is
        accepted, and the state simply does not change. An X783 declines DC
        turbo sent with an all-zero parameter block, which is what the client
        sends for a mode it has never watched running, and takes the same
        request with a block it has run -- with nothing on the DC port either
        time. An entity that wrote its own request into the reading and called
        it current would then show a value the charger never held, until a state
        read next came round.

        Never raises. The write this follows has already succeeded or said why
        it did not, and somebody who has just moved a control should not be told
        their change failed because the confirmation of it did.
        """
        try:
            # The write marked the cached state stale, so this asks the device
            # rather than answering from the cache -- and a poll that got here
            # first has already done the asking, which is why this goes through
            # the cache instead of around it.
            state = await self._device_state(key, iot_id, self._models.get(key))
            if not state or self.rtcx.state_is_stale(iot_id):
                # Still dirty means no reply came. The next poll will say what
                # the setting is; publishing the cached value would report the
                # old one as confirmed, which is worse than not confirming.
                _LOGGER.debug("no read-back for %s; leaving it to the poll", self._tag(key))
                return
            listed = await self._wallpaper_list_for(key, state.get("wallpaper"))
        except UgreenError as err:
            _LOGGER.debug("read-back for %s failed: %s", self._tag(key), err)
            return

        # Read after those awaits, never before them: a poll finishing in that
        # window builds a new reading, and one fetched earlier is then an orphan
        # -- updated, republished, and carrying whatever the poll saw. The
        # control visibly snapping back to its old value is exactly the failure
        # a read-back exists to prevent.
        reading = ((self.data or {}).get("power") or {}).get(key)
        if reading is None:
            return
        reading.update(state)
        if listed is not None:
            reading["wallpaper_list"] = listed
        # The retained reading takes the same update: it is what a missed poll
        # carries, so without this the first reply to go missing after a write
        # republishes the setting from before it.
        if retained := self._good.get(key):
            retained[0].update(state)
            if listed is not None:
                retained[0]["wallpaper_list"] = listed
        # carried_for stays if it is there. It is the age of the power figures,
        # which nothing here has touched -- dropping it would hand diagnostics a
        # reading that never arrived and call it current.
        self.async_update_listeners()

    async def _wallpaper_list_for(
        self, key: str, current: str | None
    ) -> list[dict[str, Any]] | None:
        """The library again, if the charger now shows a picture it did not list.

        Picking a newly uploaded one changes what the charger shows to an id the
        list in hand does not have, and the preview has nowhere to point until
        the next poll looks again. Unchanged in the ordinary case: _name_current
        only re-reads when the id is missing, and rate limits even then.
        """
        device = next(
            (
                entry
                for entry in (self.data or {}).get("devices") or []
                if device_key(entry) == key
            ),
            None,
        )
        if device is None:
            return None
        reading = ((self.data or {}).get("power") or {}).get(key) or {}
        return await self._name_current(
            device, key, reading.get("wallpaper_list") or [], current
        )

    async def _static_info(self, key: str, iot_id: str) -> dict[str, Any]:
        """Firmware version and SSID -- cached, since each costs a round trip to
        the device and neither changes between polls."""
        cached, fetched_at = self._static.get(key, ({}, 0.0))
        if cached and time.time() - fetched_at < STATIC_INFO_INTERVAL:
            return cached
        fresh = {
            "firmware": await self.rtcx.async_firmware_version(iot_id),
            "ssid": await self.rtcx.async_text_query(iot_id, QUERY_GET_WIFI_SSID),
        }
        # Keep whatever was already known if the device declined to answer.
        info = {k: v if v is not None else cached.get(k) for k, v in fresh.items()}
        # A version that did not arrive is asked for again in a minute rather
        # than an hour: just after an install is when a reply goes missing,
        # and an hour of no version is an hour of no update entity.
        stamp = time.time()
        if fresh["firmware"] is None:
            stamp -= STATIC_INFO_INTERVAL - 60
        self._static[key] = (info, stamp)
        return info

    async def _firmware_offer(
        self, device: dict[str, Any], key: str, mcu: int | None
    ) -> dict[str, Any] | None:
        """Newer firmware for this charger, if the account API has any.

        Asked with the MCU version the charger reports, so the answer changes
        by itself once an install lands. Only what is shown is kept: the signed
        link to the file expires, and is asked for again at install time.
        """
        offer, asked_at, due = self._offers.get(key, (None, -1, 0.0))
        if mcu is None:
            return offer
        if asked_at == mcu and time.time() < due:
            return offer
        # An offer made to a different version is not one to show.
        kept = offer if asked_at == mcu else None
        try:
            data = await self.api.check_firmware(device["productSerialNo"], mcu)
        except (UgreenError, KeyError) as err:
            _LOGGER.debug("firmware check for %s failed: %s", self._tag(key), err)
            self._offers[key] = (kept, mcu, time.time() + FIRMWARE_RETRY_INTERVAL)
            return kept
        offer = _offer(data, mcu)
        self._offers[key] = (offer, mcu, time.time() + FIRMWARE_CHECK_INTERVAL)
        return offer

    async def async_install_firmware(self, key: str, device: dict[str, Any]) -> None:
        """Install the offered firmware, and return once the charger has it.

        The UGREEN app's path, step for step: ask for the offer afresh -- the
        link in it is signed and short-lived -- hand the charger the file, then
        ask it how it is going until it says it is done. Raises if it says it
        failed, or never says either.
        """
        if key in self.installs:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="firmware_installing"
            )
        iot_id = (device.get("extra") or {}).get("iotId")
        reading = ((self.data or {}).get("power") or {}).get(key) or {}
        mcu = reading.get("mcu_version")
        if not iot_id or mcu is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="firmware_version_unknown"
            )
        # Taken before the first await. A second press, another tab or an
        # automation arriving while the offer is fetched has to find the
        # charger busy, or it is sent two installs.
        self.installs[key] = {"status": UPGRADE_IDLE, "progress": 0}
        self.async_update_listeners()
        try:
            offer, url = await self._fresh_offer(key, device, mcu)
            _LOGGER.info("installing firmware %s on %s", offer["version"], self._tag(key))
            try:
                await self.rtcx.async_start_firmware_update(
                    iot_id, url=url, size=offer["size"], md5=offer["md5"], version=offer["code"]
                )
            except Exception as err:  # anything at all: see below
                # Not proof that it did not arrive: a request that timed out
                # may well have. What the charger says next decides, and one
                # that never starts is caught by the start timeout -- while
                # giving up here would reopen the button over a charger that
                # may already be fetching the file.
                _LOGGER.warning(
                    "the firmware command to %s may not have arrived (%s: %s)",
                    self._tag(key), type(err).__name__, err,
                )
            await self._follow_install(key, iot_id, offer)
        finally:
            self.installs.pop(key, None)
            # Whatever happened, what is known about this charger's firmware is
            # now out of date: the version, and what the cloud offers it. The
            # version is marked for re-reading rather than forgotten, so a
            # charger slow to answer after its restart keeps showing one.
            self._offers.pop(key, None)
            known, _ = self._static.get(key, ({}, 0.0))
            self._static[key] = (known, 0.0)
            self._state.pop(key, None)
            await self.async_request_refresh()

    async def _fresh_offer(
        self, key: str, device: dict[str, Any], mcu: int
    ) -> tuple[dict[str, Any], str]:
        """The offer, asked for now, and the link to its file."""
        try:
            data = await self.api.check_firmware(device["productSerialNo"], mcu)
        except (UgreenError, KeyError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="firmware_check_failed",
                translation_placeholders={"error": str(err)},
            ) from None
        offer = _offer(data, mcu)
        if offer is None:
            self._offers[key] = (None, mcu, time.time() + FIRMWARE_CHECK_INTERVAL)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="firmware_current"
            )
        url = data.get("fileUrl")
        if not isinstance(url, str) or not url:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="firmware_check_failed",
                translation_placeholders={"error": "the answer carried no link to the file"},
            )
        return offer, url

    async def _follow_install(self, key: str, iot_id: str, offer: dict[str, Any]) -> None:
        """Ask the charger about its install until it names an outcome.

        What it says is not taken on faith at either end. "Done" is believed
        once the version says so: the answer may be left over from the install
        before, and one heard before this one started would end it before it
        began. "Failed" before anything has run may be left over the same way,
        so it only counts once the start has had its time.
        """
        started = time.monotonic()
        deadline = started + FIRMWARE_INSTALL_TIMEOUT
        running = False
        failed_early = False
        checked = float("-inf")

        async def settled() -> bool:
            nonlocal checked
            if time.monotonic() - checked < FIRMWARE_VERIFY_SECONDS:
                return False
            checked = time.monotonic()
            return await self._installed(iot_id, key, offer)

        while time.monotonic() < deadline:
            if not running and time.monotonic() - started > FIRMWARE_START_TIMEOUT:
                break
            await asyncio.sleep(FIRMWARE_POLL_SECONDS)
            try:
                answer = await self.rtcx.async_upgrade_status(iot_id)
            except Exception as err:  # the flash goes on whatever this met
                # The app sees these too, and keeps asking: the charger
                # restarts into the new firmware partway through.
                _LOGGER.debug("install status for %s: %s", self._tag(key), err)
                answer = None
            if answer is None:
                # Gone quiet after starting is the restart, and the new
                # firmware may not answer this question at once. Its version
                # answers either way.
                if running and await settled():
                    return
                continue
            status, progress = answer
            if status == UPGRADE_RUNNING:
                running = True
                self.installs[key] = {"status": status, "progress": progress}
                self.async_update_listeners()
            elif status == UPGRADE_FAILED:
                if running:
                    raise HomeAssistantError(
                        translation_domain=DOMAIN, translation_key="firmware_failed"
                    )
                failed_early = True
            elif (status == UPGRADE_DONE or running) and await settled():
                # Done, or back to "not upgrading" after having been at it --
                # and the version has moved either way.
                _LOGGER.info("firmware %s installed on %s", offer["version"], self._tag(key))
                return
        if await self._installed(iot_id, key, offer):
            return
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="firmware_failed" if failed_early else "firmware_unconfirmed",
        )

    async def _installed(self, iot_id: str, key: str, offer: dict[str, Any]) -> bool:
        """Whether the charger now runs the MCU version it was sent.

        Through the cache like every state read, but never answered from it: a
        copy read a moment ago, mid-install, would say the old version for the
        next minute.
        """
        self._state.pop(key, None)
        try:
            state = await self._device_state(key, iot_id, self._models.get(key))
        except UgreenError:
            return False
        return bool(state) and state.get("mcu_version") == offer["code"]

    def _installing(self, key: str) -> dict[str, Any] | None:
        """This charger's reading while it installs firmware.

        Nothing is measured, so no port has a value -- the last watts shown
        again would be a charger delivering power while it restarts -- and the
        rest is what was last known, marked carried so no session counts it.
        """
        reading, arrived = self._good.get(key, (None, 0.0))
        if reading is None:
            return None
        return reading | {
            "ports": {},
            "total": None,
            "carried_for": round(time.time() - arrived, 1),
        }

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
            _LOGGER.debug("wallpaper list for %s failed: %s", self._tag(key), err)
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


def _offer(data: dict[str, Any] | None, mcu: int) -> dict[str, Any] | None:
    """What an answer from check_upgrade offers, minus the link; None if nothing.

    ``versionName`` reads "V1.2.3" where the charger reports 1.2.1, so the V
    goes. An offer of the version already running, or an older one, is none.
    """
    if not isinstance(data, dict):
        return None
    try:
        code = int(data["versionCode"])
        size = int(data["fileSize"])
    except (KeyError, TypeError, ValueError):
        return None
    md5 = data.get("fileMd5")
    if code <= mcu or not isinstance(md5, str) or not md5:
        return None
    # Only strings are taken as text. This runs inside every poll, and a
    # field that arrives as a list must cost its line, not the poll.
    def text(name: str) -> str | None:
        value = data.get(name)
        return value.strip() or None if isinstance(value, str) else None

    return {
        "version": (text("versionName") or str(code)).lstrip("Vv"),
        "code": code,
        "size": size,
        "md5": md5,
        "notes": text("changeList"),
        "published": text("publishTime"),
    }


def charger_keys(identifiers: set[tuple[str, str]]) -> set[str]:
    """The charger keys in a Home Assistant device's identifiers."""
    return {key for domain, key in identifiers if domain == DOMAIN}


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
