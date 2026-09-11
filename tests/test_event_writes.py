"""A raised event that is never written never happened.

`EventEntity._trigger_event` records the type, the time and the attributes and
returns; Home Assistant's own docs pair it with `async_write_ha_state()`. Two
raised inside one callback therefore leave only the second unless each is
written as it is raised -- and this integration raises two in one callback on
purpose, when a device is swapped inside the unplug debounce and one bout ends
as another begins. The automation waiting for the first to finish is exactly
the one that would never hear.

Read from the source, so it needs no Home Assistant install.
"""

import ast
from pathlib import Path

_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "ugreen_connect"
    / "event.py"
)
_TREE = ast.parse(_SOURCE.read_text(), str(_SOURCE))


def _is_call(node: ast.stmt, name: str) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == name
    )


def _statement_lists(node: ast.AST):
    """Every block of statements hanging off a node, not just `body`.

    `orelse` is the one that matters: the triggers this guards live in the
    `else:` of an `if`, so a check that only walked `body` would pass while
    looking straight past them.
    """
    for field in ("body", "orelse", "finalbody"):
        block = getattr(node, field, None)
        if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
            yield block
    for handler in getattr(node, "handlers", []) or []:
        yield handler.body


def test_every_raised_event_is_written_before_the_next_one():
    """Each `_trigger_event` is followed immediately by a state write."""
    unwritten: list[int] = []
    for node in ast.walk(_TREE):
      for body in _statement_lists(node):
        for statement, following in zip(body, [*body[1:], None], strict=True):
            if not _is_call(statement, "_trigger_event"):
                continue
            if following is None or not _is_call(following, "async_write_ha_state"):
                unwritten.append(statement.lineno)
    assert not unwritten, (
        "_trigger_event without async_write_ha_state after it, at lines: "
        f"{unwritten}. Two events in one callback would collapse into one."
    )


# There was a second guard here asserting that some callback still raises two
# events, on the grounds that otherwise the one above guards nothing. It was
# removed: extracting the trigger-and-write pair into a helper is the obvious
# cleanup this fix invites, and that guard went red on it while the guard above
# correctly stayed green -- a test that fails on a correct refactor and points
# at the wrong problem is worse than no test.
