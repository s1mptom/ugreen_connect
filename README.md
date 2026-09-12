# UGREEN Connect for Home Assistant

Home Assistant integration for chargers managed by the **UgreenConnect** app
(`*.ugreeniot.com`). Developed against a **UGREEN Nexode Pro 300W (X783)** —
sold as the *Nexode Pro Smart Display Desktop Charger, 300W, 8-Port, GaN*.
`X783` is the code the app knows it by, and the one that turns up in entity ids;
it is not printed on the box.

Live per-port **voltage, current and power**, plus everything the app's screen
settings can do: brightness, screen-off time, charging mode, and the whole
screensaver — clock style, time format and wallpaper, your own included.

![The charger on a dashboard](docs/dashboard.png)

*That dashboard is [`docs/dashboard.yaml`](docs/dashboard.yaml) — one screenful,
ready to paste. Its power-history card is `statistics-graph-chart-card` from
HACS; without it that one card shows an error and everything else still works,
so delete it or swap in the built-in `history-graph`.*

> Not affiliated with, endorsed by, or supported by UGREEN. Trademarks belong to
> their respective owners.

## Why it exists

The charger has **no local API at all** — a full TCP 1–65535 scan finds nothing
open, and there is no mDNS or SSDP. Its only outbound path is its own cloud.
Local control exists solely over BLE. So a cloud integration is the only way to
get readings into Home Assistant without a Bluetooth proxy next to the device.

## Install

**HACS** → three-dot menu → *Custom repositories* → add `s1mptom/ugreen_connect`
as type *Integration* → install → **restart Home Assistant** → *Settings →
Devices & Services → Add integration → UGREEN Connect*.

Manual: copy `custom_components/ugreen_connect` into your `config/` and restart.

Sign in with your normal UGREEN account e-mail and password, and pick the region
your account belongs to — the same one the app shows. Accounts are not shared
between regions.

Requires Home Assistant **2024.7** or newer.

## What you get

Entity ids below are written `<device>`; in practice that is the charger's name,
e.g. `sensor.ugreen_nexode_pro_x783_c1_power`.

### Readings

| Entity | Notes |
|---|---|
| `sensor.<device>_c1_power` … | one per port the charger reports; a 300W's are C1–C6, A1, DC |
| `sensor.<device>_c1_voltage`, `_c1_current` | same ports |
| `sensor.<device>_c1_protocol` | negotiated fast-charge protocol: PD, PPS, QC, AFC, FCP, UFCS, AVS |
| `sensor.<device>_c1_session_energy` | watt-hours delivered to whatever is plugged into that port now |
| `sensor.<device>_c1_session_charge` | the same session read as milliamp-hours into a battery |
| `sensor.<device>_total_power` | sum across ports; firmware and Wi-Fi SSID in its attributes |
| `sensor.<device>_cloud_status` | `online` / `offline`; MAC in its attributes |
| `update.<device>_firmware` | installed version, and whether one is waiting |

### Controls

| Entity | Values |
|---|---|
| `number.<device>_screen_brightness` | 0–100 % |
| `select.<device>_screen_off_time` | 1, 5, 10, 30 minutes, or always on |
| `select.<device>_charging_mode` | adaptive power, thermal safe, DC turbo, priority |
| `switch.<device>_screensaver` | the clock the screen shows once it sleeps |
| `select.<device>_time_format` | 12- or 24-hour |
| `select.<device>_clock_style` | the two faces the charger draws |
| `select.<device>_wallpaper` | any picture in your UGREEN library, or none |

The choices match the app's own, and every write is read back from the device on
the next poll rather than assumed.

`custom` is a real charging mode and is reported when the device is in it, but it
cannot be selected here: setting a mode carries that mode's parameter block, and
only the app's editor can compose a custom one. Home Assistant replays a block it
has watched the charger running; the only block it ever composes is an empty one,
for a mode it has not seen.

That applies to the presets too. A mode carries its own settings — `priority`
keeps its chosen port there, DC Turbo a setting of its own — and the charger holds no
copy, so the write is what decides them. Once a mode has been seen running it is
remembered, across restarts, and put back whenever it is selected.

Before it has been seen, selecting it sends empty parameters, and the charger
either loses what that mode was configured with or refuses the change outright —
DC Turbo does the latter, so it simply will not switch. The log says so when it
happens. If the settings are gone, set that mode up again in the app; if the
change did not take, select the mode in the app. Either way leave the charger in
it for a minute or so — that is the soonest its settings are read again, and on a
slow poll interval it is longer.

