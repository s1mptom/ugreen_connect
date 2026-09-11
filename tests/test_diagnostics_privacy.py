"""Nothing that belongs to the household leaves in the diagnostics download.

The README asks owners of other chargers to attach this file to a public issue,
so the bar is not "no credentials" but "nothing identifying". What makes an
unknown charger supportable is its model and its frames; the MAC, the cloud id,
the unit's own code and the name of the Wi-Fi network it joined say nothing
about a model and everything about whose desk it is on.

Two of those are easy to lose by accident. `async_redact_data` only ever looks
at values, and three sections of the payload are *keyed* by the unit code -- so
a value redacted everywhere else sits in plain sight as a key. And the raw
frames are bytes rather than values, so the reply to the SSID query carries the
network name as ASCII where redaction by key name cannot see it; the frames are
published from an allowlist for that reason, and a query added to the client
later must not join it by default.

Home Assistant's own `async_redact_data` is the only thing stubbed, with its
real algorithm, so the module under test is the shipped one.
"""

import asyncio
import json
import sys
import types
from collections.abc import Mapping
from pathlib import Path

import pytest

REDACTED = "**REDACTED**"

_COMPONENT = (
    Path(__file__).resolve().parents[1] / "custom_components" / "ugreen_connect"
)


def _redact(data, to_redact):
    """Home Assistant's algorithm, copied rather than imported."""
    if not isinstance(data, (Mapping, list)):
        return data
    if isinstance(data, list):
        return [_redact(v, to_redact) for v in data]
    out = {**data}
    for key, value in out.items():
        if value is None or (isinstance(value, str) and not value):
            continue
        if key in to_redact:
            out[key] = REDACTED
        elif isinstance(value, Mapping):
            out[key] = _redact(value, to_redact)
        elif isinstance(value, list):
            out[key] = [_redact(v, to_redact) for v in value]
    return out


def _module():
    """`diagnostics.py` with only Home Assistant stubbed out."""
    for name, attrs in (
        ("homeassistant", {}),
        ("homeassistant.components", {}),
        ("homeassistant.components.diagnostics", {"async_redact_data": _redact}),
        ("homeassistant.const", {"CONF_EMAIL": "email", "CONF_PASSWORD": "password"}),
        ("homeassistant.core", {"HomeAssistant": object}),
    ):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module

    package = types.ModuleType("ugc")
    package.__path__ = []
    package.UgreenConfigEntry = object
    sys.modules["ugc"] = package

    def device_key(device):
        return str(
            device.get("deviceUniqueCode")
            or (device.get("extra") or {}).get("iotId")
            or ""
        ) or None

    coordinator = types.ModuleType("ugc.coordinator")
    coordinator.device_key = device_key
    sys.modules["ugc.coordinator"] = coordinator

    for stem in ("protocol", "diagnostics"):
        source = (_COMPONENT / f"{stem}.py").read_text()
        module = types.ModuleType(f"ugc.{stem}")
        module.__package__ = "ugc"
        sys.modules[f"ugc.{stem}"] = module
        exec(compile(source, f"{stem}.py", "exec"), module.__dict__)
    return sys.modules["ugc.diagnostics"]


# Values a real download would carry if nothing removed them. The frames are the
# shapes the client actually asks for: a power report, a device state, a product
# version -- and the two that answer with the household's own details.
MAC = "AA:BB:CC:DD:EE:FF"
UNIT = "FF7H0039306100089"
IOT_ID = "JuSTiZWwzabFoehKLgWT8UojudBS3LQjz7YOnQvH"
SSID = "PavelHomeWiFi"
EMAIL = "someone@example.com"
PASSWORD = "hunter2"
SSID_FRAME = "AA0800" + "MyWifi123".encode().hex().upper()
SERIAL_FRAME = "AA0500" + UNIT.encode().hex().upper()


def _payload():
    return {
        "devices": [
            {
                "deviceUniqueCode": UNIT,
                # Despite the name this is the *model* code, not the unit's, and
                # it is the one identifier a report is useless without.
                "productSerialNo": "030002",
                "deviceName": "UGREEN Nexode Pro X783",
                "deviceMac": MAC,
                "extra": {"iotId": IOT_ID, "onlineStatus": 1},
            }
        ],
        "detail": {UNIT: {"productNo": "X783", "name": "Nexode Pro 300W"}},
        "power": {
            UNIT: {
                "total": 39.0,
                "ssid": SSID,
                "firmware": "1.2.1",
                "ports": {"C3": {"voltage": 27.9, "current": 1.4, "power": 39.0}},
            }
        },
        "power_errors": {},
    }


