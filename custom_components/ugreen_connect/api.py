"""Async client for the UGREEN Connect cloud (``*.ugreeniot.com``).

Reconstructed from the UgreenConnect Android app (``com.ugreen.aiot.connect``).
The service always answers HTTP 200 and reports status in the JSON body as
``{"code": ..., "msg": ..., "data": ..., "success": ...}``.

Sensitive endpoints are wrapped in an encrypted envelope. In the app this is done
transparently by an OkHttp interceptor triggered by an ``X-Encrypt: true`` header:

1. ``POST /app/v1/user/security/getSidInfo`` returns a short lived ``sid`` plus
   an RSA-2048 public key (DER, base64).
2. The *entire* request body JSON is encrypted with ``RSA/ECB/PKCS1Padding`` and
   base64 encoded.
3. What is actually sent is ``{"data": "<ciphertext>", "sid": "<sid>"}``.

Responses are not encrypted. Authenticated calls carry
``Authorization: Bearer <accessToken>``.

Note the 245 byte PKCS#1 v1.5 ceiling for RSA-2048: envelopes only ever carry
short credential payloads, which stay well inside it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
import uuid
from typing import Any

import aiohttp
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_der_public_key

from .const import (
    APP_INFO_PLATFORM,
    CODE_NO_PERMISSION,
    CODE_OK,
    DEFAULT_LANGUAGE,
    DEFAULT_REGION,
)

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=30)

# Returned when the envelope decrypted fine but the credentials were wrong. A
# plain `failure` instead usually means the envelope itself was malformed.
CODE_BAD_CREDENTIALS = 200040
# Generic "bad request"; also used for "Unsupported http method ...".
CODE_WRONG_METHOD = 100001

# Access tokens are JWTs that live only ~20 minutes; renew this far ahead.
TOKEN_REFRESH_MARGIN = 120

# The uplink drops a fair number of TLS handshakes; absorb those locally.
NETWORK_RETRIES = 3
RETRY_DELAY = 1.5

_METHOD_RE = re.compile(r"supported http methods are \[([A-Z]+)", re.I)


def _wanted_method(msg: str) -> str | None:
    """Pull the verb out of the API's own complaint about the wrong one."""
    match = _METHOD_RE.search(msg or "")
    return match.group(1).upper() if match else None


class UgreenError(Exception):
    """Any error talking to the UGREEN cloud."""


class UgreenAuthError(UgreenError):
    """Credentials were rejected, or the token is no longer valid."""


