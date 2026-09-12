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
# Smallest pause between a reply and the next request. The poll period is
# measured start-to-start, so when a poll already overruns the configured
# period this is what stops it becoming a back-to-back loop against the cloud.
MIN_POLL_GAP: Final = 1.0

# What to do when nothing is charging. Polling somebody else's cloud is this
# integration's whole running cost, and a charger with nothing plugged into it
# has nothing to say that anyone is watching for. The rate goes back up the
# moment a port draws again, or a poll fails -- an outage is precisely when
# someone is waiting for the charger to come back.
IDLE_SCAN_FACTOR: Final = 6
IDLE_SCAN_MAX: Final = 60

# The screen settings and the charging mode only change when someone opens the
# app, and asking for them costs a round trip of its own. Read once a minute
# rather than beside every wattage -- except right after a write, when the copy
# is known to be out of date and waiting out the timer would mean watching your
# own change take a minute to appear.
DEVICE_STATE_INTERVAL: Final = 60
MAX_SCAN_INTERVAL: Final = 900

# --- Charging sessions ------------------------------------------------------
# Watt-hours are what the charger actually delivers; milliamp-hours are what
# people think in. Converting between them needs a battery voltage and a
# conversion loss, neither of which the charger can know, so both are options.
# 3.85 V is the nominal cell voltage of essentially every phone and earbud; a
# laptop on USB-PD has a far higher pack voltage, and its milliamp-hour figure
# is meaningless until this is set to match.
CONF_NOMINAL_VOLTAGE: Final = "nominal_voltage"
CONF_EFFICIENCY: Final = "efficiency"
DEFAULT_NOMINAL_VOLTAGE: Final = 3.85
DEFAULT_EFFICIENCY: Final = 90

# A reading is only continuous with the one before it if it arrived roughly on
# schedule; this multiple of the poll period is where "roughly" stops.
SESSION_GAP_FACTOR: Final = 4

# How long a port has to draw nothing before its charging session counts as over.
# The charger cannot say whether a device is still attached -- it holds the port live
# for a bare cable -- so this is the only thing that can end a session. See session.py
# for what it is trading off.
CONF_IDLE_END: Final = "session_idle_end"
DEFAULT_IDLE_END: Final = 120  # minutes

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

# How many times the product lookup may come back empty before the ports are
# numbered instead of named.
#
# The model decides what a port is called, and a name that changes later means
# a second set of entities beside the first, with the history left behind on
# the old ones. So a charger whose model is not known yet is left alone for a
# poll rather than published under names that may be taken back -- but not
# forever: if that endpoint is simply down, numbered ports beat no ports, and
# the decision then stays put.
MODEL_LOOKUP_ATTEMPTS: Final = 3
# How far a reading that did arrive may be carried when the next one does not.
#
# The charger answers into a single cloud property, so anything else asking at
# the same moment -- a second Home Assistant, the phone app being opened -- can
# take the reply meant for this one. Blanking every entity of a charger for a
# cycle reads like the device fell off the shelf, and nothing it last said is
# less true for being a few seconds old.
#
# Both bounds are deliberately short. This covers a reply going astray, not a
# charger that has been unplugged: past either, "unavailable" is the honest
# answer again.
RETAIN_MISSES: Final = 2
RETAIN_SECONDS: Final = 60

# The charger's screen, in pixels. Its stock pictures are stored rotated, but
# what the app uploads is this way round.
WALLPAPER_SIZE: Final[tuple[int, int]] = (560, 170)

# Firmware version and SSID never change between polls; re-read them rarely.
STATIC_INFO_INTERVAL: Final = 3600

# Charging presets. "custom" is left out on purpose: setting a mode carries that
# mode's parameter block, and composing a custom block is the app editor's
# job -- all this can do is replay one it has watched the charger running. (An
# earlier note here said the presets leave those bytes at zero. They do not:
# `priority` was seen carrying a setting in the first of them, which is why
# sending zeros used to erase it.)
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

# Links to uploaded wallpapers are signed and last about ten minutes, which is
# why previews are served through the integration rather than pointed at the
# CDN -- the list itself only has to be current enough to name what the charger
# is showing.
WALLPAPER_LIST_INTERVAL: Final = 900
# ...and how often to go looking when the charger names a picture the library
# has never mentioned, which is what a picture uploaded from the phone app looks
# like. Rate limited, because a picture that has been replaced in the library
# stays on the charger and would otherwise be chased on every poll.
WALLPAPER_MISS_INTERVAL: Final = 60

# How long to let the charger download a picture before pointing the screensaver
# at it.
PICTURE_SETTLE_SECONDS: Final = 5

# Dumped next to configuration.yaml on every refresh while `debug_dump` is on.
# It is the raw, unmodified cloud payload and is what the entity layer is built
# from -- see the integration README.
DEBUG_DUMP_FILE: Final = "ugreen_connect_debug.json"
CONF_DEBUG_DUMP: Final = "debug_dump"

# Where the models each charger has been found to be are kept between starts.
#
# A charger does not become a different model, and the answer decides what its
# ports are called -- which decides their unique ids. Asking again on every
# restart leaves a hole exactly where it hurts: a charger with a year of
# history, restarted while that endpoint is having a bad minute, would spend
# its attempts and come back numbered beside the names it has always had.
#
# Storage rather than the config entry, because this is a note to itself and
# not a setting. Everything in the entry is something the owner chose, and the
# reload on writing it is there because those choices only take effect on one.
MODEL_STORE_VERSION: Final = 1
MODEL_STORE_KEY: Final = f"{DOMAIN}.models"

# The parameter block each charging mode was last seen running with. Kept
# across restarts because a mode's parameters can only be learned while that
# mode is in force: without this, the first mode change after every start goes
# out with empty parameters, and the charger either loses what that mode was
# configured with or refuses the change outright.
# Its own store rather than a field in the models one, which holds a flat
# name per charger and would need a migration to hold anything else.
PARAMS_STORE_VERSION: Final = 1
PARAMS_STORE_KEY: Final = f"{DOMAIN}.mode_params"
