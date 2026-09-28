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


# --- what an error says, which somebody will post --------------------------------


@pytest.fixture
def _forgetful():
    from conftest import logsafe

    logsafe._known.clear()
    logsafe._compiled = None
    yield logsafe
    logsafe._known.clear()
    logsafe._compiled = None


def _answering(answer: dict):
    client = api_module.UgreenApi(session=None, base_url="https://example.invalid")

    async def _post(*_args, **_kwargs):
        return answer

    client._post = _post
    return client


def test_a_login_answer_without_a_token_is_described_not_quoted(_forgetful):
    """The answer is the refresh token and the user id."""
    client = _answering(
        {"code": api_module.CODE_OK, "data": {"refresh": "eyJsecret-refresh", "userId": 9}}
    )
    with pytest.raises(api_module.UgreenError) as err:
        asyncio.run(client.login("someone@example.com", "hunter2-password"))
    assert "eyJsecret-refresh" not in str(err.value)
    assert "keys ['refresh', 'userId']" in str(err.value)


def test_a_login_teaches_the_log_its_secrets(_forgetful):
    """The password and the user id, once known, are scrubbed wherever they go.

    Not the tokens: one arrives every twenty minutes, and remembered they would
    make the list grow for as long as Home Assistant runs.
    """
    client = _answering(
        {
            "code": api_module.CODE_OK,
            "data": {"accessToken": "eyJaccess-token-value", "userId": 1234567890123},
        }
    )
    asyncio.run(client.login("someone@example.com", "hunter2-password"))
    assert _forgetful.scrub("hunter2-password 1234567890123") == "<password> <user>"
    assert _forgetful.scrub("eyJaccess-token-value") == "eyJaccess-token-value"


def test_an_error_cleans_its_own_message(_forgetful):
    """Home Assistant writes the traceback, on loggers this integration does not clean."""
    _forgetful.remember_charger("an-iot-id-long", "FF7J0000000000001")
    err = api_module.UgreenError("gateway refused FF7J0000000000001")
    assert "FF7J0000000000001" not in str(err)
    assert "FF7J0000000000001" not in repr(err)


def test_a_network_error_does_not_carry_aiohttp_s_own_along(_forgetful, monkeypatch):
    """aiohttp's message is the URL, and a GET's query can name the charger.

    The text goes into the error, cleaned. The original is not chained, since
    Home Assistant would print it under the error, uncleaned.
    """
    import aiohttp

    unit = "FF7J0000000000001"
    _forgetful.remember_charger("an-iot-id-long", unit)
    monkeypatch.setattr(api_module, "RETRY_DELAY", 0)

    class _Session:
        def request(self, *_args, **_kwargs):
            raise aiohttp.InvalidURL(f"https://example.invalid/list?deviceUniqueCode={unit}")

    client = api_module.UgreenApi(session=_Session(), base_url="https://example.invalid")
    with pytest.raises(api_module.UgreenError) as err:
        asyncio.run(client._call("/list", {"deviceUniqueCode": unit}, "GET", auth=False))
    assert unit not in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__
