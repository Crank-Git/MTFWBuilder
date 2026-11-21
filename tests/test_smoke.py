"""Smoke tests: the app starts and serves its pages (FR-foundation-10)."""

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.parametrize("path", ["/", "/admin"])
async def test_page_returns_200(client, path):
    resp = await client.get(path)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


async def test_healthz_returns_status_ok(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
