# UGREEN Connect for Home Assistant

Home Assistant integration for chargers managed by the **UgreenConnect** app
(`*.ugreeniot.com`). Developed against a **UGREEN Nexode Pro X783**.

Live per-port **voltage, current and power**, plus device online state.

> Not affiliated with, endorsed by, or supported by UGREEN. Trademarks belong to
> their respective owners.

## Why it exists

The charger has **no local API at all** — a full TCP 1–65535 scan finds nothing
open, and there is no mDNS or SSDP. Its only outbound path is its own cloud.
Local control exists solely over BLE. So a cloud integration is the only way to
get readings into Home Assistant without a Bluetooth proxy next to the device.

## What you get

| Entity | Notes |
|---|---|
| `sensor.<device>_c1_power` … | one per port that has ever drawn power |
| `sensor.<device>_c1_voltage`, `..._current` | same ports |
| `sensor.<device>_total_power` | sum across ports; `work_mode` in attributes |
| `sensor.<device>_status` | `online` / `offline` |

Ports are reported in the order `C1 C2 C3 C4 C5 C6 A1 DC`. Slots the hardware
does not have stay at zero and get no entities.

## Install

**HACS** → three-dot menu → *Custom repositories* → add this repo as type
*Integration* → install → restart Home Assistant → *Settings → Devices &
Services → Add integration → UGREEN Connect*.

Manual: copy `custom_components/ugreen_connect` into your `config/` and restart.

You log in with your normal UGREEN account e-mail and password, and pick the
region your account belongs to (the same one the app shows).

## How it works

Two clouds are involved.

**Account API** (`api2.ugreeniot.com` for Europe). Credentials are sent inside an
RSA envelope: `getSidInfo` hands out a short-lived `sid` plus an RSA-2048 public
key, the whole login body is encrypted with PKCS#1 v1.5 and posted as
`{data, sid}`. This yields the account access token and the device inventory.

**RTCX/Polaris gateway** (`eu-gateway.ugreeniot.com`) carries telemetry. It wants
its own token, obtained by trading a one-time OAuth code:

```
GET  /app/v1/variety/getAppInfo?platform=rtcx  -> appKey, appSecret, oauthClientId, authFlag
POST /app/v1/oauth/authorize                   -> data.code            (single use)
POST /client/account/third/login               -> data.accessToken     (the iotToken, 24 h)
```

The gateway login's `password` field carries **that OAuth code**, not the user's
password — `pwdType: "4"`, `accountType: "6"`. Gateway requests are signed
Alibaba-API-Gateway style: `HMAC-SHA256(appSecret, stringToSign)` in
`x-ca-signature`, over `x-ca-key`, `x-ca-nonce` and `x-ca-timestamp`.

`appKey`/`appSecret` are **fetched at runtime under your own account** and are
not embedded in this repository.

### The charger's binary protocol

Readings are not exposed as named properties. The device tunnels a small binary
protocol through a single property, `PT_data`:

```
TYPE(1) CMD(1) LEN(2, big endian) PAYLOAD(LEN) CRC16(2)
TYPE: 0xAA query   0xEE device notify   0x11 setting
CRC:  CRC-16/MODBUS, low byte first
```

Writing a query frame to `PT_data` (`thing/properties/set`) makes the device
answer; the reply becomes the property's value, read back with
`thing/properties/get/all`. `GET_POWER_INFO` (`0xAA 0x06`) answers with eight
7-byte port records:

| offset | field | encoding |
|---|---|---|
| `7*i + 0` | voltage | U16 big endian, tenths of a volt |
| `7*i + 2` | current | U16 big endian, tenths of an amp |
| `7*i + 4` | power | U16 big endian, tenths of a watt |
| `56 + i` | handshake protocol | U8 |

Since the property retains its last value indefinitely, readings older than five
minutes are treated as "no reading" rather than as live.

## Limitations

- Cloud polling only; default interval 60 s. Each poll is two gateway calls plus
  a short wait for the device to answer.
- Logging in from the app with the same account can invalidate the integration's
  token. It re-authenticates on rejection, so this is self-healing.
- Read-only for now. Brightness, charging mode and per-port switching are known
  commands (`SET_BRIGHTNESS`, `SET_CHARGING_MODE`, `SET_PORT_CONTROL`) but are
  not exposed as entities yet.

## Legal

Written for interoperability, using the exception in Directive 2009/24/EC Art. 6
(UK: CDPA s.50B). No UGREEN code is redistributed, and no credentials of theirs
are embedded. MIT licensed.
