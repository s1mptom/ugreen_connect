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

DEFAULT_SCAN_INTERVAL: Final = 60

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
POWER_SETTLE_SECONDS: Final = 2.5
# PT_data keeps its last value indefinitely, so anything older than this is
# treated as "no reading" rather than as a live one.
PT_DATA_MAX_AGE: Final = 300

# Port order of the X783's power report, from the app's own port table.
X783_PORTS: Final[tuple[str, ...]] = ("C1", "C2", "C3", "C4", "C5", "C6", "A1", "DC")

# Dumped next to configuration.yaml on every refresh while `debug_dump` is on.
# It is the raw, unmodified cloud payload and is what the entity layer is built
# from -- see the integration README.
DEBUG_DUMP_FILE: Final = "ugreen_connect_debug.json"
CONF_DEBUG_DUMP: Final = "debug_dump"
