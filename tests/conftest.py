"""Load the Home-Assistant-free modules without a Home Assistant install.

Importing it through the package would run ``custom_components/ugreen_connect/__init__.py``,
which pulls in Home Assistant. The session logic is deliberately free of those imports, so
the module is compiled straight from its source text -- which also fails loudly the moment
someone adds a Home Assistant import to it.

The source is compiled here rather than imported so that ``__pycache__`` is never
consulted: two edits a second apart that leave the file the same length look unchanged
to the bytecode cache, and the tests would then run against the previous version.
"""

import importlib.util
import sys
from pathlib import Path

_COMPONENT = (
    Path(__file__).resolve().parents[1] / "custom_components" / "ugreen_connect"
)


def _load(name: str, file_name: str, package: str | None = None):
    path = _COMPONENT / file_name
    spec = importlib.util.spec_from_loader(name, loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    # A module doing `from .x import y` needs to know which package it is in.
    if package is not None:
        module.__package__ = package
    sys.modules[name] = module
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    return module


session = _load("ugreen_session", "session.py")
protocol = _load("ugreen_protocol", "protocol.py")


# ``api`` is Home-Assistant-free for the same reason and can be exercised the
# same way, but unlike the two above it imports from ``.const`` -- so it is
# loaded inside a stand-in package with that module already in it.
_PKG = "ugreen_pkg"
_package = importlib.util.module_from_spec(
    importlib.util.spec_from_loader(_PKG, loader=None)
)
_package.__path__ = []
sys.modules[_PKG] = _package

_load(f"{_PKG}.const", "const.py")

# It does need aiohttp and cryptography, which the other two do not. Where they
# are absent the module is simply not loaded and the tests over it skip, so the
# promise this file makes -- that these run without Home Assistant -- still holds
# for someone with neither installed.
# Only the third-party names may be missing. A ModuleNotFoundError naming
# anything else is `api.py` itself being broken -- a typo in a relative import,
# a constant that moved -- and swallowing that made a broken module
# indistinguishable from an environment without aiohttp: the same
# "4 skipped" either way, in CI where that is the normal signature.
_OPTIONAL = {"aiohttp", "cryptography"}

try:
    api = _load(f"{_PKG}.api", "api.py", package=_PKG)
except ModuleNotFoundError as err:  # pragma: no cover - depends on the environment
    if (err.name or "").split(".")[0] not in _OPTIONAL:
        raise
    api = None
