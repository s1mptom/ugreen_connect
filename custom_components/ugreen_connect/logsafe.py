"""Keep the household out of the log.

The README asks anyone mapping their charger to post their diagnostics, and
sometimes their log, so both have to be safe to post. The lines this
integration writes are written with that in mind; this is the net under them,
for the text nobody here writes -- an error message the cloud sent back, an
exception's own words, the lines Home Assistant itself writes about this
integration -- and for a line added later by somebody who forgot.

Every identifier of the household the integration comes to know is kept here:
the account's e-mail, and each charger's unit code, cloud id, MAC and Wi-Fi
network name, with the hex spelling they have inside a frame. Every log record
made in the process -- Home Assistant's own included, since the traceback of an
error raised here is written by Home Assistant's loggers -- has them replaced as
it is created. A charger's identifiers become its tag, the same six characters
the state record uses, so a line still says which charger it is about.

No Home Assistant imports, so the standalone tests can load it.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from typing import Any, Final

# A value shorter than this, or a short one of letters only, is too likely to
# be an ordinary word: an SSID of "Home" would rewrite "Home Assistant" in every
# line that has it, and the rewrite would give the SSID away.
MIN_LENGTH: Final = 4
MIN_WORD_LENGTH: Final = 8

# Put in place of a line that could not be checked. Losing a line is the
# lesser failure: a line let through unchecked is the one this module exists
# to prevent, and an exception raised from here would break whatever was
# logging.
WITHHELD: Final = "[log line withheld: it could not be checked for private data]"

_lock = threading.Lock()
# spelling -> (stand-in, whether it must stand on its own rather than inside a
# longer run of letters and digits)
_known: dict[str, tuple[str, bool]] = {}
_compiled: tuple[re.Pattern[str], dict[str, str]] | None = None
_installed = False


def charger_tag(iot_id: str) -> str:
    """A short name for a charger in a log meant to be posted in public.

    Two chargers on one account still have to be told apart, so each gets six
    characters of a hash of its cloud id: stable, and saying nothing.
    """
    return hashlib.sha256(iot_id.encode()).hexdigest()[:6]


def _distinctive(value: str) -> bool:
    if len(value) < MIN_LENGTH:
        return False
    return len(value) >= MIN_WORD_LENGTH or not value.isalpha()


def remember(value: Any, stand_in: str) -> None:
    """Replace `value` with `stand_in` wherever it is logged.

    Matched as written, and as a whole: not inside a longer run of letters and
    digits. Its hex spelling, which is how it appears inside a frame, is
    matched in either case and anywhere, since a frame is one long run.
    """
    global _compiled
    if not isinstance(value, str) or not _distinctive(value):
        return
    spelled = value.encode().hex()
    spellings = {
        value: (stand_in, True),
        spelled: (stand_in, False),
        spelled.upper(): (stand_in, False),
    }
    with _lock:
        if all(_known.get(key) == entry for key, entry in spellings.items()):
            return
        _known.update(spellings)
        _compiled = None


def remember_charger(
    iot_id: str | None,
    unit: str | None = None,
    mac: str | None = None,
) -> str | None:
    """Everything that names one charger. Returns how the log names it."""
    global _compiled
    name = iot_id or unit
    tag = f"charger {charger_tag(name)}" if name else "a charger"
    remember(iot_id, tag)
    remember(unit, tag)
    if isinstance(mac, str) and mac:
        bare = re.sub(r"[^0-9A-Fa-f]", "", mac)
        for spelling in {mac, mac.upper(), mac.lower()}:
            remember(spelling, "<mac>")
        # Bare, the MAC is also how it sits inside a frame as raw bytes.
        with _lock:
            for spelling in {bare.upper(), bare.lower()}:
                if len(spelling) >= MIN_LENGTH:
                    _known[spelling] = ("<mac>", False)
            _compiled = None
    return tag if name else None


def _pattern() -> tuple[re.Pattern[str], dict[str, str]] | None:
    global _compiled
    compiled = _compiled
    if compiled is not None:
        return compiled
    with _lock:
        if not _known:
            return None
        # Longest first, so a value inside a longer one does not split it.
        spellings = sorted(_known, key=len, reverse=True)
        parts = [
            rf"(?<![0-9A-Za-z]){re.escape(s)}(?![0-9A-Za-z])" if _known[s][1] else re.escape(s)
            for s in spellings
        ]
        compiled = (re.compile("|".join(parts)), {s: _known[s][0] for s in spellings})
        _compiled = compiled
        return compiled


def scrub(text: str) -> str:
    """The text with every remembered identifier replaced."""
    if not text:
        return text
    compiled = _pattern()
    if compiled is None:
        return text
    pattern, lookup = compiled
    return pattern.sub(lambda match: lookup.get(match.group(0), "<private>"), text)


def describe(value: Any) -> str:
    """What a cloud answer looked like, without anything that was in it.

    For an error message about an answer that was not what was expected. The
    answer itself is no business of a log: a login's carries the tokens, an
    upload slot's a signed URL.
    """
    if isinstance(value, dict):
        return f"an object with keys {sorted(map(str, value))}"
    if isinstance(value, list):
        return f"a list of {len(value)}"
    if value is None:
        return "nothing"
    return f"a {type(value).__name__}"


def _scrub_record(record: logging.LogRecord) -> None:
    try:
        message = record.getMessage()
        clean = scrub(message)
        if clean != message:
            record.msg, record.args = clean, None
        if record.exc_info:
            text = record.exc_text or logging.Formatter().formatException(record.exc_info)
            clean = scrub(text)
            if clean != text:
                # The cleaned text goes out, and the exception itself does not:
                # a handler that formats it afresh -- Home Assistant's own log
                # viewer does -- would put the original words back.
                record.exc_text, record.exc_info = clean, None
        if record.stack_info:
            record.stack_info = scrub(record.stack_info)
    except Exception:  # noqa: BLE001 - nothing here may break the caller
        record.msg, record.args = WITHHELD, None
        record.exc_info = record.exc_text = record.stack_info = None


def install() -> None:
    """Scrub every log record the process makes from now on. Once per process.

    Wraps the record factory rather than putting a filter on loggers: a
    logger's filters see only records made on that logger, so a traceback
    Home Assistant writes about an error raised here would get past them, and
    so would the logger of any module added later.
    """
    global _installed
    with _lock:
        if _installed:
            return
        _installed = True
        make = logging.getLogRecordFactory()

    def _factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = make(*args, **kwargs)
        if _known:
            _scrub_record(record)
        return record

    logging.setLogRecordFactory(_factory)
