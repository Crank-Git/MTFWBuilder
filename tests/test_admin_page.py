"""Tests for the admin page: sign-in, the session cookie, CSRF tokens and the dashboard."""

import re
import time
from ipaddress import ip_network

import aiosqlite
import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner

from mtfwbuilder.auth import CSRF_COOKIE, SESSION_COOKIE, csrf_token
from mtfwbuilder.database import init_db, save_build
from mtfwbuilder.rate_limit import limiter
from mtfwbuilder.services.firmware_updater import ReleaseNotFound
from tests.conftest import sign_in_admin

CSRF_FIELD = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as c:
        yield c


@pytest.fixture
async def database(settings):
    """Return the database path, after the tables are created."""
    await init_db(settings)
    return settings.database_path


async def _page_token(client):
    """Return the CSRF token in the forms of `/admin`, after the client gets the CSRF cookie."""
    resp = await client.get("/admin")
    assert resp.status_code == 200
    tokens = CSRF_FIELD.findall(resp.text)
    assert tokens
    return tokens[0]


def _set_cookie_header(resp, name):
    return next(value for key, value in resp.headers.multi_items() if key == "set-cookie" and value.startswith(name))


# FR-frontend-htmx-11


async def test_admin_without_a_session_shows_a_password_field_that_posts_to_login(client):
    resp = await client.get("/admin")

    assert resp.status_code == 200
    assert 'type="password"' in resp.text
    assert 'name="admin_key"' in resp.text
    assert 'action="/admin/login"' in resp.text
    assert "Sign out" not in resp.text


async def test_wrong_password_shows_the_sign_in_form_with_wrong_password(client):
    token = await _page_token(client)

    resp = await client.post("/admin/login", data={"admin_key": "wrong", "csrf_token": token})

    assert resp.status_code == 401
    assert "Wrong password." in resp.text
    assert 'type="password"' in resp.text


async def test_eleventh_sign_in_in_one_minute_shows_too_many_attempts(client):
    token = await _page_token(client)
    for _ in range(10):
        await client.post("/admin/login", data={"admin_key": "wrong", "csrf_token": token})

    resp = await client.post("/admin/login", data={"admin_key": "wrong", "csrf_token": token})

    assert resp.status_code == 429
    assert "Too many attempts. Wait one minute." in resp.text
    assert 'type="password"' in resp.text


async def test_rate_limit_on_another_route_keeps_the_json_response(app, client):
    @app.post("/limited-for-test")
    @limiter.limit("1/minute")
    async def limited(request: Request):
        return {}

    await client.post("/limited-for-test")
    resp = await client.post("/limited-for-test")

    assert resp.status_code == 429
    assert resp.headers["content-type"].startswith("application/json")


# FR-frontend-htmx-13


async def test_correct_password_over_plain_http_gives_a_session_cookie_without_secure(client, admin_password):
    token = await _page_token(client)

    resp = await client.post("/admin/login", data={"admin_key": admin_password, "csrf_token": token})

    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin"
    cookie = _set_cookie_header(resp, SESSION_COOKIE)
    assert "httponly" in cookie.lower()
    assert "secure" not in cookie.lower()


async def test_correct_password_over_https_gives_a_secure_session_cookie(app, admin_password):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test", follow_redirects=False) as c:
        token = await _page_token(c)

        resp = await c.post("/admin/login", data={"admin_key": admin_password, "csrf_token": token})

    assert resp.status_code == 303
    assert "secure" in _set_cookie_header(resp, SESSION_COOKIE).lower()


# FR-platform-modernization-16
@pytest.mark.parametrize(
    ("peer", "forwarded_proto", "secure"),
    [
        ("172.30.80.5", "https", True),
        ("203.0.113.9", "https", False),
        ("172.30.80.5", None, False),
    ],
    ids=["trusted-proxy-https", "untrusted-peer-https", "trusted-proxy-plain-http"],
)
async def test_session_cookie_is_secure_only_for_https_from_a_trusted_proxy(
    app, admin_password, peer, forwarded_proto, secure
):
    app.state.settings.trusted_proxies = [ip_network("172.30.80.0/24")]
    headers = {"X-Forwarded-Proto": forwarded_proto} if forwarded_proto else {}
    transport = ASGITransport(app=app, client=(peer, 40000))
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as c:
        token = await _page_token(c)

        resp = await c.post("/admin/login", data={"admin_key": admin_password, "csrf_token": token}, headers=headers)

    assert resp.status_code == 303
    assert ("secure" in _set_cookie_header(resp, SESSION_COOKIE).lower()) is secure


@pytest.mark.usefixtures("database")
async def test_sign_in_gives_a_new_csrf_token(client, admin_password):
    token = await _page_token(client)

    await client.post("/admin/login", data={"admin_key": admin_password, "csrf_token": token})

    assert await _page_token(client) != token


# FR-frontend-htmx-14


@pytest.mark.parametrize("path", ["/admin/login", "/admin/logout", "/api/v1/update-firmware", "/api/v1/cleanup"])
async def test_admin_post_without_a_csrf_token_returns_403(client, settings, path):
    sign_in_admin(client, settings)

    resp = await client.post(path, data={"admin_key": "x"})

    assert resp.status_code == 403


@pytest.mark.parametrize("path", ["/admin/login", "/api/v1/cleanup"])
async def test_admin_post_with_a_forged_csrf_token_returns_403(client, settings, path):
    sign_in_admin(client, settings)

    resp = await client.post(path, data={"csrf_token": "forged.token"})

    assert resp.status_code == 403