A 300W reports its ports in the order `C1 C2 C3 C4 C5 C6 A1 DC`, and a 160W as
`C-Cable C1 C2 A`; the order comes from a table keyed on `productNo`, and a
model with no entry gets `P1..Pn` counted from the report's own length. A port
keeps its entities once it has been seen, so unplugging a cable does not delete
its history.

### Charging sessions

A session is a *bout of charging*: it starts when current begins to flow and its
total stays on screen afterwards, so *how much did that get?* is still answerable
once the phone is back in your pocket. The next bout starts a new session from zero.
Both session sensors carry the same detail in their attributes: `charging`,
`started`, `ended`, `duration`, `peak_power`, `average_power`, `last_draw` and
`protocol`.

It is a bout rather than the span between plugging in and unplugging because **this
charger cannot tell you a device has been removed.** It holds the port live and keeps
the negotiated USB-PD contract alive with nothing but a cable in the socket — 5 V and
`PD`, indistinguishable from an attached device that is not currently drawing. The
report's per-port occupancy byte says "present" for a bare cable too. So the end of a
session is decided by the current going away and staying away, which is what
*Session ends after* configures; a port that does report itself empty — some do —
ends its session at once instead of waiting that out.

That setting is a real trade-off, and the right value depends on what lives on the
port. A phone left at 100% tops itself up every so often, and those sips have to land
inside the same session, or a 5 mAh trickle would start a "new session" and the charge
that actually went into the phone would disappear off the card. The two-hour default
clears that comfortably. The cost is at the other end: two devices swapped on the same
cable less than two hours apart are counted as one session.

The charger reports no energy total, so this is integrated from the per-port
wattage, and three things about the device shape how:

- **Watt-hours are the measurement; milliamp-hours are a conversion.** A port may
  be handing over 5, 9 or 28 V, so the charge that reaches a battery depends on
  that battery's own voltage — which the charger cannot know. Set it under
  *Battery voltage* in the options; the 3.85 V default suits phones and earbuds
  and is meaningless for a laptop.
- **The cloud drops out for minutes at a time.** An outage leaves a session
  exactly as it was rather than reading as an unplug, and the missing minutes are
  not filled in with the last known wattage.
- **Neither current nor power can be trusted on its own.** A full device still
  reports the 0.1 A measurement quantum, which at 9 V looks like 0.9 W and would
  invent close to a whole battery over a night; a bare cable produces the mirror
  image, a stray 0.3 A that the charger itself reports as 0.0 W. Charge counts as
  flowing only when both are above their floors: 0.15 A and 0.5 W.

Sessions survive a restart of Home Assistant. If the device on the port changed
while it was down, or the downtime outlasted the idle window, the old total is
left as it was rather than added to.

**What the device does not offer.** Its TSL model declares `WiFiRSSI`,
`errorCode`, `IPAddress` and more, but this charger never populates them — asking
for those identifiers returns the same four properties it always reports. There
is no temperature sensor of any kind, and no energy total of its own -- the
charger reports watts and nothing else. The **Energy** sensors integrate those
readings here instead, one per port and one for the charger, so the Energy
dashboard can be fed without a Riemann-sum helper. Take the ports or the
charger, not both: together they count every watt-hour twice.

## The screensaver card

The entities above are enough to automate with, but the screensaver is easier to
set the way the app sets it: one panel, with a live preview of the strip the
charger will actually show.

<img src="docs/screensaver-card.png" alt="The screensaver card" width="340">

The card is **served by the integration itself**, so there is nothing to add in
HACS and no resource to register — install the integration, restart, and the
card is available. Add it to a dashboard with *Add card → Manual*:

```yaml
type: custom:ugreen-wallpaper-card
```

That is the whole configuration for a single charger. With more than one, name
the device:

```yaml
type: custom:ugreen-wallpaper-card
device_id: 0123456789abcdef0123456789abcdef   # Settings → Devices → your charger, from the URL
title: Screensaver                            # optional; the card's heading
```

| Option | Default | Meaning |
|---|---|---|
| `device_id` | first charger found | which charger the card controls |
| `title` | `Screensaver` | heading next to the on/off switch |

Turning the switch off hides the settings, exactly as the app does — there is
nothing to configure while the screensaver is off.

**If the card does not appear** after installing, reload the browser with a hard
refresh (Ctrl/Cmd + Shift + R). The dashboard can render once before the
integration has finished starting, and shows *Configuration error* until the
page is loaded again.

### Your own wallpaper

*Upload a picture* opens a crop window over the photo you choose — drag, zoom
and rotate under it. The screen is **560 × 170**, wide enough that a photo almost
never suits it as taken, and zoom cannot go below the size that fills the window,
so a wallpaper never ends up with empty edges.

