"""The shape of a write, checked without a Home Assistant anywhere.

Four properties that are invisible at a glance and expensive to lose. Each of
them has been wrong at least once in this integration, and none of them failed
loudly when it was: the charger accepted everything, the log stayed quiet, and
the screen showed a value nobody had set.

Like test_config_flow_fields, this reads the source rather than running it --
the questions here are about where a statement sits relative to another one,
which is exactly what an AST can answer and a mock cannot.
"""

import ast
from pathlib import Path

_COMPONENT = (
    Path(__file__).resolve().parents[1] / "custom_components" / "ugreen_connect"
)
_PLATFORMS = ("number.py", "select.py", "switch.py")


def _tree(file_name: str) -> ast.Module:
    path = _COMPONENT / file_name
    return ast.parse(path.read_text(), str(path))


def _functions(file_name: str) -> dict[str, ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in ast.walk(_tree(file_name))
        if isinstance(node, ast.AsyncFunctionDef)
    }


def _calls(node: ast.AST, attr: str) -> list[ast.Call]:
    """Every ``something.attr(...)`` below this node."""
    return [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == attr
    ]


def _holds(node: ast.AST, name: str) -> list[ast.AsyncWith]:
    """Every ``async with self.<name>:`` below this node."""
    return [
        block
        for block in ast.walk(node)
        if isinstance(block, ast.AsyncWith)
        and any(
            isinstance(item.context_expr, ast.Attribute)
            and item.context_expr.attr == name
            for item in block.items
        )
    ]


def _within(inner: ast.AST, outer: ast.AST) -> bool:
    return outer.lineno <= inner.lineno <= (outer.end_lineno or outer.lineno)


def test_no_frame_is_written_without_holding_the_slot():
    """PT_data is one slot in both directions, and nothing correlates a reply.

    Two callers overlapping do not get slow answers, they get each other's:
    the poll reads the frame meant for a read-back, the read-back reads the
    poll's, and frame_body rejects both because neither is what was asked for.
    Every write of that property therefore happens with the lock held.
    """
    for name in ("_ask", "_setting"):
        function = _functions("rtcx.py")[name]
        blocks = _holds(function, "_talk")
        assert blocks, f"{name} writes PT_data without holding _talk"
        for call in _calls(function, "call"):
            assert any(_within(call, block) for block in blocks), (
                f"{name} reaches the gateway outside the lock at line {call.lineno}"
            )


def test_the_read_back_looks_at_the_data_it_will_publish():
    """A poll finishing mid-read-back builds a whole new reading.

    One fetched before the await is then an orphan: updated, republished, and
    carrying whatever the poll happened to see -- the control visibly snapping
    back to its old value, which is the failure a read-back exists to prevent.
    """
    function = _functions("coordinator.py")["async_read_back"]
    awaits = [node.lineno for node in ast.walk(function) if isinstance(node, ast.Await)]
    reads = [
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute) and node.attr == "data"
    ]
    assert awaits and reads
    assert min(reads) > max(awaits), (
        "async_read_back reads self.data before its last await, so what it "
        "updates may no longer be what is published"
    )


def test_a_failed_read_is_not_reported_as_a_failed_write():
    """cloud_errors turns a cloud failure into a message on someone's screen.

    A read-back that cannot confirm is worth a log line. Inside the block it
    becomes HomeAssistantError instead, and the person retries a change that
    already went through.
    """
    for file_name in _PLATFORMS:
        tree = _tree(file_name)
        blocks = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.With)
            and any(
                isinstance(item.context_expr, ast.Call)
                and getattr(item.context_expr.func, "id", None) == "cloud_errors"
                for item in node.items
            )
        ]
        for call in _calls(tree, "async_read_back"):
            assert not any(_within(call, block) for block in blocks), (
                f"{file_name}:{call.lineno} reads back inside cloud_errors()"
            )


def test_the_guard_comes_before_the_write_and_the_write_happens_once():
    """Both halves of this were wrong at once, and neither showed.

    A restack resolved a conflict by keeping both sides, which left the setting
    sent twice with _require_writable running after it -- so a field the model
    does not accept was written anyway, and written again, while the guard that
    exists to stop it reported nothing.
    """
    for file_name in _PLATFORMS:
        for name, function in _functions(file_name).items():
            setters = [
                call
                for call in ast.walk(function)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr.startswith("async_set_")
            ]
            if not setters:
                continue
            sent = [call.func.attr for call in setters]
            assert len(sent) == len(set(sent)), (
                f"{file_name}:{name} sends {sorted(sent)} -- the same setting twice"
            )
            for guard in _calls(function, "_require_writable"):
                assert guard.lineno < min(call.lineno for call in setters), (
                    f"{file_name}:{name} checks whether the field may be set "
                    "only after setting it"
                )

def test_the_device_state_is_asked_for_once_and_told_the_model():
    """Two ways this has already gone wrong, both silent.

    A restack left the poll reading the state twice -- once cached and once
    not -- which spends the round trip the cache exists to save and puts an
    unfiltered copy in the cache. And a call that forgot the model still
    parsed, still passed every test here, and would have raised TypeError the
    first time somebody moved a control.
    """
    tree = _tree("coordinator.py")
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
    }
    poll = _calls(functions["_async_poll"], "_device_state")
    assert len(poll) <= 1, "the poll asks the charger for its state more than once"
    direct = [
        call
        for name, function in functions.items()
        if name != "_device_state"
        for call in _calls(function, "async_device_state")
    ]
    assert not direct, (
        "async_device_state is reached around the cache at line "
        f"{direct[0].lineno if direct else 0}"
    )
    wanted = len(functions["_device_state"].args.args) - 1
    for call in _calls(tree, "_device_state"):
        assert len(call.args) + len(call.keywords) == wanted, (
            f"_device_state called with {len(call.args)} arguments at line "
            f"{call.lineno}, but it takes {wanted}"
        )
