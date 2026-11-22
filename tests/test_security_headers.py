"""Tests for the security headers on every response (FR-platform-modernization-18 to 21)."""

import re

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.security_headers import DOCS_POLICY, PERMISSIONS_POLICY

PAGES = ["/", "/admin", "/profiles", "/fleets", "/push-config"]
NONCE = re.compile(r"'nonce-([A-Za-z0-9_-]+)'")


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _directives(policy: str) -> dict[str, str]:
    return dict(part.strip().split(" ", 1) for part in policy.split(";"))


@pytest.mark.parametrize("path", [*PAGES, "/healthz", "/static/js/main.js"])
async def test_response_has_the_security_headers(client, path):
    resp = await client.get(path)

    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "same-origin"
    assert resp.headers["permissions-policy"] == PERMISSIONS_POLICY
    directives = _directives(resp.headers["content-security-policy"])
    assert directives["frame-ancestors"] == "'none'"
    assert directives["object-src"] == "'none'"


@pytest.mark.parametrize("path", PAGES)
async def test_page_policy_allows_scripts_only_from_the_server_and_the_nonce(client, path):
    resp = await client.get(path)

    directives = _directives(resp.headers["content-security-policy"])
    assert NONCE.fullmatch(directives["script-src"].removeprefix("'self' "))
    assert directives["style-src"] == "'self'"
    assert "unsafe-eval" not in resp.headers["content-security-policy"]
    assert "http" not in resp.headers["content-security-policy"]


async def test_each_response_gets_a_new_nonce(client):
    first = await client.get("/")
    second = await client.get("/")

    assert NONCE.search(first.headers["content-security-policy"]).group(1) != NONCE.search(
        second.headers["content-security-policy"]
    ).group(1)


async def test_push_config_import_map_carries_the_nonce_of_the_policy(client):
    resp = await client.get("/push-config")

    nonce = NONCE.search(resp.headers["content-security-policy"]).group(1)
    assert f'<script type="importmap" nonce="{nonce}">' in resp.text


@pytest.mark.parametrize("path", PAGES)
async def test_page_has_no_inline_script_or_event_handler_without_the_nonce(client, path):
    resp = await client.get(path)

    # A data block such as `type="application/json"` does not run, so the policy does not apply to it.
    scripts = re.findall(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)([^>]*)>", resp.text)
    assert all("nonce=" in attrs for attrs in scripts)
    assert not re.search(r"\son[a-z]+=", resp.text)


async def test_permissions_policy_allows_serial_for_the_server_only_and_leaves_out_bluetooth(client):
    resp = await client.get("/")

    features = dict(item.split("=") for item in resp.headers["permissions-policy"].split(", "))
    assert features["serial"] == "(self)"
    # Linux Chromium does not know `bluetooth`, and an unknown feature writes an error to the console.
    assert "bluetooth" not in features
    assert features["usb"] == "()"
    assert features["camera"] == "()"


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
async def test_api_docs_get_the_docs_policy_that_allows_the_cdn(client, path):
    resp = await client.get(path)

    assert resp.status_code == 200
    assert resp.headers["content-security-policy"] == DOCS_POLICY
    assert "https://cdn.jsdelivr.net" in _directives(DOCS_POLICY)["script-src"]
