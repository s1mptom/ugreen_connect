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
from typing import Any

import aiohttp

from .api import UgreenApi, UgreenAuthError, UgreenError
from .const import (
    GATEWAY_LANGUAGE,
    GATEWAY_OK,
    HANDSHAKE_PROTOCOL,
    POWER_SETTLE_SECONDS,
    PT_DATA_MAX_AGE,
    RTCX_TOKEN_MARGIN,
    X783_PORTS,
)

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=30)

# Only these three are folded into the signature, and they are also what the
# app advertises in `x-ca-signature-headers`.
SIGNED_HEADERS = ("x-ca-key", "x-ca-nonce", "x-ca-timestamp")

FRAME_QUERY = 0xAA
FRAME_NOTIFY = 0xEE
FRAME_SETTING = 0x11

QUERY_GET_DEVICE_STATE = 1
QUERY_GET_SN = 5
QUERY_GET_POWER_INFO = 6
QUERY_GET_UPGRADE_STATUS = 7
QUERY_GET_WIFI_SSID = 8
QUERY_GET_PRODUCT_VERSION = 10

SETTING_SET_BRIGHTNESS = 1

# One 7-byte record per port, then one handshake-protocol byte per port.
PORT_RECORD = 7
PORT_COUNT = 8
POWER_BODY_MIN = PORT_RECORD * PORT_COUNT

# Offset of the brightness byte in the GET_DEVICE_STATE reply, confirmed by
# setting a value and reading it back.
STATE_BRIGHTNESS = 2


def crc16_modbus(data: bytes) -> int:
    """CRC-16/MODBUS, the checksum the charger's frames carry."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def build_frame(frame_type: int, cmd: int, payload: bytes = b"\x00") -> str:
    """Encode one protocol frame as the uppercase hex string ``PT_data`` wants."""
    body = bytes((frame_type, cmd)) + len(payload).to_bytes(2, "big") + payload
    return (body + crc16_modbus(body).to_bytes(2, "little")).hex().upper()


def frame_body(value: str, frame_type: int, cmd: int) -> bytes | None:
    """Return a frame's payload if it is the reply we asked for and the CRC holds."""
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        _LOGGER.debug("PT_data is not hex: %r", value)
        return None
    if len(raw) < 6:
        return None
    length = int.from_bytes(raw[2:4], "big")
    body = raw[4 : 4 + length]
    if len(body) != length:
        return None
    if crc16_modbus(raw[: 4 + length]) != int.from_bytes(
        raw[4 + length : 6 + length], "little"
    ):
        _LOGGER.debug("PT_data CRC mismatch: %s", value)
        return None
    if raw[0] != frame_type or raw[1] != cmd:
        return None
    return body


def parse_power_frame(value: str) -> dict[str, dict[str, Any]] | None:
    """Decode a ``GET_POWER_INFO`` reply into ``{port_name: {volt, amp, watt}}``.

    Returns None for anything else -- the property also holds replies to other
    commands, and the last one simply stays there until the device sends a new.
    """
    body = frame_body(value, FRAME_QUERY, QUERY_GET_POWER_INFO)
    if body is None:
        return None
    if len(body) < POWER_BODY_MIN:
        _LOGGER.debug("power body too short: %d < %d", len(body), POWER_BODY_MIN)
        return None

    def u16(offset: int) -> int:
        return int.from_bytes(body[offset : offset + 2], "big")

    ports: dict[str, dict[str, Any]] = {}
    for index, name in enumerate(X783_PORTS):
        base = PORT_RECORD * index
        # The protocol byte block follows the port records; a port that has
        # nothing attached reports 0 ("none").
        proto_at = POWER_BODY_MIN + index
        ports[name] = {
            "voltage": u16(base) / 10,
            "current": u16(base + 2) / 10,
            "power": u16(base + 4) / 10,
            "protocol": HANDSHAKE_PROTOCOL.get(
                body[proto_at] if len(body) > proto_at else 0, "unknown"
            ),
        }
    return ports


class RtcxClient:
    """Signed access to ``/client/*`` on the RTCX gateway."""

    def __init__(self, session: aiohttp.ClientSession, api: UgreenApi) -> None:
        self._session = session
        self._api = api
        self._app: dict[str, Any] = {}
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()
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
        await asyncio.sleep(POWER_SETTLE_SECONDS)
        payload_map = await self.call(
            "/client/thing/properties/get/all", {"iotId": iot_id}
        )
        prop_map = (payload_map.get("data") or {}).get("propertyMap") or {}
        entries = prop_map.get("PT_data") or []
        if not entries:
            return None
        entry = entries[0]
        stamp = entry.get("time")
        # The property keeps its last frame forever, so an unresponsive device
        # would otherwise look like it is still answering.
        if stamp and (time.time() * 1000 - stamp) > PT_DATA_MAX_AGE * 1000:
            _LOGGER.debug("PT_data for %s is stale (%s)", iot_id, stamp)
            return None
        return entry.get("value")

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

    async def async_brightness(self, iot_id: str) -> int | None:
        value = await self._ask(iot_id, FRAME_QUERY, QUERY_GET_DEVICE_STATE)
        body = frame_body(value, FRAME_QUERY, QUERY_GET_DEVICE_STATE) if value else None
        if not body or len(body) <= STATE_BRIGHTNESS:
            return None
        return body[STATE_BRIGHTNESS]

    async def async_set_brightness(self, iot_id: str, value: int) -> None:
        level = max(0, min(100, int(value)))
        await self.call(
            "/client/thing/properties/set",
            {
                "iotId": iot_id,
                "items": {
                    "PT_data": build_frame(FRAME_SETTING, SETTING_SET_BRIGHTNESS, bytes([level]))
                },
            },
        )

    async def async_power(self, iot_id: str) -> dict[str, Any] | None:
        """Ask the charger for a power report and read the answer back.

        The device replies asynchronously: the write only queues the query, and
        the reply shows up as the property's new value a moment later.
        """
        value = await self._ask(iot_id, FRAME_QUERY, QUERY_GET_POWER_INFO)
        ports = parse_power_frame(value) if value else None
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
