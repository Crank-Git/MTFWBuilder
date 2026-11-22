"""Tests for API routes."""

import json

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestConfigGeneratorRoutes:
    """Tests for /api/v1/ config generation endpoints."""

    @pytest.mark.asyncio
    async def test_generate_valid_config(self, client):
        resp = await client.post(
            "/api/v1/generate",
            json={"owner_long_name": "TestNode", "owner_short_name": "TST"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        parsed = json.loads(data["content"])
        assert parsed["USERPREFS_CONFIG_OWNER_LONG_NAME"] == "TestNode"

    @pytest.mark.asyncio
    async def test_generate_empty_config(self, client):
        resp = await client.post("/api/v1/generate", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        parsed = json.loads(data["content"])
        assert "USERPREFS_CHANNELS_TO_WRITE" in parsed

    @pytest.mark.asyncio
    async def test_preview_returns_jsonc(self, client):
        resp = await client.post(
            "/api/v1/preview",
            json={"owner_long_name": "PreviewNode", "channels_to_write": "1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "PreviewNode" in data["content"]

    @pytest.mark.asyncio
    async def test_download_returns_attachment(self, client):
        resp = await client.post(
            "/api/v1/download",
            json={"owner_long_name": "DLNode"},
        )
        assert resp.status_code == 200
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert "userPrefs.jsonc" in resp.headers["content-disposition"]
        parsed = json.loads(resp.text)
        assert parsed["USERPREFS_CONFIG_OWNER_LONG_NAME"] == "DLNode"

    @pytest.mark.asyncio
    async def test_config_form_has_no_device_name_field(self, client):
        """Upstream reads no device name key, so the form offers no such field."""
        resp = await client.get("/")
        assert resp.status_code == 200
        assert 'name="device_name"' not in resp.text
        assert 'name="owner_long_name"' in resp.text

    @pytest.mark.asyncio
    async def test_preview_userprefs_valid_file(self, client):
        content = json.dumps({"USERPREFS_CONFIG_OWNER_SHORT_NAME": "TST", "USERPREFS_CHANNEL_0_NAME": "Primary"})
        resp = await client.post(
            "/api/v1/preview-userprefs",
            files={"userPrefs": ("userPrefs.jsonc", content.encode(), "application/json")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["checks"]["owner_name"] is True
        assert data["checks"]["channel_name"] is True
        assert data["filename"] == "userPrefs.jsonc"

    @pytest.mark.asyncio
    async def test_preview_userprefs_too_large(self, client):
        big_content = "x" * 70000
        resp = await client.post(
            "/api/v1/preview-userprefs",
            files={"userPrefs": ("big.jsonc", big_content.encode(), "text/plain")},
        )
        assert resp.status_code == 413
        assert resp.json() == {"detail": "The file is larger than 64 KB."}

    @pytest.mark.asyncio
    async def test_preview_userprefs_of_a_file_that_is_not_utf8_returns_422_without_the_decode_error(self, client):
        resp = await client.post(
            "/api/v1/preview-userprefs",
            files={"userPrefs": ("bad.jsonc", b"\xff\xfe\xfa", "text/plain")},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["msg"] == "File is not valid UTF-8 text"
        assert "codec" not in resp.text
        assert "0xff" not in resp.text

    @pytest.mark.asyncio
    async def test_preview_userprefs_of_a_file_without_a_name_returns_422(self, client):
        resp = await client.post("/api/v1/preview-userprefs", files={"userPrefs": ("", b"{}", "text/plain")})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_htmx_preview_userprefs_error_returns_the_field_error_fragment(self, client):
        resp = await client.post(
            "/api/v1/preview-userprefs",
            files={"userPrefs": ("bad.jsonc", b"\xff", "text/plain")},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 422
        assert resp.headers["content-type"].startswith("text/html")
        assert "File is not valid UTF-8 text" in resp.text

    @pytest.mark.asyncio
    async def test_preview_userprefs_no_file(self, client):
        resp = await client.post("/api/v1/preview-userprefs")
        assert resp.status_code == 422  # FastAPI validation error — missing required file
