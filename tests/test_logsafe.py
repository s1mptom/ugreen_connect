"""Nothing of the household in the log.

The README asks people mapping a charger to post their diagnostics and
sometimes their log, so what the integration knows about them must not be in
either. These hold the net under every line: the identifiers it knows are
replaced wherever they turn up -- in a message, an argument, a traceback, a
frame's hex, a line some other logger wrote -- and nothing else is touched.
"""

import logging
import sys

import pytest
from conftest import logsafe

IOT = "a1B2c3D4e5-iot"
UNIT = "FF7J0000000000001"
MAC = "EC:1A:C3:00:00:01"
EMAIL = "someone@example.invalid"


@pytest.fixture(autouse=True)
def _nothing_known():
    logsafe._known.clear()
    logsafe._compiled = None
    yield
    logsafe._known.clear()
    logsafe._compiled = None


def test_a_charger_s_ids_become_its_tag():
    tag = logsafe.remember_charger(IOT, UNIT, MAC)
    assert tag == f"charger {logsafe.charger_tag(IOT)}"
    text = logsafe.scrub(f"poll of {UNIT} ({IOT}) at {MAC} failed")
    assert text == f"poll of {tag} ({tag}) at <mac> failed"


def test_a_charger_without_a_cloud_id_is_still_covered():
    """Its unit code and MAC are what would be in a message about it."""
    tag = logsafe.remember_charger(None, UNIT, MAC)
    assert tag == f"charger {logsafe.charger_tag(UNIT)}"
    assert logsafe.scrub(f"{UNIT} {MAC}") == f"{tag} <mac>"


def test_every_spelling_is_caught():
    """The MAC in any case and without colons, the id as hex inside a frame."""
    tag = logsafe.remember_charger(IOT, UNIT, MAC)
    assert logsafe.scrub(MAC.lower()) == "<mac>"
    assert logsafe.scrub("aa06ec1ac300000101ff") == "aa06<mac>01ff"
    assert logsafe.scrub("aa0500" + UNIT.encode().hex().upper() + "ffff") == f"aa0500{tag}ffff"


def test_a_value_is_matched_whole_and_as_written():
    """Inside a longer word it is some other word; in another case, another value."""
    logsafe.remember("Lab-2G", "<wifi>")
    assert logsafe.scrub("on Lab-2G now") == "on <wifi> now"
    assert logsafe.scrub("Lab-2GHz") == "Lab-2GHz"
    assert logsafe.scrub("lab-2g") == "lab-2g"


def test_an_ordinary_word_is_not_hunted():
    """An SSID of `Home` would rewrite "Home Assistant" -- and give itself away."""
    logsafe.remember("Home", "<wifi>")
    logsafe.remember("lab", "<wifi>")
    text = "Home Assistant at /usr/src/homeassistant, the lab bench"
    assert logsafe.scrub(text) == text


def test_nothing_known_changes_nothing():
    assert logsafe.scrub(f"{UNIT} {EMAIL}") == f"{UNIT} {EMAIL}"


def test_an_answer_is_described_not_quoted():
    """A login answer is the tokens; an error message about it says its shape."""
    answer = {"refreshToken": "eyJsecret", "userId": 42}
    described = logsafe.describe(answer)
    assert "eyJsecret" not in described and "42" not in described
    assert described == "an object with keys ['refreshToken', 'userId']"
    assert logsafe.describe(None) == "nothing"
    assert logsafe.describe([1, 2]) == "a list of 2"


@pytest.fixture
def installed():
    """The record factory as the integration leaves it, put back afterwards."""
    make = logging.getLogRecordFactory()
    was = logsafe._installed
    logsafe._installed = False
    logsafe.install()
    yield
    logging.setLogRecordFactory(make)
    logsafe._installed = was


def test_every_logger_is_covered_home_assistant_s_too(installed, caplog):
    """A traceback of an error raised here is written by Home Assistant's loggers."""
    logsafe.remember(EMAIL, "<account>")
    tag = logsafe.remember_charger(IOT, UNIT)
    caplog.set_level(logging.DEBUG)

    logging.getLogger("custom_components.ugreen_connect.api").warning("login as %s", EMAIL)
    try:
        raise RuntimeError(f"cloud said no to {UNIT}")
    except RuntimeError:
        logging.getLogger("homeassistant.components.websocket_api").exception("Unexpected")

    assert EMAIL not in caplog.text and "<account>" in caplog.text
    assert UNIT not in caplog.text, "the traceback carried it"
    assert tag in caplog.text


def test_a_traceback_that_had_to_be_cleaned_loses_its_exception(installed):
    """A handler formatting the exception afresh would put the words back."""
    logsafe.remember_charger(IOT, UNIT)
    try:
        raise RuntimeError(f"no to {UNIT}")
    except RuntimeError:
        record = logging.getLogger("x").makeRecord(
            "x", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )
    assert record.exc_info is None
    assert UNIT not in record.exc_text


def test_a_line_that_cannot_be_checked_is_withheld_not_raised(installed):
    """A broken line used to be a `--- Logging error ---`; it must not become
    an exception in whatever was logging, nor slip through unchecked."""
    logsafe.remember(EMAIL, "<account>")
    record = logging.getLogger("x").makeRecord(
        "x", logging.DEBUG, __file__, 1, f"{EMAIL} %s %s", (1,), None
    )
    assert record.getMessage() == logsafe.WITHHELD


def test_it_goes_in_once():
    make = logging.getLogRecordFactory()
    was = logsafe._installed
    logsafe._installed = False
    try:
        logsafe.install()
        once = logging.getLogRecordFactory()
        logsafe.install()
        assert logging.getLogRecordFactory() is once
    finally:
        logging.setLogRecordFactory(make)
        logsafe._installed = was
