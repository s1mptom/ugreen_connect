"""Nothing of the household in what the integration logs.

The README asks people mapping a charger to post their diagnostics and
sometimes their log, so what the integration knows about them must not be in
either. These hold the net under every line: the identifiers it knows are
replaced wherever they turn up in its records -- a message, an argument, a
traceback, a stack, a frame's hex -- and nothing else is touched, least of all
anybody else's records.
"""

import logging
import sys
import threading

import pytest
from conftest import logsafe

IOT = "a1B2c3D4e5-iot"
UNIT = "FF7J0000000000001"
MAC = "EC:1A:C3:00:00:01"
EMAIL = "Someone@Example.invalid"
SCOPE = "ugreen_logsafe_test"


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


def test_case_does_not_matter():
    """The cloud may send the e-mail back lower-cased; an entity id has the
    unit code in lower case."""
    tag = logsafe.remember_charger(IOT, UNIT)
    logsafe.remember(EMAIL, "<account>")
    assert logsafe.scrub("user someone@example.invalid not found") == "user <account> not found"
    assert logsafe.scrub(f"sensor.ugreen_{UNIT.lower()}_power") == f"sensor.ugreen_{tag}_power"


def test_a_frame_s_hex_is_caught_in_either_case():
    """`body.hex()` is lower case, and it is how every body is logged."""
    tag = logsafe.remember_charger(IOT, UNIT, MAC)
    spelled = UNIT.encode().hex()
    assert logsafe.scrub("aa0500" + spelled + "ffff") == f"aa0500{tag}ffff"
    assert logsafe.scrub("AA0500" + spelled.upper() + "FFFF") == f"AA0500{tag}FFFF"
    assert logsafe.scrub("aa06ec1ac300000101ff") == "aa06<mac>01ff"


def test_a_wi_fi_name_in_any_script_is_caught():
    """Decoded, a Cyrillic name has to be matched as UTF-8 bytes in a frame."""
    name = "Дом_WiFi_5G"
    logsafe.remember(name, "<wifi>")
    assert logsafe.scrub(f"joined {name}") == "joined <wifi>"
    assert logsafe.scrub("aa08" + name.encode().hex()) == "aa08<wifi>"


def test_bytes_that_do_not_decode_are_caught_as_bytes():
    raw = b"\xff\xfeNet\x80"
    logsafe.remember_bytes(raw, "<wifi>")
    assert logsafe.scrub("aa08" + raw.hex()) == "aa08<wifi>"


def test_a_value_is_matched_whole():
    """Inside a longer word it is some other word."""
    logsafe.remember("Lab-2G-Net", "<wifi>")
    assert logsafe.scrub("on Lab-2G-Net now") == "on <wifi> now"
    assert logsafe.scrub("Lab-2G-Network") == "Lab-2G-Network"


@pytest.mark.parametrize(
    "value", ["Home", "Wi-Fi", "1402", "lab", "Password", "Firmware", "STARLINK", "Wirelessabc"]
)
def test_what_could_be_an_ordinary_word_is_not_hunted_as_written(value):
    """An SSID of `Home` would rewrite "Home Assistant", a password `Password`
    every "password", one of `1402` every such number -- and each rewrite would
    give the value away."""
    logsafe.remember(value, "<wifi>")
    text = (
        "Home Assistant took 1402 ms on Wi-Fi at the lab: wrong password, "
        f"firmware on starlink, wirelessabc, {value}"
    )
    assert logsafe.scrub(text) == text


@pytest.mark.parametrize(
    ("value", "hunted"),
    [("Lab-2Gx", False), ("Lab-2G-N", True), ("Wirelessabcd", True), ("Wirelessabc", False)],
)
def test_where_hunting_as_written_begins(value, hunted):
    """Eight characters with a non-letter in them, or twelve letters."""
    logsafe.remember(value, "<wifi>")
    assert (logsafe.scrub(f"on {value} now") == "on <wifi> now") is hunted


def test_a_number_s_hex_is_not_hunted_inside_other_numbers():
    """`1402` spelled in hex is `31343032`, which is also part of a timestamp."""
    logsafe.remember("1402", "<wifi>")
    text = "PT_data for charger abc123 is stale (1727431343032)"
    assert logsafe.scrub(text) == text


def test_hex_is_hunted_from_three_bytes():
    logsafe.remember("yz", "<x>")
    logsafe.remember("xyz", "<y>")
    assert logsafe.scrub("ff797aff") == "ff797aff"
    assert logsafe.scrub("ff78797aff") == "ff<y>ff"


