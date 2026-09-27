"""Keep the household out of the log.

The README asks anyone mapping their charger to post what this integration
logs, so every line it writes has to be safe to post. The lines are written
with that in mind; this is the net under them, for the text nobody here
writes -- an error message the cloud sent back, an exception's own words --
and for a line added later by somebody who forgot.

Every identifier of the household the integration comes to know is kept here:
the account's e-mail, and each charger's unit code, cloud id, MAC and Wi-Fi
network name, with the hex spelling they have inside a frame. A filter on the
integration's loggers replaces any of them in a record before a handler sees
it. A charger's identifiers become its tag, the same six characters the state
record uses, so a line still says which charger it is about.

No Home Assistant imports, so the standalone tests can load it.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Final

# Shorter than this and a value is too likely to be part of something else --
# an SSID of "home" would take the word out of every line that has it.
MIN_LENGTH: Final = 4

_known: dict[str, str] = {}
_pattern: re.Pattern[str] | None = None
_lookup: dict[str, str] = {}


def charger_tag(iot_id: str) -> str:
    """A short name for a charger in a log meant to be posted in public.

    Two chargers on one account still have to be told apart, so each gets six
    characters of a hash of its cloud id: stable, and saying nothing.
    """
    return hashlib.sha256(iot_id.encode()).hexdigest()[:6]


def remember(value: str | None, stand_in: str) -> None:
    """Replace `value` with `stand_in` wherever the integration logs it."""
    global _pattern
    if not isinstance(value, str) or len(value) < MIN_LENGTH:
        return
    spellings = {value, value.encode().hex()}
    if all(_known.get(spelling) == stand_in for spelling in spellings):
        return
    for spelling in spellings:
        _known[spelling] = stand_in
    _pattern = None


def remember_charger(
    iot_id: str | None,
    unit: str | None = None,
    mac: str | None = None,
) -> str | None:
    """Everything that names one charger, as that charger's tag."""
    if not iot_id:
        return None
    tag = f"charger {charger_tag(iot_id)}"
    remember(iot_id, tag)
    remember(unit, tag)
    if mac:
        remember(mac, "<mac>")
        remember(re.sub(r"[^0-9A-Fa-f]", "", mac), "<mac>")
    return tag


def scrub(text: str) -> str:
    """The text with every remembered identifier replaced."""
    global _pattern, _lookup
    if not _known or not text:
        return text
    if _pattern is None:
        # Longest first, so a value inside a longer one does not split it.
        spellings = sorted(_known, key=len, reverse=True)
        _pattern = re.compile("|".join(re.escape(s) for s in spellings), re.IGNORECASE)
        _lookup = {spelling.lower(): stand_in for spelling, stand_in in _known.items()}
    return _pattern.sub(lambda match: _lookup[match.group(0).lower()], text)


class _Scrub(logging.Filter):
    """Rewrite a record's message, and its traceback, before it is handled."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not _known:
            return True
        message = record.getMessage()
        clean = scrub(message)
        if clean != message:
            record.msg, record.args = clean, None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = scrub(record.exc_text)
        return True


FILTER: Final = _Scrub()


def install(package: str) -> None:
    """Put the filter on every logger of the package that exists by now.

    A logger's filters see only the records made on that logger, not ones
    passed up from its children, so each module's logger needs its own. Called
    again once the platforms are loaded, for their loggers; putting it on
    twice is a no-op.
    """
    names = [
        name
        for name in logging.Logger.manager.loggerDict
        if name == package or name.startswith(f"{package}.")
    ]
    for name in [package, *names]:
        logger = logging.getLogger(name)
        if FILTER not in logger.filters:
            logger.addFilter(FILTER)
