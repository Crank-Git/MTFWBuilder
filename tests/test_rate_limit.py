"""Tests for the rate-limit key and the rate limits that the settings set."""

from ipaddress import ip_network

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.config import Settings, load_settings
from mtfwbuilder.rate_limit import configure_rate_limits, limiter

PROXY = "10.0.0.2"


@pytest.fixture(autouse=True)
def _empty_limiter_storage():
    """Clear the counts, because every app in the suite shares one limiter."""
    limiter.reset()
    yield
    limiter.reset()


def _client(app, peer: str) -> AsyncClient:
    """Return a client whose requests reach the app from the address `peer`."""
    transport = ASGITransport(app=app, client=(peer, 40000))
    return AsyncClient(transport=transport, base_url="http://test", follow_redirects=False)


async def _login_statuses(app, peer: str, count: int, forwarded_for: str | None = None) -> list[int]:
    headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else {}
    async with _client(app, peer) as c:
        return [
            (await c.post("/admin/login", data={"admin_key": "wrong"}, headers=headers)).status_code
            for _ in range(count)
        ]


@pytest.fixture
def limited_app(app, settings):
    """Return the app with a login limit of 2 per minute and a trusted proxy at `PROXY`."""
    settings.login_rate_limit = "2/minute"
    settings.trusted_proxies = [ip_network(PROXY)]
    configure_rate_limits(settings)
    return app


class TestTrustedProxies:
    async def test_two_clients_behind_a_trusted_proxy_have_separate_counts(self, limited_app):
        assert await _login_statuses(limited_app, PROXY, 3, "203.0.113.1") == [401, 401, 429]
        assert await _login_statuses(limited_app, PROXY, 1, "203.0.113.2") == [401]

    async def test_an_untrusted_peer_header_has_no_effect(self, limited_app):
        assert await _login_statuses(limited_app, "198.51.100.9", 2, "203.0.113.1") == [401, 401]
        assert await _login_statuses(limited_app, "198.51.100.9", 1, "203.0.113.2") == [429]

    async def test_the_key_is_the_rightmost_untrusted_hop(self, limited_app):
        # A client can write any value at the left end of the header, so the value there is not the key.
        assert await _login_statuses(limited_app, PROXY, 2, "1.1.1.1, 203.0.113.1") == [401, 401]
        assert await _login_statuses(limited_app, PROXY, 1, "2.2.2.2, 203.0.113.1") == [429]

    async def test_trusted_hops_in_the_header_are_skipped(self, limited_app, settings):
        settings.trusted_proxies = [ip_network("10.0.0.0/24")]
        configure_rate_limits(settings)
        assert await _login_statuses(limited_app, PROXY, 2, "203.0.113.1, 10.0.0.7") == [401, 401]
        assert await _login_statuses(limited_app, PROXY, 1, "203.0.113.1") == [429]

    async def test_a_trusted_peer_without_the_header_is_its_own_key(self, limited_app):
        assert await _login_statuses(limited_app, PROXY, 3) == [401, 401, 429]


class TestLimitsFromSettings:
    async def test_login_limit_reads_the_login_rate_limit_setting(self, app, settings):
        settings.login_rate_limit = "1/minute"
        configure_rate_limits(settings)
        assert await _login_statuses(app, "198.51.100.1", 2) == [401, 429]

    async def test_build_limit_reads_the_build_rate_limit_setting(self, app, settings):
        settings.build_rate_limit = "1/minute"
        configure_rate_limits(settings)
        async with _client(app, "198.51.100.1") as c:
            statuses = [(await c.post("/api/v1/build-firmware", data={})).status_code for _ in range(2)]
        assert statuses == [400, 429]


class TestSettings:
    def test_trusted_proxies_reads_a_comma_separated_variable(self, settings_env, monkeypatch):
        monkeypatch.setenv("MTFW_TRUSTED_PROXIES", "172.18.0.0/16, 10.0.0.2")
        assert load_settings().trusted_proxies == [ip_network("172.18.0.0/16"), ip_network("10.0.0.2")]

    def test_trusted_proxies_is_empty_by_default(self, settings):
        assert settings.trusted_proxies == []

    def test_an_invalid_trusted_proxy_fails_at_load(self, settings_env, monkeypatch):
        monkeypatch.setenv("MTFW_TRUSTED_PROXIES", "nginx")
        with pytest.raises(ValueError):
            load_settings()

    @pytest.mark.parametrize("name", ["host", "port"])
    def test_unread_setting_is_removed(self, name):
        assert name not in Settings.model_fields
