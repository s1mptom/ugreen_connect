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
# ``protocol`` is free of Home Assistant and of third-party imports alike, so it
# loads unconditionally -- inside the stand-in package, because ``rtcx`` reaches
# it as ``.protocol`` and two copies of it under different names would let a test
# assert against constants the code under test is not using.
protocol = _load(f"{_PKG}.protocol", "protocol.py", package=_PKG)

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

def _optional(name: str, file_name: str):
    """Load a module that needs a third-party package, or return None.

    One module at a time, so that a broken ``rtcx`` cannot present itself as an
    environment without aiohttp and take ``api``'s tests down with it -- the
    same confusion, one module along.
    """
    try:
        return _load(f"{_PKG}.{name}", file_name, package=_PKG)
    except ModuleNotFoundError as err:  # pragma: no cover - depends on the env
        if (err.name or "").split(".")[0] not in _OPTIONAL:
            raise
        return None


api = _optional("api", "api.py")
# ``rtcx`` needs aiohttp for the same reason, and reaches ``api`` as ``.api``:
# with that one absent there is no package for this one to be loaded into, and
# the error would name the stand-in package rather than the missing dependency.
rtcx = _optional("rtcx", "rtcx.py") if api is not None else None
