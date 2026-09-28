"""Keep the household out of what this integration logs.

The README asks anyone mapping their charger to post their diagnostics, and
sometimes their log, so both have to be safe to post. The lines here are
written with that in mind; this is the net under them, for the text nobody
here writes -- an error message the cloud sent back, an exception's own words
-- and for a line added later by somebody who forgot.

Every identifier of the household the integration comes to know is kept here:
the account's e-mail, password and user id, and each charger's unit code,
cloud id, MAC and Wi-Fi network name, each also in the hex spelling it has
inside a frame. Not the tokens: a new one arrives every twenty minutes or so,
and kept they would make this list, and the pattern built from it, grow for as
long as Home Assistant runs. Nothing here writes a token anywhere, and no
error quotes a cloud answer (see `describe`). Two places use the list:

- every record made on this integration's loggers is cleaned as it is made,
  and only those: a hook on every record in Home Assistant would be this
  integration's code running under everybody else's logging;
- the errors this integration raises clean their own message as they are
  made (see `UgreenError`), so a traceback Home Assistant writes about one is
  clean too, and its log viewer can keep the exception.

A charger's identifiers become its tag, the same six characters the state
record uses, so a line still says which charger it is about.

No Home Assistant imports, so the standalone tests can load it.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from typing import Any, Final

# When a value is hunted as written. One that could be an ordinary word or
# number is not: an SSID of "Home" would rewrite "Home Assistant", a password
# of "Password" every "password", one of "1402" every such number -- and each
# rewrite would give the value away. So eight characters with something other
# than a letter in them, or twelve letters. The identifiers that matter all
# qualify: a unit code, a cloud id, a MAC, an e-mail address.
MIN_PLAIN: Final = 8
MIN_PLAIN_WORD: Final = 12
# Its hex spelling is hunted from three bytes up, inside a frame's run of hex
# digits -- unless that spelling is all decimal digits, as it is for a value
# made of digits, when it would be found inside ordinary numbers.
MIN_BYTES: Final = 3

# Put in place of a line of this integration's that could not be checked.
# Losing the line is the lesser failure: let through unchecked, it is the one
# this module exists to prevent, and an exception from here would break
# whatever was logging.
WITHHELD: Final = "[log line withheld: it could not be checked for private data]"

# Reentrant: taking it can run the garbage collector, a finalizer can log, and
# that record comes back through here on the same thread.
_lock = threading.RLock()
# lower-case spelling -> (stand-in, whether it must stand on its own rather
# than inside a longer run of letters and digits)
_known: dict[str, tuple[str, bool]] = {}
_version = 0
_compiled: tuple[int, re.Pattern[str], list[str]] | None = None
_scope: str | None = None


def charger_tag(iot_id: str) -> str:
    """A short name for a charger in a log meant to be posted in public.

    Two chargers on one account still have to be told apart, so each gets six
    characters of a hash of its cloud id: stable, and saying nothing.
    """
    return hashlib.sha256(iot_id.encode()).hexdigest()[:6]


def _add(spelling: str, stand_in: str, whole: bool) -> None:
    global _version
    key = spelling.lower()
    if _known.get(key) != (stand_in, whole):
        _known[key] = (stand_in, whole)
        _version += 1


def remember(value: Any, stand_in: str) -> None:
    """Replace `value` with `stand_in` wherever this integration logs it.

    In any case, since an e-mail or a unit code may come back from the cloud
    in another; as a whole, not inside a longer word; and its hex spelling
    anywhere, which is how it sits inside a frame.
    """
    if not isinstance(value, str) or not value:
        return
    raw = value.encode()
    with _lock:
        if len(value) >= (MIN_PLAIN_WORD if value.isalpha() else MIN_PLAIN):
            _add(value, stand_in, True)
        if len(raw) >= MIN_BYTES and not raw.hex().isdigit():
            _add(raw.hex(), stand_in, False)


def remember_bytes(raw: bytes | None, stand_in: str) -> None:
    """Replace these bytes' hex spelling, for a value only known as bytes.

    The Wi-Fi name arrives as raw bytes, and decoded it is not always the
    same bytes again: a name that is not UTF-8 comes back with replacement
    characters, whose hex matches nothing in any frame.
    """
    if isinstance(raw, bytes) and len(raw) >= MIN_BYTES and not raw.hex().isdigit():
        with _lock:
            _add(raw.hex(), stand_in, False)


def remember_charger(
    iot_id: str | None,
    unit: str | None = None,
    mac: str | None = None,
) -> str | None:
    """Everything that names one charger. Returns how the log names it."""
    name = iot_id or unit
    tag = f"charger {charger_tag(name)}" if name else "a charger"
    remember(iot_id, tag)
    remember(unit, tag)
    if isinstance(mac, str) and mac:
        remember(mac, "<mac>")
        # Bare, the MAC is how it sits inside a frame as raw bytes.
        bare = re.sub(r"[^0-9A-Fa-f]", "", mac)
        if len(bare) >= 2 * MIN_BYTES:
            with _lock:
                _add(bare, "<mac>", False)
    return tag if name else None


def _pattern() -> tuple[re.Pattern[str], list[str]] | None:
    """The compiled pattern for what is known now, compiled outside the lock.

    Holding the lock only to copy and to publish means nothing that logs can
    run while it is held on this thread's behalf -- and if something does, the
    lock is reentrant anyway.
    """
    global _compiled
    compiled = _compiled
    if compiled is not None and compiled[0] == _version:
        return compiled[1], compiled[2]
    with _lock:
        version, known = _version, dict(_known)
    if not known:
        return None
    # Longest first, so a value inside a longer one does not split it. Named
    # groups say which one matched, whatever case it matched in.
    spellings = sorted(known, key=len, reverse=True)
    parts = [
        rf"(?P<g{i}>(?<![0-9A-Za-z]){re.escape(s)}(?![0-9A-Za-z]))"
        if known[s][1]
        else rf"(?P<g{i}>{re.escape(s)})"
        for i, s in enumerate(spellings)
    ]
    pattern = re.compile("|".join(parts), re.IGNORECASE)
    stand_ins = [known[s][0] for s in spellings]
    with _lock:
        if _version == version:
            _compiled = (version, pattern, stand_ins)
    return pattern, stand_ins


def scrub(text: str) -> str:
    """The text with every remembered identifier replaced."""
    if not text or not _known:
        return text
    compiled = _pattern()
    if compiled is None:
        return text
    pattern, stand_ins = compiled
    # Every alternative is a named group, so one of them is always the match.
    return pattern.sub(lambda match: stand_ins[int(str(match.lastgroup)[1:])], text)


def describe(value: Any) -> str:
    """What a cloud answer looked like, without anything that was in it.

    For an error message about an answer that was not what was expected. The
    answer itself is no business of a log -- a login's carries the tokens, an
    upload slot's a signed URL -- but the code and message the cloud put in it
    are what explain the refusal, and they are kept, cleaned.
    """
    if isinstance(value, dict):
        said = [
            f"{key} {value[key]!r}"
            for key in ("code", "msg", "message")
            if isinstance(value.get(key), str | int)
        ]
        keys = f"an object with keys {sorted(map(str, value))}"
        # Not cleaned here: it only ever goes into an error, which cleans its
        # own message, or a record of this integration's, which is cleaned.
        return ", ".join([keys, *said])
    if isinstance(value, list):
        return f"a list of {len(value)}"
    if value is None:
        return "nothing"
    return f"a {type(value).__name__}"


def _clean(record: logging.LogRecord) -> None:
    try:
        message = record.getMessage()
        clean = scrub(message)
        if clean != message:
            record.msg, record.args = clean, None
        if record.exc_info:
            text = record.exc_text or logging.Formatter().formatException(record.exc_info)
            clean = scrub(text)
            # Kept either way, so a handler does not format it a second time.
            record.exc_text = clean
            if clean != text:
                # The exception itself does not go out: a handler that formats
                # it afresh -- Home Assistant's log viewer does -- would put the
                # original words back.
                record.exc_info = None
        if record.stack_info:
            record.stack_info = scrub(record.stack_info)
    except Exception:  # noqa: BLE001 - nothing here may break the caller
        record.msg, record.args = WITHHELD, None
        record.exc_info = record.exc_text = record.stack_info = None


def install(scope: str) -> None:
    """Clean every record made on `scope`'s loggers from now on.

    Wraps the record factory rather than putting a filter on each logger: a
    logger's filters see only the records made on that logger, so the logger
    of a module added later would get past them. Only this integration's own
    records are touched; everybody else's cost one string comparison.
    """
    global _scope
    with _lock:
        if _scope is not None:
            return
        _scope = scope
        make = logging.getLogRecordFactory()
    prefix = f"{scope}."

    def _factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = make(*args, **kwargs)
        # `makeLogRecord` builds a record with no name and fills it in after.
        name = record.name
        if _known and isinstance(name, str) and (name == scope or name.startswith(prefix)):
            _clean(record)
        return record

    logging.setLogRecordFactory(_factory)
