"""What a user, the network around Home Assistant, or the cloud can get out of it.

From the HACS review and the audit that followed it: the preview view, the
attributes every user can read, the size of a picture anyone may send, the
debug snapshot, and the entry's title.
"""

from __future__ import annotations

import io
import json
import os
import stat
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.ugreen_connect import image_proxy, logsafe, services
from custom_components.ugreen_connect.const import DEBUG_DUMP_FILE, DOMAIN
from tests_ha.conftest import DEVICE_CODE

LIBRARY = [{
    "fileNameMd5": "31F207", "url": "https://cdn.example/31F207.jpg?auth_key=signed",
    "fileName": "IMG_2041.jpg", "fileSize": 12345, "stock": False,
}]


class _Reply:
    """A CDN answer that arrives in chunks, as aiohttp's does.

    A `read(n)` here would give the first chunk only -- which is the bug a
    single read had: every picture cut short at 16 KB or less.
    """

    def __init__(self, status=200, kind="image/jpeg", body=b"\xff\xd8" + b"j" * 50_000):
        self.status = status
        self.content_type = kind
        self.content = SimpleNamespace(iter_chunked=self._chunks, read=self._first)
        self._body = body

    async def _chunks(self, size):
        for at in range(0, len(self._body), 4096):
            yield self._body[at : at + 4096]

    async def _first(self, limit=-1):
        return self._body[:4096]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _Session:
    """The CDN: answers every request with `reply`, and notes how it was asked."""

    def __init__(self, reply):
        self.reply = reply
        self.asked: list[tuple[str, dict]] = []

    def get(self, url, **kwargs):
        self.asked.append((url, kwargs))
        return self.reply


@pytest.fixture
def library(api):
    calls = []

    async def _library(*args):
        calls.append(args)
        return [dict(item) for item in LIBRARY]

    api.get_wallpapers = _library
    return calls


async def _ask(hass, monkeypatch, entry_id, image_id, reply=None):
    session = _Session(reply or _Reply())
    monkeypatch.setattr(image_proxy, "async_get_clientsession", lambda _hass: session)
    view = image_proxy.UgreenWallpaperView()
    response = await view.get(SimpleNamespace(app={"hass": hass}), entry_id, image_id)
    return response, session, view


async def _with_library(hass, started, library):
    await started.runtime_data.async_refresh()
    await hass.async_block_till_done()


# --- The preview view --------------------------------------------------------


async def test_an_id_the_account_does_not_have_never_reaches_the_cloud(
    hass, started, library, monkeypatch
):
    """Any signed-in user used to be able to make it re-read the library on demand."""
    await _with_library(hass, started, library)
    before = len(library)
    response, session, _ = await _ask(hass, monkeypatch, started.entry_id, "NOSUCH")
    assert response.status == 404
    assert len(library) == before
    assert session.asked == []


async def test_the_library_is_re_read_at_most_once_a_minute(hass, started, library):
    await _with_library(hass, started, library)
    coordinator = started.runtime_data
    before = len(library)
    for _ in range(5):
        await coordinator.async_wallpaper_url("31F207", refresh=True)
    assert len(library) - before == 1


async def test_a_picture_is_fetched_over_https_without_following_redirects(
    hass, started, library, monkeypatch
):
    await _with_library(hass, started, library)
    response, session, view = await _ask(hass, monkeypatch, started.entry_id, "31F207")
    assert response.status == 200
    # Whole, though it came in chunks.
    assert len(response.body) == 50_002
    assert session.asked[0][1]["allow_redirects"] is False
    # Kept for this account only.
    assert list(view._cache) == [(started.entry_id, "31F207")]


@pytest.mark.parametrize(
    ("reply", "why"),
    [
        (_Reply(kind="text/html"), "not a picture"),
        (_Reply(body=b"x" * (image_proxy.MAX_PICTURE_BYTES + 10)), "too large"),
    ],
)
async def test_what_the_cdn_sends_back_is_checked(
    hass, started, library, monkeypatch, reply, why
):
    await _with_library(hass, started, library)
    response, _, _ = await _ask(hass, monkeypatch, started.entry_id, "31F207", reply)
    assert response.status == 502, why


async def test_a_link_that_is_not_https_is_not_followed(hass, started, api, monkeypatch):
    async def _plain(*_args):
        return [{**LIBRARY[0], "url": "http://192.168.1.1/admin"}]

    api.get_wallpapers = _plain
    await started.runtime_data.async_refresh()
    response, session, _ = await _ask(hass, monkeypatch, started.entry_id, "31F207")
    assert response.status == 502
    assert session.asked == []


async def test_an_entry_that_is_not_this_integration_s_is_a_404(
    hass, started, library, monkeypatch
):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    other = MockConfigEntry(domain="sun", title="not ours")
    other.add_to_hass(hass)
    response, _, _ = await _ask(hass, monkeypatch, other.entry_id, "31F207")
    assert response.status == 404
    response, _, _ = await _ask(hass, monkeypatch, "no-such-entry", "31F207")
    assert response.status == 404


# --- What every user can read -------------------------------------------------