def _download(payload=None, frames=None):
    module = _module()
    payload = _payload() if payload is None else payload
    rtcx = types.SimpleNamespace(
        last_frames={
            IOT_ID: {
                "AA/6": "AA06003F0033",
                "AA/1": "AA01004A0037",
                "AA/10": "AA0A00030102",
                "AA/8": SSID_FRAME,
                "AA/5": SERIAL_FRAME,
            }
            if frames is None
            else frames
        }
    )
    entry = types.SimpleNamespace(
        runtime_data=types.SimpleNamespace(data=payload, rtcx=rtcx),
        data={"email": EMAIL, "password": PASSWORD, "region": "europe"},
    )
    return module.async_get_config_entry_diagnostics(None, entry)


@pytest.mark.parametrize(
    ("what", "value"),
    [
        ("the account", EMAIL),
        ("the password", PASSWORD),
        ("the charger's MAC", MAC),
        ("its cloud id", IOT_ID),
        ("its unit code", UNIT),
        ("the household's network name", SSID),
    ],
)
def test_nothing_identifying_travels(what, value):
    assert value not in json.dumps(asyncio.run(_download())), f"{what} is in the file"


def test_what_makes_a_charger_supportable_stays():
    """The whole point of the file; redacting it into uselessness is the other failure."""
    text = json.dumps(asyncio.run(_download()))

    assert '"030002"' in text, "the model code is what a report is about"
    assert "X783" in text
    assert '"C3"' in text and "39.0" in text, "the readings"
    assert "1.2.1" in text, "the firmware"


def test_the_sections_keyed_by_the_unit_code_are_renamed_not_blanked():
    """Redaction reaches values, and these are keyed by the thing being hidden.

    A stand-in rather than a blanking, so a report can still be followed from a
    device through to its readings.
    """
    data = asyncio.run(_download())["data"]

    for section in ("detail", "power"):
        assert list(data[section]) == ["device_0"]
    assert data["detail"]["device_0"]["productNo"] == "X783"


def test_only_frames_on_the_allowlist_are_published():
    """A query added to the client later must not travel by default.

    `GET_SN` and `GET_WIFI_SSID` are the two that answer with the household's
    own details, as ASCII inside the bytes where no redaction by key name can
    reach them.
    """
    frames = asyncio.run(_download())["frames"]["device_0"]

    assert "AA/8" not in frames, "the SSID reply carries the network name"
    assert "AA/5" not in frames, "the serial reply carries the unit code"
    assert set(frames) == {"AA/1", "AA/6", "AA/10"}


def test_a_second_charger_does_not_get_the_first_one_s_frames():
    """Two on one account is the likely case, and misattribution is the one
    failure the frames exist to prevent: a report about an unsupported charger
    carrying bytes from the supported one next to it."""
    second_unit, second_iot = "KK9P1XYZ", "IOTID_SECOND"
    payload = _payload()
    payload["devices"].append(
        {
            "deviceUniqueCode": second_unit,
            "productSerialNo": "030007",
            "deviceName": "UGREEN Nexode Pro X776",
            "deviceMac": "11:22:33:44:55:66",
            "extra": {"iotId": second_iot, "onlineStatus": 1},
        }
    )
    payload["power"][second_unit] = {"total": 0.0, "ports": {}}

    module = _module()
    rtcx = types.SimpleNamespace(
        last_frames={
            IOT_ID: {"AA/6": "AA06003F0033"},
            second_iot: {"AA/6": "AA0600200C7"},
        }
    )
    entry = types.SimpleNamespace(
        runtime_data=types.SimpleNamespace(data=payload, rtcx=rtcx),
        data={"email": EMAIL, "password": PASSWORD, "region": "europe"},
    )
    frames = asyncio.run(module.async_get_config_entry_diagnostics(None, entry))["frames"]

    assert frames["device_0"]["AA/6"] == "AA06003F0033"
    assert frames["device_1"]["AA/6"] == "AA0600200C7"


def test_the_coordinator_s_own_payload_is_not_mutated():
    """The download is a copy; redacting in place would blank the running data."""
    payload = _payload()
    asyncio.run(_download(payload=payload))

    assert payload["power"][UNIT]["ssid"] == SSID
    assert payload["devices"][0]["deviceMac"] == MAC
