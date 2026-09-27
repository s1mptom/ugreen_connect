"""Nothing of the household in the log.

The README asks people mapping a charger to post their debug log, so what the
integration logs is published. These hold the net under every line: the
identifiers it knows are replaced wherever they turn up, in a message, in an
argument, in a traceback, in a frame's hex.
"""

import logging

import pytest
from conftest import logsafe

IOT = "a1B2c3D4e5-iot"
UNIT = "FF7J0000000000001"
MAC = "EC:1A:C3:00:00:01"
EMAIL = "someone@example.invalid"


@pytest.fixture(autouse=True)
def _nothing_known():
    logsafe._known.clear()
    logsafe._pattern = None
    yield
    logsafe._known.clear()
    logsafe._pattern = None


def test_a_charger_s_ids_become_its_tag():
    tag = logsafe.remember_charger(IOT, UNIT, MAC)
    assert tag == f"charger {logsafe.charger_tag(IOT)}"
    text = logsafe.scrub(f"poll of {UNIT} ({IOT}) at {MAC} failed")
    assert text == f"poll of {tag} ({tag}) at <mac> failed"


def test_every_spelling_is_caught():
    """A MAC without colons, any case, and the id as ASCII hex inside a frame."""
    tag = logsafe.remember_charger(IOT, UNIT, MAC)
    assert logsafe.scrub("ec1ac3000001") == "<mac>"
    assert logsafe.scrub(IOT.upper()) == tag
    assert logsafe.scrub("aa0500" + UNIT.encode().hex().upper() + "ffff") == f"aa0500{tag}ffff"


def test_a_value_too_short_to_be_safe_is_left_alone():
    """An SSID of `lab` would take the word out of every line."""
    logsafe.remember("lab", "<wifi>")
    assert logsafe.scrub("the lab bench") == "the lab bench"


def test_nothing_known_changes_nothing():
    assert logsafe.scrub(f"{UNIT} {EMAIL}") == f"{UNIT} {EMAIL}"


def _logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.filters.clear()
    return logger


def test_the_filter_reaches_arguments_and_tracebacks(caplog):
    logsafe.remember(EMAIL, "<account>")
    logsafe.remember_charger(IOT, UNIT)
    logger = _logger("ugreen_logsafe_test.sub")
    logsafe.install("ugreen_logsafe_test")
    caplog.set_level(logging.DEBUG, logger="ugreen_logsafe_test")

    logger.warning("login as %s rejected", EMAIL)
    try:
        raise RuntimeError(f"cloud said no to {UNIT}")
    except RuntimeError:
        logger.exception("poll failed")

    assert EMAIL not in caplog.text and "<account>" in caplog.text
    assert UNIT not in caplog.text, "the traceback carried it"
    assert logsafe.charger_tag(IOT) in caplog.text


def test_it_is_put_on_every_logger_once():
    child = _logger("ugreen_logsafe_once.a.b")
    logsafe.install("ugreen_logsafe_once")
    logsafe.install("ugreen_logsafe_once")
    assert child.filters.count(logsafe.FILTER) == 1
    assert logsafe.FILTER in logging.getLogger("ugreen_logsafe_once").filters
    assert logsafe.FILTER not in logging.getLogger("ugreen_logsafe_other").filters