def test_a_short_value_is_still_caught_as_bytes():
    """Inside a frame it is a run of hex digits, not a word."""
    logsafe.remember("Home", "<wifi>")
    assert logsafe.scrub("aa08" + b"Home".hex()) == "aa08<wifi>"


def test_nothing_known_changes_nothing():
    assert logsafe.scrub(f"{UNIT} {EMAIL}") == f"{UNIT} {EMAIL}"


def test_an_answer_is_described_not_quoted():
    """A login answer is the tokens; the code and message explain a refusal."""
    answer = {"refreshToken": "eyJsecret", "userId": 42, "code": 460, "msg": "token invalid"}
    described = logsafe.describe(answer)
    assert "eyJsecret" not in described and "42" not in described
    assert described == (
        "an object with keys ['code', 'msg', 'refreshToken', 'userId'], "
        "code 460, msg 'token invalid'"
    )
    assert logsafe.describe(None) == "nothing"
    assert logsafe.describe([1, 2]) == "a list of 2"


@pytest.fixture
def installed():
    """The record factory as the integration leaves it, put back afterwards."""
    make = logging.getLogRecordFactory()
    was = logsafe._scope
    logsafe._scope = None
    logsafe.install(SCOPE)
    yield
    logging.setLogRecordFactory(make)
    logsafe._scope = was


def _record(name, msg, args=None, exc_info=None, sinfo=None):
    return logging.getLogger(name).makeRecord(
        name, logging.ERROR, __file__, 1, msg, args, exc_info, sinfo=sinfo
    )


def test_this_integration_s_records_are_cleaned(installed, caplog):
    logsafe.remember(EMAIL, "<account>")
    tag = logsafe.remember_charger(IOT, UNIT)
    caplog.set_level(logging.DEBUG, logger=SCOPE)

    logging.getLogger(f"{SCOPE}.api").warning("login as %s", EMAIL)
    try:
        raise RuntimeError(f"cloud said no to {UNIT}")
    except RuntimeError:
        logging.getLogger(f"{SCOPE}.coordinator").exception("poll failed")

    assert EMAIL not in caplog.text and "<account>" in caplog.text
    assert UNIT not in caplog.text, "the traceback carried it"
    assert tag in caplog.text


def test_everybody_else_s_records_are_left_alone(installed):
    """Their records are not this integration's to rewrite."""
    logsafe.remember(EMAIL, "<account>")
    record = _record("homeassistant.core", "hello %s", (EMAIL,))
    assert record.getMessage() == f"hello {EMAIL}"
    assert _record(f"{SCOPE}x.sub", "hello %s", (EMAIL,)).getMessage() == f"hello {EMAIL}"


def test_a_traceback_that_had_to_be_cleaned_loses_its_exception(installed):
    """A handler formatting the exception afresh would put the words back."""
    logsafe.remember_charger(IOT, UNIT)
    try:
        raise RuntimeError(f"no to {UNIT}")
    except RuntimeError:
        record = _record(SCOPE, "failed", exc_info=sys.exc_info())
    assert record.exc_info is None
    assert UNIT not in record.exc_text


def test_a_clean_traceback_keeps_its_exception(installed):
    """Home Assistant's log viewer shows it; nothing needed taking out."""
    logsafe.remember_charger(IOT, UNIT)
    try:
        raise RuntimeError("nothing private")
    except RuntimeError:
        record = _record(SCOPE, "failed", exc_info=sys.exc_info())
    assert record.exc_info is not None


def test_a_stack_is_cleaned(installed):
    logsafe.remember_charger(IOT, UNIT)
    record = _record(SCOPE, "here", sinfo=f"Stack (most recent call last):\n  at {UNIT}")
    assert UNIT not in record.stack_info


def test_a_line_that_cannot_be_checked_is_withheld_whole(installed):
    """Not raised into whatever was logging, and not let through unchecked --
    its traceback included."""
    logsafe.remember_charger(IOT, UNIT)
    try:
        raise RuntimeError(f"no to {UNIT}")
    except RuntimeError:
        record = _record(SCOPE, f"{UNIT} %s %s", (1,), exc_info=sys.exc_info())
    assert record.getMessage() == logsafe.WITHHELD
    assert record.exc_info is None and record.exc_text is None


