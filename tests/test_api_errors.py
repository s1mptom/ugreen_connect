"""What a failure is called decides what the owner is asked to do about it.

`UgreenAuthError` becomes `ConfigEntryAuthFailed`, which stops the integration
and asks for the password again. That is right for a password that was actually
rejected and wrong for anything else -- and a re-login is exactly where the two
meet, because the call it makes is the one the cloud rate-limits.
"""

import asyncio

import pytest
from conftest import api as api_module

pytestmark = pytest.mark.skipif(
    api_module is None, reason="api.py needs aiohttp and cryptography"
)


def _api(raises: Exception | None):
    """A client whose re-login fails in a given way, and nothing else."""
    client = api_module.UgreenApi(session=None, base_url="https://example.invalid")
    client._credentials = ("someone@example.com", "hunter2")

    async def _login(*_args, **_kwargs):
        if raises is not None:
            raise raises

    client.login = _login
    return client


def test_a_rate_limit_during_re_login_is_not_a_rejected_password():
    """It has to reach the caller as itself.

    Swallowed as an ordinary failure, `_relogin` returns False, the original
    `no permission` stands, and the owner is asked to re-enter a password that
    was never wrong -- over a cooldown that would have cleared by itself.
    """
    client = _api(api_module.UgreenRateLimited("oauth/authorize: too many requests"))
    with pytest.raises(api_module.UgreenRateLimited):
        asyncio.run(client._relogin(force=True))


def test_an_ordinary_re_login_failure_still_just_fails():
    client = _api(api_module.UgreenError("the cloud is having a moment"))
    assert asyncio.run(client._relogin(force=True)) is False


def test_a_re_login_that_works_says_so():
    assert asyncio.run(_api(None)._relogin(force=True)) is True


def test_rate_limited_is_not_an_auth_error():
    """Or the coordinator would turn it into a re-auth flow regardless."""
    assert issubclass(api_module.UgreenRateLimited, api_module.UgreenError)
    assert not issubclass(api_module.UgreenRateLimited, api_module.UgreenAuthError)
