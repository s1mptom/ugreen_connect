"""Client for the RTCX/Polaris gateway (``eu-gateway.ugreeniot.com``).

This is the layer that carries live telemetry. It is a separate cloud from the
``api2`` account API: requests are signed Alibaba-API-Gateway style and carry
their own ``iotToken``, obtained by trading a one-time OAuth code from the
account API::

    GET  api2 /app/v1/variety/getAppInfo?platform=rtcx  -> appKey/appSecret/oauthClientId/authFlag
    POST api2 /app/v1/oauth/authorize                   -> data.code   (one-time)
    POST gw   /client/account/third/login               -> data.accessToken == iotToken (24 h)

The charger itself does not expose its readings as named properties. It speaks a
small binary protocol tunnelled through the ``PT_data`` property::

    TYPE(1) CMD(1) LEN(2, big endian) PAYLOAD(LEN) CRC16(2, MODBUS, low byte first)

Writing a ``0xAA`` query frame to ``PT_data`` makes the device answer with a
frame of its own, which the cloud then serves as the property's current value.
``GET_POWER_INFO`` answers with eight 7-byte port records (voltage, current and
power, each U16 big endian in tenths).

Both the frame codec and the field layout were verified against a live capture of
the Android app: generated frames match the captured bytes exactly, and decoded
readings satisfy P = U*I.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any, Final

import aiohttp

from .api import UgreenApi, UgreenAuthError, UgreenError
from .const import (
    CHARGING_MODES,
    GATEWAY_LANGUAGE,
    GATEWAY_OK,
    POWER_POLL_ATTEMPTS,
    POWER_SETTLE_SECONDS,
    PT_DATA_MAX_AGE,
    RTCX_TOKEN_MARGIN,
)
from .protocol import (
    FRAME_QUERY,
    FRAME_SETTING,
    QUERY_GET_DEVICE_STATE,
    QUERY_GET_POWER_INFO,
    QUERY_GET_PRODUCT_VERSION,
    SETTING_SET_BRIGHTNESS,
    SETTING_SET_CHARGING_MODE,
    SETTING_SET_SCREENSAVER,
    SETTING_SET_SLEEP_TIME,
    STATE_LAYOUT_BY_MODEL,
    build_frame,
    frame_body,
    parse_power_frame,
    state_fields,
    state_layout,
    state_layout_measured,
)

_LOGGER = logging.getLogger(__name__)


TIMEOUT = aiohttp.ClientTimeout(total=30)

# Only these three are folded into the signature, and they are also what the
# app advertises in `x-ca-signature-headers`.
SIGNED_HEADERS = ("x-ca-key", "x-ca-nonce", "x-ca-timestamp")


# The mode byte is followed by 35 parameter bytes, and they belong to whichever
# mode is in force rather than to the custom editor alone. Watched on a live
# X783: a charger left in `priority` by the app reported 02 in the first of
# them with the other 34 at zero, and selecting `priority` from here -- which
# used to send 35 zeros -- put that byte back to 00 and left it there. So
# sending zeros does not "leave a preset alone": the preset either loses what
# it was carrying or refuses the write, which is what `dc_turbo` does. Hence the
# block last seen for a mode goes back out with it.
#
# That byte is `priority`'s chosen port: set to C2 in the app it reads 0x02.
# The rest of `priority`'s block has been zero in every frame taken from a
# charger in that mode, which is not the same as being unused -- elsewhere in
# the block at least one setting moves two bytes at once, the shared C6+A limit
# at parameter bytes 10 and 34. What the other presets keep there has not been
# watched closely enough to say.
#
# The copy is only as fresh as the state timer. A setting changed in the app
# and that mode re-selected from here inside the same minute replays the older
# block -- and replaying it *writes* it, so the app's change is gone and the
# next read agrees with what was written. Nothing puts it back. Narrower than
# the previous behaviour, which reset it every time rather than sometimes, but
# it is a silent revert and not a window that heals.
# The X783's length, and only its own: the write path asks the model's layout
# rather than this, and nothing in the integration reads it any more. It stays
# because the paragraph above is about these bytes and needs somewhere to live,
# and because the tests over that path measure their payloads against it.
CHARGING_MODE_PARAMS = 35


# Offsets into the GET_DEVICE_STATE reply. Each was confirmed by writing a
# distinctive value and reading it back, not inferred.
STATE_BRIGHTNESS = 2
STATE_SLEEP_TIME = 3
STATE_CHARGING_MODE = 4
# The parameter block, in the same order the setting command takes it.
STATE_MODE_PARAMS = 5
IMAGE_ID_LEN = 6


# How long a parameter block is on each model that has been measured: from the
# mode byte to the screensaver group. Nothing else is a block, and a stored one
# of any other length is not sent -- the payload goes to a charger, and the
# store is a file that outlives this code and can be edited, truncated or left
# behind by a version that wrote something else.
PARAM_BLOCK_LENGTHS: Final[frozenset[int]] = frozenset(
    layout.screensaver - STATE_MODE_PARAMS for layout in STATE_LAYOUT_BY_MODEL.values()
)


def _unpack_params(stored: dict[str, str] | None) -> dict[tuple[str, int], bytes]:
    """Read back what `mode_params_snapshot` wrote, dropping anything odd.

    Dropped rather than raised: the cost of a bad line is one mode change going
    out with empty parameters, and the alternative is a charger that will not
    start at all.
    """
    unpacked: dict[tuple[str, int], bytes] = {}
    if not isinstance(stored, dict):
        # Not the shape this writes. A file of the wrong shape raising here
        # would take the whole integration down on every retry, for the sake
        # of a cache that can be relearned in a minute.
        return unpacked
    for name, value in stored.items():
        iot_id, _, mode = name.rpartition(":")
        try:
            block = bytes.fromhex(value)
            key = (iot_id, int(mode))
        except (TypeError, ValueError):
            _LOGGER.debug("dropping unreadable stored mode parameters: %s", name)
            continue
        if len(block) not in PARAM_BLOCK_LENGTHS:
            _LOGGER.debug(
                "dropping stored mode parameters of %d bytes for %s", len(block), name
            )
            continue
        unpacked[key] = block
    return unpacked


class RtcxClient:
    """Signed access to ``/client/*`` on the RTCX gateway."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api: UgreenApi,
        mode_params: dict[str, str] | None = None,
    ) -> None:
        self._session = session
        self._api = api
        self._app: dict[str, Any] = {}
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()
        # Last propertyMap seen, so OTA state can be read without another call.
        self.last_properties: dict[str, Any] = {}
        # The last raw frame seen for each question asked, per charger. Keyed
        # by the device rather than globally: one client serves an account, so
        # two chargers on it would otherwise share one dict and a diagnostics
        # download for either would carry whichever was polled last.
        # Diagnostics hands these out: on a charger nobody here has, the decoded
        # values are only as good as offsets established on a different one, and
        # these bytes are what someone else can check them against.
        self.last_frames: dict[str, dict[str, str]] = {}
        # The parameter block last seen while each mode was the one in force,
        # keyed by charger and mode byte. Setting a mode has to carry its
        # parameters, and the only place they can be learnt is a state reply
        # taken while that mode was running.
        self._mode_params: dict[tuple[str, int], bytes] = _unpack_params(mode_params)
        # Chargers written to since their state was last read.
        self._state_dirty: set[str] = set()
        # Stable per-account, so the cloud sees one client rather than a new one
        # on every restart. The app uses "ANDRC_" + 12 hex.
        self._client_key = "ANDRC_" + hashlib.sha256(
            (api.user_id or "ugreen-ha").encode()
        ).hexdigest()[:12]

    @property
    def gateway(self) -> str | None:
        domain = self._app.get("appGatewayDomain")
        return f"https://{domain}" if domain else None

    # --------------------------------------------------------------------- auth

    async def async_login(self, *, force: bool = False) -> None:
        """Obtain an ``iotToken``, reusing the current one while it is valid."""
        async with self._lock:
            if (
                not force
                and self._token
                and time.time() < self._expires_at - RTCX_TOKEN_MARGIN
            ):
                return

            if not self._app:
                self._app = await self._api.get_app_info() or {}
            for field in ("appKey", "appSecret", "oauthClientId", "appGatewayDomain"):
                if not self._app.get(field):
                    raise UgreenError(f"getAppInfo did not return {field}")

            # The code is single use, so a fresh one is minted for every login.
            code = await self._api.oauth_authorize(self._app["oauthClientId"])

            payload = await self._call(
                "/client/account/third/login",
                {
                    # Not the account's region -- the app sends this constant.
                    "country": "CN",
                    # This field carries the OAuth code, not the user's password.
                    "password": code,
                    "pwdType": "4",
                    "accountType": "6",
                    "authFlag": self._app.get("authFlag", "3C"),
                    "account": "",
                },
                token="",
            )
            data = payload.get("data") or {}
            token = data.get("accessToken")
            if not token:
                raise UgreenAuthError(f"third/login returned no accessToken: {payload}")
            self._token = token
            self._expires_at = _jwt_expiry(token) or (time.time() + 3600)
            _LOGGER.debug("RTCX login ok, token valid until %s", self._expires_at)

    # ---------------------------------------------------------------- transport

    def _sign(self, path: str, headers: dict[str, str]) -> str:
        parts = [
            "POST",
            headers.get("Accept", ""),
            headers.get("Content-MD5", ""),
            headers.get("Content-Type", ""),
            headers.get("Date", ""),
        ]
        string_to_sign = "\n".join(parts) + "\n"
        for name in sorted(SIGNED_HEADERS):
            string_to_sign += f"{name}:{headers[name]}\n"
        string_to_sign += path
        mac = hmac.new(
            self._app["appSecret"].encode(), string_to_sign.encode(), hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode()

    async def _call(
        self, path: str, params: dict[str, Any], *, token: str | None = None
    ) -> dict[str, Any]:
        body = {
            "id": uuid.uuid4().hex,
            "params": params,
            "request": {
                "apiVer": "1.0",
                "clientUniqueKey": self._client_key,
                "iotToken": self._token if token is None else token,
                "language": GATEWAY_LANGUAGE,
            },
            "version": "1.0",
        }
        raw = json.dumps(body, separators=(",", ":"))
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json; charset=utf-8",
            "Content-MD5": base64.b64encode(hashlib.md5(raw.encode()).digest()).decode(),
            "x-ca-key": self._app["appKey"],
            "x-ca-timestamp": str(int(time.time() * 1000)),
            "x-ca-nonce": str(uuid.uuid4()),
            "x-ca-stage": "release",
            "User-Agent": "okhttp/4.12.0",
        }
        headers["x-ca-signature"] = self._sign(path, headers)
        headers["x-ca-signature-headers"] = ",".join(SIGNED_HEADERS)

        url = f"{self.gateway}{path}"
        try:
            async with self._session.post(
                url, data=raw.encode(), headers=headers, timeout=TIMEOUT
            ) as resp:
                if resp.status != 200:
                    raise UgreenError(f"{path}: HTTP {resp.status}")
                payload = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UgreenError(f"{path}: {err}") from err

        if not isinstance(payload, dict):
            raise UgreenError(f"{path}: unexpected response {payload!r}")
        return payload

    async def call(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """Signed call with the token refreshed, and one retry if it is stale."""
        await self.async_login()
        payload = await self._call(path, params)
        if payload.get("code") != GATEWAY_OK:
            # A token can be invalidated server side before it expires (logging
            # in elsewhere with the same account does it), so try once more.
            _LOGGER.debug("%s -> %s, retrying with a fresh token", path, payload.get("code"))
            await self.async_login(force=True)
            payload = await self._call(path, params)
        if payload.get("code") != GATEWAY_OK:
            raise UgreenError(f"{path}: gateway code {payload.get('code')}")
        return payload

    # ------------------------------------------------------------------ queries

    async def async_status(self, iot_id: str) -> bool:
        payload = await self.call("/client/thing/status/get", {"iotId": iot_id})
        return (payload.get("data") or {}).get("status") == 1

    async def _ask(self, iot_id: str, frame_type: int, cmd: int,
                   payload: bytes = b"\x00") -> str | None:
        """Send one frame and return the raw PT_data the device answers with.

        The device replies asynchronously: the write only queues the frame, and
        the answer turns up as the property's new value a moment later.
        """
        await self.call(
            "/client/thing/properties/set",
            {"iotId": iot_id, "items": {"PT_data": build_frame(frame_type, cmd, payload)}},
        )

        # How long the device takes to answer varies, and until it does the
        # property still holds the previous reply -- which is a different
        # command's frame and would be read as "no data". So poll until the
        # frame on offer is the one that was asked for.
        for attempt in range(POWER_POLL_ATTEMPTS):
            await asyncio.sleep(POWER_SETTLE_SECONDS)
            payload_map = await self.call(
                "/client/thing/properties/get/all", {"iotId": iot_id}
            )
            prop_map = (payload_map.get("data") or {}).get("propertyMap") or {}
            entries = prop_map.get("PT_data") or []
            if not entries:
                continue
            entry = entries[0]
            stamp, value = entry.get("time"), entry.get("value")
            # The property keeps its last frame forever, so an unresponsive
            # device would otherwise look like it is still answering.
            if stamp and (time.time() * 1000 - stamp) > PT_DATA_MAX_AGE * 1000:
                _LOGGER.debug("PT_data for %s is stale (%s)", iot_id, stamp)
                return None
            # Keep the rest of the map: OTA state rides along in the same
            # response, so reading it costs no extra round trip.
            self.last_properties = {
                name: (entries[0].get("value") if entries else None)
                for name, entries in prop_map.items()
            }
            if value and frame_body(value, frame_type, cmd) is not None:
                seen = self.last_frames.setdefault(iot_id, {})
                seen[f"{frame_type:02X}/{cmd}"] = value
                return value
            _LOGGER.debug(
                "PT_data is not the reply to 0x%02X/%d yet (try %d)",
                frame_type, cmd, attempt + 1,
            )
        return None

    def ota_state(self) -> dict[str, Any]:
        """Firmware update state, read from the properties already fetched.

        ``OTA_ugrade`` (the cloud's spelling) only appears once an update is
        actually waiting -- its absence is how "up to date" is expressed, which
        is why nothing here invents a version when it is missing.
        """
        raw = self.last_properties.get("OTA_ugrade")
        offer: dict[str, Any] = {}
        if isinstance(raw, dict):
            offer = raw
        elif isinstance(raw, str) and raw:
            try:
                offer = json.loads(raw)
            except json.JSONDecodeError:
                _LOGGER.debug("OTA_ugrade is not JSON: %r", raw)

        raw_progress = self.last_properties.get("OTA_status")
        try:
            progress: int | None = int(raw_progress)
        except (TypeError, ValueError):
            progress = None
        return {
            "available": offer.get("version"),
            "module": offer.get("module"),
            "size": offer.get("size"),
            "progress": progress,
        }

    async def async_text_query(self, iot_id: str, cmd: int) -> str | None:
        """Queries whose reply is a plain ASCII string (SSID, serial number)."""
        value = await self._ask(iot_id, FRAME_QUERY, cmd)
        body = frame_body(value, FRAME_QUERY, cmd) if value else None
        return body.decode("ascii", "replace").strip("\x00").strip() if body else None

    async def async_firmware_version(self, iot_id: str) -> str | None:
        """Three bytes, one per version component."""
        value = await self._ask(iot_id, FRAME_QUERY, QUERY_GET_PRODUCT_VERSION)
        body = frame_body(value, FRAME_QUERY, QUERY_GET_PRODUCT_VERSION) if value else None
        return ".".join(str(b) for b in body) if body else None

    async def async_device_state(
        self, iot_id: str, model: str | None = None
    ) -> dict[str, Any] | None:
        """Everything the screen settings need, in one round trip.

        Byte offsets were established by changing a value in the app and
        watching which byte moved on a real charger, not by guessing -- which
        proves the byte rather than the model, so where a model has not been
        read, its fields are left out rather than approximated.
        """
        fields = state_fields(model)
        if not fields:
            # The screen settings are offsets rather than a countable layout,
            # and they are written back as well as read. On a charger whose
            # reply has never been seen, none of them appears at all -- which
            # leaves its readings working and its screen alone.
            _LOGGER.debug("state reply not read on model %s", model)
            return None
        layout = state_layout(model)
        value = await self._ask(iot_id, FRAME_QUERY, QUERY_GET_DEVICE_STATE)
        body = frame_body(value, FRAME_QUERY, QUERY_GET_DEVICE_STATE) if value else None
        if not body or len(body) < layout.image_id + IMAGE_ID_LEN:
            return None

        # Remember this mode's parameters: setting the mode again later has to
        # send them back, and this reply is the only place they appear. The
        # block ends where the screensaver group begins, which is nine bytes
        # earlier on the 160W -- taking a fixed 35 would copy that group into
        # what is meant to be parameters. The body is known to reach past it
        # already: the check above needs `image_id` plus six, and the image sits
        # three bytes after the screensaver.
        #
        # Only where the offsets were measured on this model. A charger read at
        # a borrowed layout still shows its settings, which the next lookup
        # corrects; bytes kept in order to be written back are not correctable
        # the same way.
        if state_layout_measured(model):
            self._mode_params[(iot_id, body[STATE_CHARGING_MODE])] = bytes(
                body[STATE_MODE_PARAMS : layout.screensaver]
            )

        image = body[layout.image_id : layout.image_id + IMAGE_ID_LEN]
        # A count byte nobody has watched counting is not read at all.
        wallpapers: list[str] = []
        if layout.wallpaper_count is not None and len(body) > layout.wallpaper_count:
            count = body[layout.wallpaper_count]
            start = layout.wallpaper_count + 1
            wallpapers = [
                body[start + IMAGE_ID_LEN * i : start + IMAGE_ID_LEN * (i + 1)].decode(
                    "ascii", "replace"
                )
                for i in range(count)
                if len(body) >= start + IMAGE_ID_LEN * (i + 1)
            ]
        state = {
            "brightness": body[STATE_BRIGHTNESS],
            "sleep_time": body[STATE_SLEEP_TIME],
            "charging_mode": CHARGING_MODES.get(body[STATE_CHARGING_MODE]),
            "screensaver": bool(body[layout.screensaver]),
            "screensaver_theme": body[layout.screensaver + 1],
            "screensaver_flag": body[layout.screensaver + 2],
            # All-0xFF is how "no picture" is spelled.
            "wallpaper": None if image == b"\xff" * IMAGE_ID_LEN else image.decode(
                "ascii", "replace"
            ),
            "wallpapers": wallpapers,
        }
        # A model is understood a field at a time. Everything not yet confirmed
        # on this one is dropped here rather than published as a plausible
        # number, and the entities that would have carried it never appear.
        return {name: value for name, value in state.items() if name in fields}

    def mode_params_snapshot(self) -> dict[str, str]:
        """The learned blocks, in a shape a store can hold."""
        return {
            f"{iot_id}:{mode}": block.hex()
            for (iot_id, mode), block in self._mode_params.items()
        }

    def state_is_stale(self, iot_id: str) -> bool:
        """Whether this charger has been written to since its state was read."""
        return iot_id in self._state_dirty

    def state_was_read(self, iot_id: str) -> None:
        self._state_dirty.discard(iot_id)

    async def _setting(self, iot_id: str, cmd: int, payload: bytes) -> None:
        await self.call(
            "/client/thing/properties/set",
            {"iotId": iot_id, "items": {"PT_data": build_frame(FRAME_SETTING, cmd, payload)}},
        )
        # Everything set this way shows up in the state reply, so whatever was
        # last read of it is now out of date. Marked here rather than at each of
        # the places that write, so a new one cannot forget to.
        self._state_dirty.add(iot_id)

    async def async_set_picture(
        self, iot_id: str, url: str, size: int, image_id: str, stock: bool = False
    ) -> None:
        """Hand the charger a picture to fetch.

        Unlike everything else here this is a plain property rather than a
        PT_data frame: the device downloads the file itself, which is why the
        screensaver only ever refers to pictures by id.
        """
        await self.call(
            "/client/thing/properties/set",
            {
                "iotId": iot_id,
                "items": {
                    "PIC_data": {
                        "Type": 1 if stock else 0,
                        "size": size,
                        "id": image_id,
                        "version": "01",
                        "url": url,
                    }
                },
            },
        )
        # The one write that does not go through _setting, and the charger's
        # stored list of pictures is part of the state reply -- so without this
        # the cache keeps looking current after the library has changed. The
        # wallpaper flow happens to write the screensaver straight afterwards
        # and mark it that way, which is luck rather than design.
        self._state_dirty.add(iot_id)

    async def async_set_brightness(self, iot_id: str, value: int) -> None:
        await self._setting(
            iot_id, SETTING_SET_BRIGHTNESS, bytes([max(0, min(100, int(value)))])
        )

    async def async_set_sleep_time(self, iot_id: str, value: int) -> None:
        await self._setting(
            iot_id, SETTING_SET_SLEEP_TIME, bytes([max(0, min(255, int(value)))])
        )

    async def async_set_charging_mode(
        self, iot_id: str, mode: int, model: str | None = None
    ) -> None:
        """Mode byte plus the parameters that mode was last seen carrying.

        Zeros only where this mode has not been watched running, which is the
        most that can be said then. Sending zeros unconditionally is what made
        selecting `priority` from Home Assistant reset the priority port the
        app had set -- the charger keeps no copy of its own, so whatever the
        write carries becomes the setting.

        Replaying the bytes rather than rebuilding them, because the two are
        not equivalent: on this model moving the shared C6+A slider one step
        moves two bytes at once -- its limit at body offset 15 and the low byte
        of its protocol mask at 39, parameter bytes 10 and 34 -- so a block
        assembled from decoded values can hold a pair no setting in the app
        produces. Copying cannot.

        Both halves are measured. Sending a `GET_DEVICE_STATE` reply's 36 bytes
        straight back changed nothing on a live X783 -- no limit, no mask, not
        the screensaver bytes after them -- and a port charging at 30 W carried
        on; moving one field landed on that field alone. And the round trip
        this exists for: a priority port set to C2 in the app survived
        `priority` -> `adaptive_power` -> `priority` driven from Home
        Assistant, where before this it came back as 0.
        """
        # A model whose offsets nobody has measured gets nothing written to it.
        # `state_layout` answers with the X783's where it does not know, which
        # is the right shape of guess for *reading* -- a wrong number, corrected
        # by the next lookup -- and the wrong one for a write: 35 bytes into the
        # 160W's 26-byte block land on its screensaver group. `state_writable`
        # refuses the same charger one layer up; this is the layer that touches
        # the hardware, so it refuses too rather than rely on that.
        if not state_layout_measured(model):
            raise UgreenError(
                f"refusing to set a charging mode on an unrecognised model: "
                f"the parameter block's length is not known for {model or 'it'}"
            )
        expected = state_layout(model).screensaver - STATE_MODE_PARAMS
        params = self._mode_params.get((iot_id, mode))
        if params is not None and len(params) != expected:
            # Remembered for one model and sent for another. Unreachable while
            # the key carries the charger, and refused here rather than left
            # to be reachable later. Said instead of the warning below, which
            # would claim this mode has never been seen -- it has.
            _LOGGER.warning(
                "remembered parameters for mode %s are %d bytes where %s takes "
                "%d; setting it with empty parameters instead",
                mode,
                len(params),
                model,
                expected,
            )
            params = bytes(expected)
        # `is None` says "never seen" and nothing else. Truthiness would read
        # the same today -- `bytes` are falsy only when empty, and an empty
        # block cannot get this far past `_unpack_params` -- but the two
        # questions are different ones, and a preset whose parameters really
        # are all zero is an answer rather than an absence.
        elif params is None:
            # The one path left where the block is not this mode's own, and it
            # goes wrong two ways. Some modes take the empty block and lose
            # whatever they were configured with; `dc_turbo` refuses it
            # outright -- measured on an X783, where selecting it with zeros
            # left the charger in the mode it was already in, three times
            # running, with nothing to show but the entity flicking back.
            #
            # The two want different things of the person reading this. A
            # refusal loses nothing, so selecting the mode in the app is enough.
            # A loss is not undone by selecting the mode -- the charger is
            # already in it, with the setting already gone, and what gets
            # learned is the zeros. That one has to be set up again.
            #
            # They differ in how often this is said, too, and the difference is
            # the same fact seen twice: a block is learned from the mode the
            # charger reports itself to be in. After a loss the charger is in
            # the mode, so the next read learns it and this never fires again.
            # After a refusal it never entered the mode, so there is nothing to
            # learn and every further attempt says this again -- which is the
            # right behaviour, the change really is not taking.
            _LOGGER.warning(
                "charging mode %s has not been seen running on this charger, so "
                "it is being set with empty parameters: the charger will either "
                "lose what that mode was configured with or refuse the change "
                "outright. In the UGREEN app, set that mode up again if its "
                "settings are gone, or simply select it if the change did not "
                "take; leave the charger in it for a minute or so and it will be "
                "remembered from then on",
                mode,
            )
            params = bytes(expected)
        await self._setting(iot_id, SETTING_SET_CHARGING_MODE, bytes([mode]) + params)

    async def async_set_screensaver(
        self, iot_id: str, enabled: bool, theme: int, flag: int, wallpaper: str | None
    ) -> None:
        """The trailing six bytes name a wallpaper by id; 'FFFFFF' means none."""
        image = (
            wallpaper.encode("ascii")[:IMAGE_ID_LEN].ljust(IMAGE_ID_LEN, b"F")
            if wallpaper
            else b"\xff" * IMAGE_ID_LEN
        )
        await self._setting(
            iot_id, SETTING_SET_SCREENSAVER, bytes([1 if enabled else 0, theme, flag]) + image
        )

    async def async_power(
        self, iot_id: str, model: str | None = None
    ) -> dict[str, Any] | None:
        """Ask the charger for a power report and read the answer back.

        The device replies asynchronously: the write only queues the query, and
        the reply shows up as the property's new value a moment later.
        """
        value = await self._ask(iot_id, FRAME_QUERY, QUERY_GET_POWER_INFO)
        ports = parse_power_frame(value, model) if value else None
        if ports is None:
            return None
        return {
            "ports": ports,
            "total": round(sum(port["power"] for port in ports.values()), 1),
        }


def _jwt_expiry(token: str) -> float:
    """Read `exp` out of a JWT without verifying it. 0.0 if unreadable."""
    try:
        part = token.removeprefix("Bearer ").split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return float(claims["exp"])
    except Exception:  # noqa: BLE001 - a malformed token just means "unknown"
        return 0.0
