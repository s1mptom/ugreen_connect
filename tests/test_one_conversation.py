"""The shape of a write, checked without a Home Assistant anywhere.

Properties that are invisible at a glance and expensive to lose. Each of
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
    So both ways a frame goes out do their talking inside the lock, not merely
    take it: `_ask` hands over to `_ask_locked` there, and `_setting` reaches
    the gateway there.
    """
    for name, reaches in (("_ask", "_ask_locked"), ("_setting", "call")):
        function = _functions("rtcx.py")[name]
        blocks = _holds(function, "_talk")
        assert blocks, f"{name} writes PT_data without holding _talk"
        calls = _calls(function, reaches)
        assert calls, f"{name} no longer goes through {reaches} -- update this test"
        for call in calls:
            assert any(_within(call, block) for block in blocks), (
                f"{name} reaches the gateway outside the lock at line {call.lineno}"
            )


def test_the_settle_is_waited_out_with_the_slot_still_held():
    """A precaution is only one while nothing else can write during it.

    Releasing the lock and then sleeping would leave the slot open for exactly
    the window the sleep exists to cover, and every test would stay green:
    moving the sleep out, or deleting it, changed nothing in this suite until
    this. Whether the race is real is a separate question -- no setting has
    been seen lost, and SETTING_SETTLE_SECONDS says so -- but if the wait is
    kept it has to be waited out holding the slot.
    """
    function = _functions("rtcx.py")["_setting"]
    blocks = _holds(function, "_talk")
    assert blocks, "_setting writes PT_data without holding _talk"
    sleeps = _calls(function, "sleep")
    assert sleeps, "the settle is gone -- drop this test with it"
    for sleep in sleeps:
        assert any(_within(sleep, block) for block in blocks), (
            f"_setting waits out the settle outside the lock at line {sleep.lineno}"
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


def test_the_state_cache_is_written_without_yielding_after_the_read():
    """What makes the cache hold the newest read, rather than the last to land.

    The poll corrects its reading from `self._state` at the end, and a
    read-back can write that cache while the poll is still working. Which of
    the two ends up in it is decided here: `_device_state` writes the cache in
    the same step as the read that returned, so an older read cannot win. The
    lock in rtcx orders the conversations; it does not order what happens after
    one returns.

    So `_remember_params`, which sits in that gap, has to stay synchronous. It
    writes through a Store, and a Store save is exactly the thing that grows an
    await later -- at which point the poll starts publishing the older state
    and the tests that hold a poll open still pass, because the interleaving
    they hold is a different one.
    """
    tree = _tree("coordinator.py")
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    state_read = [
        node.lineno
        for node in ast.walk(functions["_device_state"])
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and getattr(node.value.func, "attr", None) == "async_device_state"
    ]
    assert len(state_read) == 1, "_device_state no longer reads the state once"
    written = [
        node.lineno
        for node in ast.walk(functions["_device_state"])
        for target in getattr(node, "targets", [])
        if isinstance(target, ast.Subscript)
        and getattr(target.value, "attr", None) == "_state"
    ]
    assert len(written) == 1, "_device_state no longer writes the cache once"
    between = [
        node.lineno
        for node in ast.walk(functions["_device_state"])
        if isinstance(node, ast.Await) and state_read[0] < node.lineno <= written[0]
    ]
    assert not between, (
        f"_device_state waits at line {between[0]} between reading the state "
        "and caching it, so a later read can be overtaken"
    )
    remember = functions["_remember_params"]
    assert not isinstance(remember, ast.AsyncFunctionDef) and not [
        node for node in ast.walk(remember) if isinstance(node, ast.Await)
    ], "_remember_params awaits now, and it runs in that gap"
