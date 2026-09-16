"""A refusal has to name the mode the way the select in front of it does.

The message for `mode_not_selectable` spells the mode out instead of taking it
as a placeholder, because Home Assistant fills a placeholder in with the raw
key and drops any language whose placeholders differ from English's. Spelled
out, each language carries its own copy of the name -- and a copy drifts: the
Russian select said «Свой», its six limit sensors said «настраиваемого режима»
and its refusal said «Пользовательский», three names for one mode in one file.

Nothing catches that at runtime. Home Assistant shows the select's name and the
message from the same file without ever comparing them, and a reader of one
language cannot see the other. So it is checked here, on the files themselves,
which needs no Home Assistant install.
"""

import json
from pathlib import Path

_COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "ugreen_connect"
_FILES = [_COMPONENT / "strings.json", *sorted((_COMPONENT / "translations").glob("*.json"))]


def _loaded() -> list[tuple[str, dict]]:
    files = [(path.name, json.loads(path.read_text())) for path in _FILES]
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
