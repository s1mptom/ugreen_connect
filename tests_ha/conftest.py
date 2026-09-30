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

# A second charger on the same account, a 160W, for the tests about choosing
# which to add. Its state is the one its owner logged in #2 after putting a new
# picture on it, in Custom Power, every port switched on.
SECOND_CODE = "FF7K0000000000002"
SECOND_IOT_ID = "another-iot-id"
SECOND: dict[str, Any] = {
    "productSerialNo": "030007",
    "deviceUniqueCode": SECOND_CODE,
    "deviceName": "UGREEN Nexode Pro X776",
    "deviceMac": "EC:1A:C3:00:00:02",
    "deviceType": "smart_charger",
    "extra": {"iotId": SECOND_IOT_ID, "networkStatus": 1, "onlineStatus": 1},
}
SECOND_PRODUCT: dict[str, Any] = {
    "productNo": "X776",
    "name": "UGREEN Nexode Pro 160W",
    "productKey": "another-product-key",
}
SECOND_STATE: dict[str, Any] = {
    "mcu_version": 4,
    "brightness": 8,
    "sleep_time": 5,
    "charging_mode": "custom",
    "screensaver": True,
    "screensaver_theme": 0,
    # Byte 33 is the screen's auto-rotate on this model, 01 for off.
    "auto_rotate": False,
    # Custom Power is running, so the priority block is not there to read --
    # and its two limits are, one byte a port.
    "priority": None,
    "custom": [
        {"port": "C-Cable", "limit": 15},
        {"port": "C1", "limit": 15},
        {"port": "C2 & A", "limit": 23},
    ],
    "wallpaper": "438EF2",
    "wallpapers": ["3E7F82", "EA1A3B", "FC1C77", "438EF2"],
    "port_outputs": {"C-Cable": True, "C1": True, "C2 & A": False},
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
    # 1.2.1 on an X783, as the state reply's first two bytes say it.
    "mcu_version": 55,
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
# And the 160W's, from its owner in #2: the built-in cable and C2 in use.
X776_REPORT = (
    "aa06002000c70005006301000000000000000035001f00a401"
    "00000000000000050005005c4d"
)


# check_upgrade's answer for an X783 at MCU 55, as it came on 28 September
# 2026 -- all but the link, which was signed and is not ours to keep.
FIRMWARE_OFFER: dict[str, Any] = {
    "id": 104,
    "softwareSerialNo": "030002",
    "versionCode": 58,
    "versionName": "V1.2.3",
    "fileName": "ota_package_X783_MV58_v1.2.3_updatepackage.bin",
    "status": 50,
    "fileUrl": "https://dl.example.invalid/ota_package_X783_MV58.bin?auth_key=signed",
    "fileSize": 307704,
    "fileMd5": "da5c01c2fa8e690e28bd3a1bcfba6094",
    "changeList": "1. Fixed some known issues",
    "upgradeMode": 1,
    "publishTime": "2026-09-20 19:28:17",
}


def _reading(model: str | None) -> dict[str, Any]:
    """What the real client builds out of a report, names and all."""
    ports = parse_power_frame(X776_REPORT if model == "X776" else X783_REPORT, model)
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
        # What the account holds; a test adds SECOND to put two on it.
        self.devices: list[dict[str, Any]] = [dict(DEVICE)]
        # Firmware on offer, per product: offered to any MCU version older
        # than its own, as the real endpoint does. Nothing by default.
        self.firmware: dict[str, dict[str, Any]] = {}
        self.firmware_checks: list[tuple[str, int]] = []

    async def login(self, *_args: Any) -> None:
        return None

    async def get_devices(self) -> list[dict[str, Any]]:
        return [dict(device) for device in self.devices]

    async def get_product_model(self, **kwargs: Any) -> dict[str, Any] | None:
        self.product_calls += 1
        if not self.product_answers:
            return None
        return dict(SECOND_PRODUCT if kwargs.get("serialNo") == "030007" else PRODUCT)

    async def get_wallpapers(self, *_args: Any) -> list[dict[str, Any]]:
        return []

    async def check_firmware(self, product_serial: str, version_code: int) -> dict[str, Any] | None:
        self.firmware_checks.append((product_serial, version_code))
        offer = self.firmware.get(product_serial)
        if offer is None or offer["versionCode"] <= version_code:
            return None
        return dict(offer)


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
        # Which chargers were asked for a reading, in order: a charger that was
        # not added must never appear here.
        self.polled: list[str] = []
        self.second_state: dict[str, Any] = dict(SECOND_STATE)
        # What each progress question is answered with, in turn; the last one
        # repeats. An exception in the list is raised instead. On "done", the
        # charger comes back at the version it was sent.
        self.upgrade_answers: list[Any] = [(0, 0), (1, 40), (1, 99), (2, 100)]
        self.upgrade_asks = 0
        self.firmware_sent: list[dict[str, Any]] = []
        # Every conversation with a charger, in order, as (iot_id, what).
        self.talk: list[tuple[str, str]] = []

    async def async_login(self) -> None:
        return None

    async def async_power(self, iot_id: str, model: str | None = None) -> dict[str, Any] | None:
        self.polled.append(iot_id)
        self.talk.append((iot_id, "power"))
        return _reading(model) if self.power_answers else None

    async def async_device_state(self, iot_id: str, _model: str | None = None) -> dict[str, Any]:
        self.state_reads += 1
        return dict(self.second_state if iot_id == SECOND_IOT_ID else self.state)

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

    async def async_start_firmware_update(self, iot_id: str, **sent: Any) -> None:
        self.talk.append((iot_id, "firmware"))
        self.firmware_sent.append({"iot_id": iot_id, **sent})

    async def async_upgrade_status(self, iot_id: str) -> tuple[int, int] | None:
        self.talk.append((iot_id, "upgrade_status"))
        index = min(self.upgrade_asks, len(self.upgrade_answers) - 1)
        self.upgrade_asks += 1
        answer = self.upgrade_answers[index]
        if isinstance(answer, Exception):
            raise answer
        if answer and answer[0] == 2 and self.firmware_sent:
            self.state = {**self.state, "mcu_version": self.firmware_sent[-1]["version"]}
        return answer


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
