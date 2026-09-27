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


def test_every_card_defines_its_element_only_if_nothing_else_has() -> None:
    """A module loaded twice must not throw on the second pass.

    `customElements.define` for a name already taken is an exception, and the
    page shows it. A card can be loaded twice -- a resource list holding an
    older url for the same file, a dashboard adding it by hand -- so every
    definition is guarded and a second load is a no-op.

    The cards that share `ugreen-ui.js` do it through `defineCard`, which holds
    the guard once; the wallpaper card, which predates the module, holds its
    own.
    """
    www = _COMPONENT / "www"
    cards = sorted(path for path in www.glob("*-card.js"))
    assert len(cards) >= 2, "no cards found -- this test is reading the wrong path"

    shared = (www / "ugreen-ui.js").read_text(encoding="utf-8")
    assert "if (customElements.get(tag)) return;" in shared, (
        "defineCard no longer refuses a name that is taken"
    )

    for card in cards:
        source = card.read_text(encoding="utf-8")
        direct = source.count("customElements.define(")
        if direct == 0:
            assert "defineCard(" in source, f"{card.name} never defines its element"
            continue
        assert direct == 1, f"{card.name} defines its element more than once"
        guard = source.find("customElements.get(")
        assert 0 <= guard < source.index("customElements.define("), (
            f"{card.name} defines its element without checking the name first"
        )
