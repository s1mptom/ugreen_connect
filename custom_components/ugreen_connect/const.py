"""Constants for the UGREEN Connect integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ugreen_connect"

CONF_REGION: Final = "region"
CONF_LANGUAGE: Final = "language"

# Regional API endpoints, as published by /app/v1/system/country/list.
# `serverNodeCode` is what the app calls the region; the charger itself talks to
# a matching signalling host (europe -> eu-sig.ugreeniot.com).
REGIONS: Final[dict[str, str]] = {
    "europe": "https://api2.ugreeniot.com",
    "america": "https://api3.ugreeniot.com",
    "asia": "https://api1.ugreeniot.com",
    "china": "https://apicn.ugreeniot.com",
}
DEFAULT_REGION: Final = "europe"
DEFAULT_LANGUAGE: Final = "en-US"

# The API answers 200 OK for everything and signals real status in the body.
CODE_OK: Final = 100000
CODE_NO_PERMISSION: Final = 100003
CODE_MISSING_HEADER: Final = 100013
CODE_NO_SID: Final = 200010

# Every poll is two gateway calls plus a wait for the charger to answer, so this
# is a trade between a live-looking wattage and how hard someone else's cloud is
# leaned on. Five seconds suits watching a laptop charge; the options flow lets
# anyone who would rather be gentle raise it without touching the code.
DEFAULT_SCAN_INTERVAL: Final = 5
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 900

# --- RTCX/Polaris gateway (live telemetry) ---------------------------------
# The gateway envelope uses an underscore locale, unlike the account API header.
GATEWAY_LANGUAGE: Final = "en_US"
# `platform` must be exactly this; `android`/`iot` return data: null.
APP_INFO_PLATFORM: Final = "rtcx"
# The gateway reports success as 200 in the body, not the API's 100000.
GATEWAY_OK: Final = 200
# iotTokens last 24 h; renew this far ahead of expiry.
RTCX_TOKEN_MARGIN: Final = 300
# The charger answers a PT_data query asynchronously -- give it time to land
# before reading the property back.
POWER_SETTLE_SECONDS: Final = 2.0
# ...and how many times to look before giving up on that reply.
POWER_POLL_ATTEMPTS: Final = 3
# PT_data keeps its last value indefinitely, so anything older than this is
# treated as "no reading" rather than as a live one.
PT_DATA_MAX_AGE: Final = 300

# The charger's screen, in pixels. Its stock pictures are stored rotated, but
# what the app uploads is this way round.
WALLPAPER_SIZE: Final[tuple[int, int]] = (560, 170)

# Port order of the X783's power report, from the app's own port table.
X783_PORTS: Final[tuple[str, ...]] = ("C1", "C2", "C3", "C4", "C5", "C6", "A1", "DC")

# The charging protocol each port negotiated, reported one byte per port at the
# tail of the power frame.
HANDSHAKE_PROTOCOL: Final[dict[int, str]] = {
    0: "none", 1: "QC", 2: "AFC", 3: "FCP", 4: "UFCS", 5: "PD", 6: "PPS", 7: "AVS",
}

# Firmware version and SSID never change between polls; re-read them rarely.
STATIC_INFO_INTERVAL: Final = 3600

# Charging presets. "custom" is left out on purpose: it needs the 35 parameter
# bytes the presets leave at zero, and those are only meaningful alongside the
# app's own mode editor.
# Named as the app names them, so the two agree on screen.
CHARGING_MODES: Final[dict[int, str]] = {
    0: "adaptive_power",
    1: "thermal_safe",
    2: "dc_turbo",
    3: "priority",
    4: "custom",
}
SELECTABLE_MODES: Final[tuple[str, ...]] = (
    "adaptive_power", "thermal_safe", "dc_turbo", "priority",
)

# The two bytes after the screensaver's on/off flag. Both were settled by
# changing them in the app and reading the frame it sent: picking 12- or 24-hour
# moves the first, and Clock Style 1 / 2 moves the second. (An earlier guess had
# the first as a clock position, which it is not.)
TIME_FORMATS: Final[dict[int, str]] = {0: "12h", 1: "24h"}
CLOCK_STYLES: Final[dict[int, str]] = {0: "style_1", 1: "style_2"}

# Screen Off Time is plain minutes; zero means the screen never sleeps. These
# are the app's own choices, confirmed by tapping each one and reading the frame
# it sent -- note it offers 10 minutes, not 15. The device itself takes any
# value up to 255, so more can be added here without touching anything else.
SLEEP_NEVER: Final = 0
SLEEP_OPTIONS: Final[dict[str, int]] = {
    "1_min": 1,
    "5_min": 5,
    "10_min": 10,
    "30_min": 30,
    "always_on": SLEEP_NEVER,
}

# Links to uploaded wallpapers are signed and time-limited, so the list is
# re-read often enough that a preview in the dashboard stays loadable.
WALLPAPER_LIST_INTERVAL: Final = 900

# How long to let the charger download a picture before pointing the screensaver
# at it.
PICTURE_SETTLE_SECONDS: Final = 5

# Dumped next to configuration.yaml on every refresh while `debug_dump` is on.
# It is the raw, unmodified cloud payload and is what the entity layer is built
# from -- see the integration README.
DEBUG_DUMP_FILE: Final = "ugreen_connect_debug.json"
CONF_DEBUG_DUMP: Final = "debug_dump"