class UgreenApi:
    """Minimal client covering login, device listing and product models."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        language: str = DEFAULT_LANGUAGE,
        region: str = DEFAULT_REGION,
    ) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._language = language
        # The app calls the region `serverNodeCode`, and some endpoints reject
        # the request outright when it is missing.
        self._region = region
        self._token: str | None = None
        self._refresh_token: str | None = None
        self._user_id: str | None = None
        self._expires_at: float = 0.0
        self._credentials: tuple[str, str] | None = None
        self._relogin_lock = asyncio.Lock()

    @property
    def token(self) -> str | None:
        """The current access token, if logged in."""
        return self._token

    @property
    def user_id(self) -> str | None:
        """The account's user id, as reported by the login response."""
        return self._user_id

    # ---------------------------------------------------------------- transport

    def _headers(self, auth: bool) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "okhttp/4.12.0",
            # Rejected with code 100013 "Missing request header [language]"
            # on some endpoints if absent.
            "language": self._language,
        }
        if auth and self._token:
            # The cloud hands back a token that already carries the "Bearer "
            # prefix, so adding another one would double it.
            headers["Authorization"] = f"Bearer {self._token.removeprefix('Bearer ')}"
        return headers

    async def _request(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        method: str = "POST",
        auth: bool = True,
        encrypt: bool = False,
        raise_on_error: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Call an endpoint and return the response envelope.

        Which verb an endpoint wants is not always obvious, but the API says so
        explicitly when it is wrong ("the supported http methods are [GET]"), so
        one corrected retry is attempted rather than failing the whole update.
        """
        if auth:
            await self._ensure_fresh_token()
        if encrypt:
            body = await self._seal(body or {})

        payload = await self._call(path, body, method, auth, extra_headers)

        if payload.get("code") == CODE_WRONG_METHOD and (
            wanted := _wanted_method(payload.get("msg", ""))
        ):
            if wanted != method:
                _LOGGER.debug("%s wants %s, not %s -- retrying", path, wanted, method)
                method = wanted
                payload = await self._call(path, body, method, auth, extra_headers)

        # A token can still be rejected early (revoked elsewhere, clock skew), so
        # fall back to a single re-login and retry before giving up.
        if payload.get("code") == CODE_NO_PERMISSION and auth and self._credentials:
            _LOGGER.debug("%s rejected the token; re-authenticating", path)
            if await self._relogin(force=True):
                payload = await self._call(path, body, method, auth, extra_headers)

        code = payload.get("code")
        if code == CODE_NO_PERMISSION:
            raise UgreenAuthError(f"{path}: {payload.get('msg')}")
        if raise_on_error and code != CODE_OK:
            raise UgreenError(f"{path}: {payload.get('msg')} (code {code})")
        return payload

    async def _call(
        self,
        path: str,
        body: dict[str, Any] | None,
        method: str,
        auth: bool,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        headers = self._headers(auth) | (extra_headers or {})
        kwargs: dict[str, Any] = {"headers": headers, "timeout": TIMEOUT}
        if method == "GET":
            kwargs["params"] = {k: v for k, v in (body or {}).items() if v is not None}
        else:
            kwargs["json"] = body if body is not None else {}

        # A noticeable share of outbound TLS handshakes on this connection are
        # reset mid-negotiation. Retrying transparently keeps a single dropped
        # poll from blanking every entity for a whole update interval.
        last: Exception | None = None
        for attempt in range(NETWORK_RETRIES):
            try:
                async with self._session.request(method, url, **kwargs) as resp:
                    if resp.status != 200:
                        raise UgreenError(f"{path}: HTTP {resp.status}")
                    payload = await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError) as err:
                last = err
                if attempt < NETWORK_RETRIES - 1:
                    _LOGGER.debug("%s failed (%s), retrying", path, err)
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                raise UgreenError(f"{path}: {err}") from err
            else:
                if not isinstance(payload, dict):
                    raise UgreenError(f"{path}: unexpected response {payload!r}")
                return payload
        raise UgreenError(f"{path}: {last}")

    # Kept so the rest of the module reads naturally for POST-shaped calls.
    async def _post(self, path: str, body: dict[str, Any] | None = None, **kw: Any):
        return await self._request(path, body, **kw)

    # -------------------------------------------------------------------- login

    async def _ensure_fresh_token(self) -> None:
        """Re-login before the short lived access token lapses.

        Tokens are JWTs valid for only ~20 minutes, which is far shorter than a
        typical HA session, so the expiry is read from the token itself and a
        fresh login is done pre-emptively rather than waiting for a rejection.
        """
        if not self._credentials or not self._token:
            return
        if self._expires_at and time.time() < self._expires_at - TOKEN_REFRESH_MARGIN:
            return
        await self._relogin()

    async def _relogin(self, *, force: bool = False) -> bool:
        """Log in again. `force` re-authenticates even if the token looks fresh.

        A token can be invalidated server-side before it expires -- logging in
        elsewhere with the same account does it -- so a rejection must not be
        short-circuited by the "still valid" check that exists only to stop
        concurrent callers from logging in twice.
        """
        async with self._relogin_lock:
            if (
                not force
                and self._expires_at
                and time.time() < self._expires_at - TOKEN_REFRESH_MARGIN
            ):
                return True
            if not self._credentials:
                return False
            try:
                await self.login(*self._credentials)
            except UgreenError as err:
                _LOGGER.debug("Re-login failed: %s", err)
                return False
            return True

    async def _seal(self, body: dict[str, Any]) -> dict[str, str]:
        """Wrap a body in the RSA envelope the sensitive endpoints expect."""
        payload = await self._post(
            "/app/v1/user/security/getSidInfo", {}, auth=False
        )
        data = payload.get("data") or {}
        sid, public_key = data.get("sid"), data.get("publicKey")
        if not sid or not public_key:
            raise UgreenError("getSidInfo did not return sid/publicKey")

        # The app strips PEM armour before decoding; the API returns bare base64
        # DER in practice, but tolerate the armoured form too.
        der = base64.b64decode(
            "".join(
                line
                for line in public_key.splitlines()
                if not line.startswith("-----")
            )
        )
        key = load_der_public_key(der)
        plaintext = json.dumps(body, separators=(",", ":")).encode()
        ciphertext = key.encrypt(plaintext, padding.PKCS1v15())
        return {"data": base64.b64encode(ciphertext).decode(), "sid": sid}

    async def login(self, email: str, password: str) -> dict[str, Any]:
        """Authenticate and store the resulting access token."""
        payload = await self._post(
            "/app/v1/user/login/emailPasswordLogin",
            {"email": email, "password": password},
            auth=False,
            encrypt=True,
            raise_on_error=False,
        )

        code = payload.get("code")
        if code != CODE_OK:
            msg = payload.get("msg") or "login rejected"
            _LOGGER.debug("Login rejected: %s (code %s)", msg, code)
            if code == CODE_BAD_CREDENTIALS:
                raise UgreenAuthError(msg)
            # Anything else means the request itself was not understood, which is
            # a bug here rather than a bad password -- don't blame the user.
            raise UgreenError(f"{msg} (code {code})")

        data = payload.get("data") or {}
        self._token = _find_first(data, ("accessToken", "access_token", "token"))
        self._refresh_token = _find_first(data, ("refreshToken", "refresh_token"))
        self._user_id = _find_first(data, ("userId", "user_id", "uid"))
        if not self._token:
            raise UgreenError(f"login succeeded but no access token found in {data!r}")
        self._credentials = (email, password)
        self._expires_at = _jwt_expiry(self._token)
        return payload


    # ------------------------------------------------------------------ queries

    async def get_user_info(self) -> Any:
        return (await self._post("/app/v1/user/getUserInfo", {})).get("data")

    async def get_devices(self) -> list[dict[str, Any]]:
        """Return the bound devices for this account."""
        data = (await self._request("/app/v1/variety/deviceList", method="GET")).get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("list", "records", "devices", "data"):
                if isinstance(data.get(key), list):
                    return data[key]
        _LOGGER.debug("deviceList returned an unrecognised shape: %r", data)
        return []

    async def get_product_model(self, **params: Any) -> Any:
        """Product metadata for a `serialNo` (productKey, model name, images).

        The `/v2` variant advertised in the app returns 404 on this deployment.
        """
        return (
            await self._post("/app/v1/product/model/latest_issued", params)
        ).get("data")

    async def get_app_info(self) -> dict[str, Any]:
        """Credentials for the RTCX gateway: appKey/appSecret/oauthClientId/authFlag.

        `platform` must be exactly `rtcx`; the endpoint answers `success: true`
        with a null payload for any other value instead of erroring.
        """
        payload = await self._request(
            "/app/v1/variety/getAppInfo",
            {"platform": APP_INFO_PLATFORM},
            method="GET",
            extra_headers={"x-ugreen-app-system": "android"},
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise UgreenError(f"getAppInfo returned no data: {payload}")
        return data

    async def oauth_authorize(self, client_id: str) -> str:
        """Mint a one-time OAuth code, which is what the gateway login consumes."""
        payload = await self._post(
            "/app/v1/oauth/authorize",
            {
                "client_id": client_id,
                "response_type": "code",
                "state": str(uuid.uuid4()),
            },
            # This endpoint is the one that insists on knowing the region.
            extra_headers={
                "serverNodeCode": self._region,
                "x-ugreen-app-system": "android",
            },
        )
        code = (payload.get("data") or {}).get("code")
        if not code:
            raise UgreenAuthError(f"oauth/authorize returned no code: {payload}")
        return code

    async def upload_wallpaper(
        self, image: bytes, file_name: str, device_code: str, product_serial: str
    ) -> None:
        """Put a picture into the account's wallpaper library.

        Three steps: ask for a presigned slot, PUT the bytes there, then tell the
        charger API the object belongs to this device. The presigned URL signs
        `content-md5`, so that header has to be sent exactly as promised.
        """
        digest = hashlib.md5(image).digest()
        pre = (
            await self._post(
                "/app/v1/system/file/upload-pre-info",
                {
                    "bizType": 100,
                    "fileMd5": digest.hex(),
                    "fileName": file_name,
                    "fileSize": len(image),
                },
            )
        ).get("data") or {}
        upload_url, file_key = pre.get("uploadUrl"), pre.get("fileKey")
        if not upload_url or not file_key:
            raise UgreenError(f"upload-pre-info gave no slot: {pre}")

        content_md5 = base64.b64encode(digest).decode()
        try:
            async with self._session.put(
                upload_url,
                data=image,
                headers={"Content-MD5": content_md5, "Content-Type": "image/jpeg"},
                timeout=TIMEOUT,
            ) as resp:
                if resp.status not in (200, 201, 204):
                    body = (await resp.text())[:300]
                    raise UgreenError(f"upload failed: HTTP {resp.status} {body}")
        except aiohttp.ClientError as err:
            raise UgreenError(f"upload failed: {err}") from err

        await self._post(
            "/app/v1/charger/file/wallPaper/save",
            {
                "contentMD5": content_md5,
                "objectKey": file_key,
                "fileName": file_name,
                "deviceUniqueCode": device_code,
                "fileSize": len(image),
                "productSerialNo": product_serial,
            },
        )

    async def get_wallpapers(self, device_code: str, product_serial: str) -> list[dict[str, Any]]:
        """Both the built-in pictures and the account's own uploads.

        `fileNameMd5` is the six-character id the charger itself knows a picture
        by, and is what the screensaver command refers to.
        """
        data = (
            await self._request(
                "/app/v1/charger/file/getWallPapers",
                {"productSerialNo": product_serial, "deviceUniqueCode": device_code},
                method="GET",
            )
        ).get("data") or {}
        out: list[dict[str, Any]] = []
        for group, stock in (("resourceWallpaperList", True), ("customerWallpaperList", False)):
            for item in data.get(group) or []:
                out.append(item | {"stock": stock})
        return out

    async def get_power_history(self, **params: Any) -> Any:
        return (await self._post("/app/v1/charger/data/powerHistory", params)).get("data")

    async def get_smart_modes(self, **params: Any) -> Any:
        return (await self._post("/app/v1/charger/mode/smart/list", params)).get("data")


def _jwt_expiry(token: str) -> float:
    """Read `exp` out of a JWT without verifying it. 0.0 if unreadable."""
    try:
        part = token.removeprefix("Bearer ").split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return float(claims["exp"])
    except Exception:  # noqa: BLE001 - a malformed token just means "unknown"
        return 0.0


def _find_first(obj: Any, names: tuple[str, ...]) -> Any:
    """Depth-first search for the first of `names` present in a nested mapping.

    The login response shape is not yet pinned down, so the token is located by
    name rather than by a fixed path.
    """
    if isinstance(obj, dict):
        for name in names:
            if obj.get(name):
                return obj[name]
        for value in obj.values():
            if (found := _find_first(value, names)) is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            if (found := _find_first(item, names)) is not None:
                return found
    return None