async def test_csrf_token_of_another_csrf_cookie_returns_403(client, settings):
    sign_in_admin(client, settings)

    resp = await client.post("/api/v1/cleanup", data={"csrf_token": csrf_token("another-nonce", settings)})

    assert resp.status_code == 403


async def test_admin_post_without_the_csrf_cookie_returns_403(client, settings):
    token = sign_in_admin(client, settings)
    client.cookies.delete(CSRF_COOKIE)

    resp = await client.post("/api/v1/cleanup", data={"csrf_token": token})

    assert resp.status_code == 403


@pytest.mark.usefixtures("database")
async def test_every_admin_form_carries_a_csrf_token(client, settings):
    sign_in_admin(client, settings)
    signed_in = (await client.get("/admin")).text
    client.cookies.delete(SESSION_COOKIE)
    signed_out = (await client.get("/admin")).text

    for page in (signed_in, signed_out):
        forms = re.findall(r"<form\b.*?</form>", page, re.S)
        assert forms
        for form in forms:
            assert 'name="csrf_token"' in form, form


async def test_cleanup_with_a_valid_csrf_token_returns_200(client, settings):
    token = sign_in_admin(client, settings)

    resp = await client.post("/api/v1/cleanup", data={"csrf_token": token})

    assert resp.status_code == 200
    assert resp.json()["success"] is True


async def test_sign_out_with_a_valid_csrf_token_clears_the_session(client, settings):
    token = sign_in_admin(client, settings)

    resp = await client.post("/admin/logout", data={"csrf_token": token})

    assert resp.status_code == 303
    assert _set_cookie_header(resp, SESSION_COOKIE).startswith(f'{SESSION_COOKIE}=""')


# FR-frontend-htmx-12


@pytest.mark.usefixtures("firmware_source")
async def test_dashboard_shows_firmware_sources_install_cleanup_recent_builds_and_sign_out(client, settings):
    sign_in_admin(client, settings)

    resp = await client.get("/admin")

    assert resp.status_code == 200
    assert 'type="password"' not in resp.text
    assert 'id="firmware-sources"' in resp.text
    assert "v2.7.26.54e0d8d" in resp.text
    assert 'hx-post="/api/v1/firmware/install"' in resp.text
    assert 'hx-post="/api/v1/cleanup"' in resp.text
    assert 'id="recent-builds"' in resp.text
    assert 'action="/admin/logout"' in resp.text
    assert "Sign out" in resp.text


async def test_dashboard_lists_the_recent_builds_from_the_builds_table(client, settings, database):
    async with aiosqlite.connect(database) as db:
        await save_build(
            db,
            {
                "id": "b0123456789abcdef",
                "variant": "heltec-v3",
                "architecture": "esp32s3",
                "firmware_format": "bin",
                "status": "complete",
                "firmware_path": None,
                "error_message": None,
                "config_hash": None,
                "firmware_version": "v2.7.26.54e0d8d",
                "fleet_run_id": None,
            },
        )
    sign_in_admin(client, settings)

    resp = await client.get("/admin")

    recent = resp.text.split('id="recent-builds"', 1)[1]
    assert "b0123456789abcdef" in recent
    assert "heltec-v3" in recent
    assert "complete" in recent


@pytest.mark.usefixtures("database")
async def test_dashboard_links_back_to_the_build_page_and_not_to_firmware_builder(client, settings):
    sign_in_admin(client, settings)

    resp = await client.get("/admin")

    assert 'href="/"' in resp.text
    assert "/firmware-builder" not in resp.text


async def test_htmx_cleanup_returns_an_html_fragment_with_the_message(client, settings):
    token = sign_in_admin(client, settings)

    resp = await client.post("/api/v1/cleanup", data={"csrf_token": token}, headers={"HX-Request": "true"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Removed 0 old build directories." in resp.text


async def test_htmx_update_failure_returns_an_html_fragment_with_the_error(app, client, settings):
    def no_release(channel):
        raise ReleaseNotFound(channel)

    app.state.firmware_installs.resolver = no_release
    token = sign_in_admin(client, settings)

    resp = await client.post("/api/v1/update-firmware", data={"csrf_token": token}, headers={"HX-Request": "true"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Cannot get the firmware version from the upstream firmware list." in resp.text


# Edge case: the admin session expires.


class _OldSigner(TimestampSigner):
    def get_timestamp(self):
        return int(time.time()) - 7200


def _sign_in_expired(client, settings):
    token = sign_in_admin(client, settings)
    client.cookies.set(SESSION_COOKIE, _OldSigner(settings.secret_key).sign("admin").decode())
    return token


async def test_admin_post_after_the_session_expires_returns_401(client, settings):
    token = _sign_in_expired(client, settings)

    resp = await client.post("/api/v1/cleanup", data={"csrf_token": token})

    assert resp.status_code == 401


async def test_htmx_admin_post_after_the_session_expires_sends_the_page_to_sign_in(client, settings):
    token = _sign_in_expired(client, settings)

    resp = await client.post("/api/v1/update-firmware", data={"csrf_token": token}, headers={"HX-Request": "true"})
    page = await client.get(resp.headers["HX-Redirect"])

    assert resp.status_code == 401
    assert 'type="password"' in page.text
