"""A refusal has to name the mode the way the select in front of it does.

The message for `mode_not_selectable` spells the mode out instead of taking it
as a placeholder, because Home Assistant fills a placeholder in with the raw
key and drops any language whose placeholders differ from English's. Spelled
out, each language carries a second copy of its select's name for the mode --
and a copy drifts: the Russian select said «Свой» while its own refusal said
«Пользовательский».

Nothing catches that at runtime. Home Assistant shows the select's name and the
message from the same file without ever comparing them. So the pair is checked
here, on the files themselves, which needs no Home Assistant install.

The limit sensors' names are not compared. They name the mode inside a phrase
of their own -- in Russian, in the genitive, «пользовательского режима» -- so
no rule a test could state holds in every language without an exception for
the one it would exist to catch.
"""

import json
from pathlib import Path

_COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "ugreen_connect"
_FILES = [_COMPONENT / "strings.json", *sorted((_COMPONENT / "translations").glob("*.json"))]


def _loaded() -> list[tuple[str, dict]]:
    # UTF-8 said outright: the default is the locale's encoding, which on
    # Windows is not UTF-8 and cannot decode the Russian file at all.
    files = [(path.name, json.loads(path.read_text(encoding="utf-8"))) for path in _FILES]
    assert len(files) >= 2, "translations are gone -- this test is reading the wrong path"
    return files


def test_every_language_names_the_mode_the_way_its_select_does() -> None:
    for name, strings in _loaded():
        shown = strings["entity"]["select"]["charging_mode"]["state"]["custom"]
        message = strings["exceptions"]["mode_not_selectable"]["message"]
        assert shown in message, f"{name}: the refusal does not name the mode {shown!r}"


def test_the_message_takes_no_placeholder() -> None:
    """Named rather than filled in -- the reason each language repeats the name.

    A placeholder here would be filled with `custom`, the key, in every
    language, and `_validate_placeholders` would drop any language that dropped
    it on its own.
    """
    for name, strings in _loaded():
        message = strings["exceptions"]["mode_not_selectable"]["message"]
        assert "{" not in message, f"{name}: the refusal took a placeholder again"


def test_the_card_defines_its_element_only_if_nothing_else_has() -> None:
    """A module loaded twice must not throw on the second pass.

    `customElements.define` for a name already taken is an exception, and the
    page shows it. The card can be loaded twice -- a resource list holding an
    older url for this same file, a dashboard adding it by hand -- so the
    definition is guarded and the rest of the module is a no-op the second
    time.
    """
    card = (_COMPONENT / "www" / "ugreen-wallpaper-card.js").read_text(encoding="utf-8")
    define = "customElements.define('ugreen-wallpaper-card'"
    assert card.count(define) == 1, "the card defines its element more than once"
    guard = card.index("if (!customElements.get('ugreen-wallpaper-card'))")
    assert guard < card.index(define), "the definition is not behind the guard"
    assert card.index("window.customCards.push") > guard, (
        "a second load would still push the card into the picker again"
    )
