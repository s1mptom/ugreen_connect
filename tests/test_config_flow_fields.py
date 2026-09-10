"""Every field the setup form asks for has to end up somewhere.

The form once collected battery voltage, charging efficiency and session idle
time and then dropped all three: `async_create_entry` carried `data` but no
`options`, so someone who set 77% during setup silently got the default and
only found out by opening the options dialog (s1mptom/ugreen_connect#4).

Nothing errored and nothing was logged, which is exactly why it wants a test.
This one reads the source rather than running the flow, so it needs no Home
Assistant install -- the question is only whether a key asked for is a key used.
"""

import ast
from pathlib import Path

_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "ugreen_connect"
    / "config_flow.py"
)
_TREE = ast.parse(_SOURCE.read_text(), str(_SOURCE))


def _schema_keys(name: str) -> set[str]:
    """The constants a `vol.Schema({...})` assignment asks the user for."""
    for node in _TREE.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return {
                key.args[0].id
                for key in ast.walk(node)
                if isinstance(key, ast.Call)
                and isinstance(key.func, ast.Attribute)
                and key.func.attr in {"Required", "Optional"}
                and key.args
                and isinstance(key.args[0], ast.Name)
            }
    raise AssertionError(f"{name} is gone -- update this test with the schema")


def _consumed(function: str) -> set[str]:
    """Every `user_input[CONF_...]` a step actually reads."""
    for node in ast.walk(_TREE):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function:
            return {
                sub.slice.id
                for sub in ast.walk(node)
                if isinstance(sub, ast.Subscript)
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "user_input"
                and isinstance(sub.slice, ast.Name)
            }
    raise AssertionError(f"{function} is gone -- update this test with the flow")


def test_setup_form_uses_everything_it_asks_for() -> None:
    dropped = _schema_keys("STEP_USER_SCHEMA") - _consumed("async_step_user")
    assert not dropped, f"asked for during setup and then discarded: {sorted(dropped)}"


def test_options_form_uses_everything_it_asks_for() -> None:
    dropped = _schema_keys("OPTIONS_SCHEMA") - _consumed("async_step_init")
    assert not dropped, f"asked for in the options dialog and then discarded: {sorted(dropped)}"