def _state(hass, domain, unique_id):
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    return hass.states.get(entity_id) if entity_id else None


async def test_no_household_identifier_is_an_attribute(hass, started, library):
    await _with_library(hass, started, library)
    status = _state(hass, "sensor", f"{DEVICE_CODE}_status")
    assert "iot_id" not in status.attributes
    assert "mac" not in status.attributes
    total = _state(hass, "sensor", f"{DEVICE_CODE}_total_power")
    assert "ssid" not in total.attributes
    wallpaper = _state(hass, "select", f"{DEVICE_CODE}_wallpaper")
    picture = wallpaper.attributes["wallpapers"][0]
    assert "name" not in picture
    assert "url" not in picture
    assert "IMG_2041" not in json.dumps(dict(wallpaper.attributes))


async def test_the_wifi_network_has_a_sensor_that_is_off_by_default(hass, started):
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{DEVICE_CODE}_wifi_network"
    )
    assert entity_id is not None
    entry = er.async_get(hass).async_get(entity_id)
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(entity_id) is None


# --- A picture anyone may send -------------------------------------------------


def _png(width: int, height: int) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("L", (width, height)).save(out, format="PNG")
    return out.getvalue()


def test_a_picture_that_decodes_to_too_many_pixels_is_refused(monkeypatch):
    """The header is read first; a small file describing a huge picture stops there."""
    monkeypatch.setattr(services, "MAX_PICTURE_PIXELS", 100)
    with pytest.raises(HomeAssistantError, match="too large"):
        services._to_wallpaper(_png(20, 20))
    monkeypatch.setattr(services, "MAX_PICTURE_PIXELS", 40_000_000)
    assert services._to_wallpaper(_png(20, 20))


def test_something_that_is_not_a_picture_is_refused():
    with pytest.raises(HomeAssistantError, match="not a picture"):
        services._to_wallpaper(b"%PDF-1.7 not an image at all")


async def test_a_picture_too_large_to_take_is_refused_before_decoding(
    hass, started, monkeypatch
):
    from homeassistant.helpers import device_registry as dr

    monkeypatch.setattr(services, "MAX_PICTURE_BYTES", 10)
    device = next(
        d for d in dr.async_entries_for_config_entry(dr.async_get(hass), started.entry_id)
        if (DOMAIN, DEVICE_CODE) in d.identifiers
    )
    decoded = []
    monkeypatch.setattr(services, "_to_wallpaper", lambda raw: decoded.append(raw))
    with pytest.raises(HomeAssistantError, match="too large"):
        await hass.services.async_call(
            DOMAIN, "set_wallpaper",
            {"device_id": device.id, "image": "QUFBQUFBQUFBQUFBQUFBQQ=="}, blocking=True,
        )
    assert decoded == []


# --- The debug snapshot ---------------------------------------------------------


async def test_the_snapshot_is_written_once_for_the_owner_without_links(
    hass, started, library
):
    coordinator = started.runtime_data
    coordinator._debug_dump = True
    coordinator._dumped = False
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    path = hass.config.path(DEBUG_DUMP_FILE)
    try:
        first = os.stat(path).st_mtime_ns
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        assert "auth_key" not in text
        assert '"url"' not in text
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert os.stat(path).st_mtime_ns == first, "written again on the next poll"
    finally:
        os.remove(path)


async def test_a_snapshot_left_from_before_goes_when_the_option_is_off(hass, entry, api, rtcx):
    from unittest.mock import patch

    path = hass.config.path(DEBUG_DUMP_FILE)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{}")
    entry.add_to_hass(hass)
    with (
        patch("custom_components.ugreen_connect.async_get_clientsession"),
        patch("custom_components.ugreen_connect.UgreenApi", return_value=api),
        patch("custom_components.ugreen_connect.RtcxClient", return_value=rtcx),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert not os.path.exists(path)


def test_the_coordinator_writes_no_snapshot_unless_asked():
    import inspect

    from custom_components.ugreen_connect.coordinator import UgreenCoordinator

    default = inspect.signature(UgreenCoordinator.__init__).parameters["debug_dump"].default
    assert default is False


# --- The entry's title ---------------------------------------------------------


def test_the_account_is_named_without_being_spelt_out():
    assert logsafe.account_title("someone@example.invalid") == "s***@example.invalid"
    assert logsafe.account_title("not-an-address") == "UGREEN account"
    assert logsafe.account_title("") == "UGREEN account"


async def test_an_entry_titled_with_the_e_mail_is_renamed(hass, api, rtcx):
    from unittest.mock import patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.ugreen_connect.const import CONF_REGION

    email = "someone@example.invalid"
    entry = MockConfigEntry(
        domain=DOMAIN, title=email,
        data={"email": email, "password": "not a real one", CONF_REGION: "europe"},
    )
    entry.add_to_hass(hass)
    with (
        patch("custom_components.ugreen_connect.async_get_clientsession"),
        patch("custom_components.ugreen_connect.UgreenApi", return_value=api),
        patch("custom_components.ugreen_connect.RtcxClient", return_value=rtcx),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.title == "s***@example.invalid"