def test_a_record_made_while_compiling_does_not_hang(installed, monkeypatch):
    """A finalizer can log while the pattern is being built, on the same thread.

    It comes back through here; with a lock that is not reentrant, held while
    compiling, that is the event loop stopped for good.
    """
    logsafe.remember_charger(IOT, UNIT)
    compile_ = logsafe.re.compile
    logged = []

    def _compile(*args, **kwargs):
        if not logged:
            logged.append(True)
            logging.getLogger(SCOPE).warning("from a finalizer about %s", UNIT)
        return compile_(*args, **kwargs)

    monkeypatch.setattr(logsafe.re, "compile", _compile)
    done = threading.Event()

    def _run():
        logsafe.scrub(f"poll of {UNIT}")
        done.set()

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    assert done.wait(5), "scrub never came back"


def test_a_record_made_while_remembering_does_not_hang(installed):
    """The garbage collector can run while the list is being written, under
    the lock, and a finalizer can log -- on the same thread, back through
    here, and into the lock again."""
    logsafe.remember_charger(IOT, UNIT)

    class _Noisy(dict):
        def __setitem__(self, key, value):
            logging.getLogger(SCOPE).warning("from a finalizer about %s", UNIT)
            super().__setitem__(key, value)

    noisy = _Noisy(logsafe._known)
    was, lock = logsafe._known, logsafe._lock
    logsafe._known = noisy
    # A lock of the module's own kind, but this test's: one left held by a
    # thread stuck on it must not hang every test after this one.
    logsafe._lock = type(lock)()
    done = threading.Event()

    def _run():
        logsafe.remember("another-value-9", "<x>")
        done.set()

    try:
        threading.Thread(target=_run, daemon=True).start()
        assert done.wait(5), "remember never came back"
    finally:
        logsafe._known, logsafe._lock = was, lock
        logsafe._known.update(noisy)


def test_a_record_built_without_a_name_is_left_alone(installed):
    """`logging.makeLogRecord` builds one with no name and fills it in after."""
    logsafe.remember_charger(IOT, UNIT)
    record = logging.makeLogRecord({"name": SCOPE, "msg": f"about {UNIT}"})
    assert record.name == SCOPE


def test_it_goes_in_once():
    make = logging.getLogRecordFactory()
    was = logsafe._scope
    logsafe._scope = None
    try:
        logsafe.install(SCOPE)
        once = logging.getLogRecordFactory()
        logsafe.install(SCOPE)
        assert logging.getLogRecordFactory() is once
    finally:
        logging.setLogRecordFactory(make)
        logsafe._scope = was



def test_a_name_whose_hex_is_digits_is_hunted_from_five_bytes():
    """`Guest` spells 4775657374 -- all digits, but ten of them."""
    logsafe.remember("Guest", "<wifi>")
    assert logsafe.scrub("aa08" + b"Guest".hex()) == "aa08<wifi>"


def test_bytes_follow_the_same_rules_as_text():
    logsafe.remember_bytes(b"1402", "<wifi>")
    logsafe.remember_bytes(b"AP", "<wifi>")
    for text in ("stale (1727431343032)", "xx4150yy"):
        assert logsafe.scrub(text) == text
    logsafe.remember_bytes(b"Guest", "<wifi>")
    assert logsafe.scrub(b"Guest".hex()) == "<wifi>"


def test_a_mac_that_is_not_one_hunts_nothing():
    """A cloud that says `N/A` for the MAC would otherwise take every `a`."""
    logsafe.remember_charger(IOT, UNIT, "N/A")
    assert logsafe.scrub("a banana, a cable") == "a banana, a cable"


def test_the_same_value_again_does_not_rebuild_the_pattern():
    """Every poll remembers every charger; the pattern is built once."""
    logsafe.remember_charger(IOT, UNIT, MAC)
    before = logsafe._version
    logsafe.remember_charger(IOT, UNIT, MAC)
    assert logsafe._version == before


def test_the_bytes_of_an_identifier_are_found_in_a_body():
    logsafe.remember_charger(IOT, UNIT, MAC)
    body = bytes.fromhex("0004" + "ec1ac3000001" + "ff")
    assert logsafe.private_bytes(body) == {2, 3, 4, 5, 6, 7}
    assert logsafe.private_bytes(bytes.fromhex("0004ff")) == set()


def test_a_clean_traceback_keeps_its_formatted_text(installed):
    """Formatted once for checking, so no handler formats it again."""
    logsafe.remember_charger(IOT, UNIT)
    try:
        raise RuntimeError("nothing private")
    except RuntimeError:
        record = _record(SCOPE, "failed", exc_info=sys.exc_info())
    assert record.exc_info is not None and record.exc_text