The charger keeps **one** slot of its own alongside the built-in pictures, so
uploading replaces whatever custom picture it was holding.

For automations there is a service taking a local `path`, a `url`, or base64 in
`image`:

```yaml
action: ugreen_connect.set_wallpaper
data:
  device_id: 0123456789abcdef0123456789abcdef
  path: /config/www/desk.jpg
```

Pictures given to the service, rather than to the card, are cover-cropped from
the centre.

Uploading needs **Pillow**, which any Home Assistant with `default_config`
already has. Everything else in the integration works without it.

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
`thing/properties/get/all`. `GET_POWER_INFO` (`0xAA 0x06`) answers with one
7-byte record per port -- eight on a 300W, four on a 160W:

| offset | field | encoding |
|---|---|---|
| `7*i + 0` | voltage | U16 big endian, tenths of a volt |
| `7*i + 2` | current | U16 big endian, tenths of an amp |
| `7*i + 4` | power | U16 big endian, tenths of a watt |
| `56 + i` | handshake protocol | U8 |

Since the property retains its last value indefinitely, readings older than five
minutes are treated as "no reading" rather than as live. A reply is waited for by
polling until the frame is the one that was asked for, because until then the
property still holds the previous command's answer.

Wallpapers are the one thing not sent as bytes. The picture is uploaded to
UGREEN's own storage (`upload-pre-info` → presigned `PUT` → `wallPaper/save`) and
the charger is then handed the resulting id and URL through a `PIC_data`
property, which is what makes it download the file.

## Settings

*Settings → Devices & Services → UGREEN Connect → the cog on the account row*:

<img src="docs/options.png" alt="The options dialog" width="480">

| Setting | Default | Notes |
|---|---|---|
| Poll every | 5 s | how often a reading arrives, measured start to start; the wait for the charger to answer comes out of it, not on top |
| Battery voltage | 3.85 V | only used to read a session's watt-hours back as milliamp-hours; a laptop's pack is far higher |
| Charging efficiency | 90 % | how much of what leaves the port reaches the cell; the rest is heat |
| Session ends after | 120 min | how long a port must deliver nothing before its charging session is finished; see [Charging sessions](#charging-sessions) for the trade-off |
| Region | as set up | only if the account itself moved servers; the password is re-checked first |
| Debug snapshot | off | writes the unedited cloud payload to `ugreen_connect_debug.json` |

Five seconds keeps the wattage live enough to watch a laptop charge. It is also
a lot of traffic against someone else's API, so raise it if you would rather be
gentle — nothing else depends on the rate.

## Tests

The session rules -- what starts one, what ends it, what a dropout means -- are the
one part of this with enough edge cases to be worth pinning down, so they live in
`session.py` with no Home Assistant imports and are tested on their own:

```
pip install pytest && pytest tests -q
```

Everything else needs a real charger and a real cloud account to say anything, and
is checked against both rather than mocked.

## Translating

Everything the integration says goes through
`custom_components/ugreen_connect/translations/`. Copy `en.json`, name it for
your language, translate the values, and open a pull request. English, German
and Russian exist so far.

The dashboard card keeps its own text in one table at the top of
`www/ugreen-wallpaper-card.js`: copy the `en` block, key it by language code,
and translate. Missing keys fall back to English, so a partial translation is
fine.

## Limitations

- **Cloud polling only**, five seconds apart by default — see *Settings* above.
- Logging in from the app with the same account can invalidate the integration's
  token. It re-authenticates on rejection, so this is self-healing.
- Per-port switching (`SET_PORT_CONTROL`) and the `custom` charging-mode editor
  are decoded but not exposed. `FACTORY_RESET` is deliberately left out.
- Settings changed from the phone app show up here on the next poll, wallpapers
  included: a picture uploaded there is named and previewed within a minute,
  because an id the library cannot account for sends the integration to read it
  again. The reverse is not true — **the app caches**, and keeps showing its old
  value until it is force-stopped and reopened.

## Contributing

Issues and pull requests are welcome, especially from owners of other UGREEN
chargers — the port table in `protocol.py` covers the two models anyone has
measured, and the byte offsets beside it are the 300W's, which is the first
thing another model will disagree about. A
diagnostics download (*Settings → Devices & Services → UGREEN Connect →
Download diagnostics*) is the most useful thing to attach; it has credentials
redacted.

## Legal

Written for interoperability, using the exception in Directive 2009/24/EC Art. 6
(UK: CDPA s.50B). No UGREEN code is redistributed, and no credentials of theirs
are embedded. MIT licensed.
