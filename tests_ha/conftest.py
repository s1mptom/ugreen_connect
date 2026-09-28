"""Fixtures for the tests that do need Home Assistant.

The suite in `tests/` loads the protocol and the session tracker straight from
their source, with Home Assistant nowhere near them. That covers the rules and
covers nothing about whether the integration starts, or about the paths that
only appear when a poll goes wrong.

Here only the two clouds are replaced. The coordinator, the platforms, the
entity and device registries and the state machine all run as they do on a real
installation -- which is the point: the two defects found this week were found
by running the thing on a charger and restarting it twice, not by reading it.

The fakes are plain classes rather than mocks so a test can say "the next power
report goes missing" without reaching into call-order bookkeeping.

Running these by hand needs the Home Assistant image, or a `home-assistant-frontend`
installed beside it: the integration declares `frontend` in its manifest, and
that package is not a dependency of the `homeassistant` wheel. The official
image carries it, which is why CI does not notice.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ugreen_connect.const import CONF_REGION, DOMAIN
from custom_components.ugreen_connect.protocol import parse_power_frame

DEVICE_CODE = "FF7J0000000000001"
IOT_ID = "an-iot-id"

DEVICE: dict[str, Any] = {
    "productSerialNo": "030002",
    "deviceUniqueCode": DEVICE_CODE,
    "deviceName": "UGREEN Nexode Pro X783",
    "deviceMac": "EC:1A:C3:00:00:01",
    "deviceType": "smart_charger",
    "extra": {"iotId": IOT_ID, "networkStatus": 1, "onlineStatus": 1},
}

PRODUCT: dict[str, Any] = {
    "productNo": "X783",
    "name": "UGREEN Nexode Pro 300W",
    "productKey": "a-product-key",
}

# 0xFD is every box the app offers, ticked at once -- bit 1 is the one it has
# nothing to put in.
ALL_PROTOCOLS = [
    "Apple5V/2.4A",
    "AFC",
    "SCP",
    "UFCS",
    "5-11V PPS",
    "5-21V PPS",
    "AVS",
]

STATE: dict[str, Any] = {
    "brightness": 100,
    "sleep_time": 0,
    "charging_mode": "custom",
    "screensaver": True,
    "screensaver_theme": 1,
    "screensaver_flag": 0,
    "wallpaper": "31F207",
    # Which ports `priority` charges first -- nothing, since the charger here
    # runs `custom`, where the byte is a limit rather than a mask.
    "priority": None,
    # And the DC port's settings, which are `dc_turbo`'s alone.
    "dc_turbo": None,
    # The charging mode is `custom` here, so the parameter block decodes and
    # the six group sensors are created. Switching away leaves them in place
    # and unavailable, which is what
    # `test_leaving_custom_mode_makes_its_sensors_unavailable` drives.
    # The names are what parse_custom_mode reads out of the mask beside them,
    # spelled out rather than derived, so a fixture that drifts from the real
    # decoder is visible here. Nothing asserts on them today.
    "custom": [
        {"port": "C1", "limit": 60, "mask": 0xFD, "protocols": ALL_PROTOCOLS},
        {"port": "C2", "limit": 140, "mask": 0xFD, "protocols": ALL_PROTOCOLS},
        {
            "port": "C3",
            "limit": 30,
            "mask": 0x25,
            "protocols": ["Apple5V/2.4A", "AFC", "5-11V PPS"],
        },
        {
            "port": "C4",
            "limit": 20,
            "mask": 0x0D,
            "protocols": ["Apple5V/2.4A", "AFC", "SCP"],
        },
        {"port": "C5", "limit": 15, "mask": 0x01, "protocols": ["Apple5V/2.4A"]},
        {
            "port": "C6+A",
            "limit": 30,
            "mask": 0x25,
            "protocols": ["Apple5V/2.4A", "AFC", "5-11V PPS"],
        },
    ],
    "wallpapers": ["31F207"],
}


# The same verified 63-byte report the protocol tests use, so the port names in
# these tests are decided by ports_for rather than by the fake. A charger whose
# model nobody could name has to come out of here numbered.
X783_REPORT = (
    "aa06003f00330000000001003300000000010117000900fb010000000000000000000000"
    "00000000000000000000000000000000000000000000000000000500000000541b"
)


def _reading(model: str | None) -> dict[str, Any]:
    """What the real client builds out of a report, names and all."""
    ports = parse_power_frame(X783_REPORT, model)
    assert ports is not None
    return {
        "ports": ports,
        "total": round(sum(values["power"] for values in ports.values()), 1),
    }


class FakeApi:
    """The account cloud, answering from constants.

    `product_answers` is what the model lookup does; setting it False is how a
    test gets a charger nobody can name.
    """

    def __init__(self) -> None:
        self.product_answers = True
        self.product_calls = 0

    async def login(self, *_args: Any) -> None:
        return None

    async def get_devices(self) -> list[dict[str, Any]]:
        return [dict(DEVICE)]

    async def get_product_model(self, **_kwargs: Any) -> dict[str, Any] | None:
        self.product_calls += 1
        return dict(PRODUCT) if self.product_answers else None

    async def get_wallpapers(self, *_args: Any) -> list[dict[str, Any]]:
        return []


class FakeRtcx:
    """The device gateway.

    `power_answers` is the one a test turns off: the charger answers into a
    single cloud property, and anything else asking at that moment can take the
    reply meant for this poll. That is ordinary rather than exceptional, and it
    is the path `_carry` exists for.
    """

    def __init__(self) -> None:
        self.power_answers = True
        # Mutable, so a test can put the charger into another mode. `stale`
        # is the same lever a write pulls on the real client: the coordinator
        # holds the screen settings for a minute, so without it a changed
        # state is simply not re-read.
        self.state: dict[str, Any] = dict(STATE)
        self.stale = False
        self.last_frames: dict[str, dict[str, str]] = {}
        # What the real client learns from a state reply and the coordinator
        # writes down; a test moves it to say the charger was seen in a mode.
        self.mode_params: dict[str, str] = {}
        self.mode_writes: list[tuple[str, int, str | None]] = []
        self.priority_writes: list[tuple[str, list[str], str | None]] = []
        self.turbo_writes: list[dict[str, Any]] = []
        self.state_reads = 0

    async def async_login(self) -> None:
        return None

    async def async_power(self, _iot_id: str, model: str | None = None) -> dict[str, Any] | None:
        return _reading(model) if self.power_answers else None

    async def async_device_state(self, _iot_id: str, _model: str | None = None) -> dict[str, Any]:
        self.state_reads += 1
        return dict(self.state)

    async def async_set_charging_mode(
        self, iot_id: str, mode: int, model: str | None = None
    ) -> None:
        # Recorded rather than ignored: the real one needs the model to know
        # how long the parameter block is, and nothing else would notice if
        # the entity stopped passing it.
        self.mode_writes.append((iot_id, mode, model))

    async def async_set_priority_ports(
        self, iot_id: str, ports: list[str], model: str | None = None
    ) -> None:
        # The charger takes the choice, so the read-back that follows finds it.
        # Not at once: a cloud round trip is seconds, and a second press made
        # meanwhile has to find this one still going.
        await asyncio.sleep(0.05)
        self.priority_writes.append((iot_id, list(ports), model))
        self.state = {**self.state, "priority": list(ports)}
        self.stale = True

    async def async_set_dc_turbo(
        self,
        iot_id: str,
        model: str | None = None,
        *,
        voltage: int | None = None,
        always_on: bool | None = None,
    ) -> None:
        # As the real client does: the block is the one last read, taken when
        # the write starts, and the whole of it goes to the charger. A second
        # write begun before the first was read back carries the first's byte
        # as it was before, and puts it back.
        turbo = dict(self.state["dc_turbo"] or {})
        await asyncio.sleep(0.05)
        asked = {"voltage": voltage, "always_on": always_on}
        asked = {name: value for name, value in asked.items() if value is not None}
        self.turbo_writes.append(asked)
        self.state = {**self.state, "dc_turbo": {**turbo, **asked}}
        self.stale = True

    def mode_params_snapshot(self) -> dict[str, str]:
        return dict(self.mode_params)

    def state_is_stale(self, _iot_id: str) -> bool:
        return self.stale

    def state_was_read(self, _iot_id: str) -> None:
        self.stale = False

    async def async_firmware_version(self, _iot_id: str) -> str:
        return "1.2.1"

    async def async_text_query(self, *_args: Any, **_kwargs: Any) -> str:
        return "a network"

    def ota_state(self) -> dict[str, Any]:
        return {"available": None, "progress": None, "module": None, "size": None}


@pytest.fixture(autouse=True)
def _nothing_remembered_between_tests():
    """`logsafe` is process-wide, so one test's identifiers would scrub the next's."""
    from custom_components.ugreen_connect import logsafe

    logsafe._known.clear()
    logsafe._compiled = None
    yield
    logsafe._known.clear()
    logsafe._compiled = None


@pytest.fixture(autouse=True)
def _event_loop_needs_a_socketpair(socket_enabled):
    """Let the loop build itself.

    pytest-homeassistant-custom-component blocks sockets, which is right: a test
    has no business dialling out. On Windows the event loop is itself built out
    of a TCP socketpair, so blocking that stops the tests before they start.
    Nothing here reaches a network -- both clouds are replaced, and the shared
    session with them.
    """
    yield


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations):
    """Home Assistant will not load a custom component in tests without this."""
    yield


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def rtcx() -> FakeRtcx:
    return FakeRtcx()


@pytest.fixture
def entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="an account",
        data={
            "email": "someone@example.invalid",
            "password": "not a real one",
            CONF_REGION: "europe",
        },
    )


@pytest.fixture
async def started(hass, entry: MockConfigEntry, api: FakeApi, rtcx: FakeRtcx):
    """The integration, set up for real, with both clouds replaced."""
    from unittest.mock import patch

    entry.add_to_hass(hass)
    with (
        # Never used -- both clients are fakes -- but building a real session
        # opens a socket, and these tests are not allowed any.
        patch("custom_components.ugreen_connect.async_get_clientsession"),
        patch("custom_components.ugreen_connect.UgreenApi", return_value=api),
        patch("custom_components.ugreen_connect.RtcxClient", return_value=rtcx),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry
