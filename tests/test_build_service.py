"""Tests for build service and firmware routes."""

import json
import os

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.config import Settings
from mtfwbuilder.services.build_service import (
    BuildContext,
    DeviceVariant,
    _parse_progress,
)
from mtfwbuilder.services.cleanup_service import cleanup_old_builds
from tests.conftest import FIRMWARE_VERSION


class TestProgressParsing:
    """Tests for PlatformIO stdout line parsing."""

    def test_compiling_detected(self):
        assert _parse_progress("Compiling .pio/build/tbeam/src/main.cpp.o") == "compiling"

    def test_building_pio_detected(self):
        assert _parse_progress("Building .pio/build/tbeam/src/main.cpp.o") == "compiling"

    def test_linking_detected(self):
        assert _parse_progress("Linking .pio/build/tbeam/firmware.elf") == "linking"

    def test_packaging_detected(self):
        assert _parse_progress("Checking size .pio/build/tbeam/firmware.bin") == "packaging"

    def test_unrelated_line_returns_none(self):
        assert _parse_progress("Installing ESP32 platform") is None
        assert _parse_progress("") is None


@pytest.mark.parametrize(
    ("architecture", "firmware_format"),
    [
        ("esp32", "bin"),
        ("esp32s3", "bin"),
        ("nrf52840", "uf2"),
        ("rp2040", "uf2"),
        ("rp2350", "uf2"),
        ("nrf54l15", "zip"),
        ("stm32", "bin"),
    ],
)
def test_firmware_format_follows_the_flash_method_of_the_architecture(architecture, firmware_format):
    variant = DeviceVariant(id="env", name="Env", manufacturer="Other", architecture=architecture)

    assert variant.firmware_format == firmware_format


class TestBuildContext:
    """Tests for BuildContext initialization."""

    def test_build_dir_created(self, temp_dir):
        settings = Settings(temp_dir=temp_dir)
        variant = DeviceVariant(id="tbeam", name="T-Beam", manufacturer="LILYGO", architecture="esp32")
        ctx = BuildContext(
            build_id="build_123_456",
            variant=variant,
            config_content='{"test": true}',
            settings=settings,
        )
        assert ctx.build_dir.exists()
        assert ctx.build_dir == temp_dir / "build_123_456"

    def test_build_log_starts_empty(self, temp_dir):
        settings = Settings(temp_dir=temp_dir)
        variant = DeviceVariant(id="tbeam", name="T-Beam", manufacturer="LILYGO", architecture="esp32")
        ctx = BuildContext(
            build_id="build_123_456",
            variant=variant,
            config_content='{"test": true}',
            settings=settings,
        )
        assert ctx.build_log == []


class TestCleanupService:
    """Tests for build directory cleanup."""

    def test_cleanup_old_builds_removes_expired(self, temp_dir):
        old_build = temp_dir / ("0" * 32)
        old_build.mkdir()
        (old_build / "firmware.bin").write_bytes(b"\x00")
        os.utime(old_build, (1_000_000, 1_000_000))

        settings = Settings(temp_dir=temp_dir, build_max_age_seconds=1)

        removed = cleanup_old_builds(settings)
        assert removed == 1
        assert not old_build.exists()

    def test_cleanup_old_builds_keeps_recent(self, temp_dir):
        recent = temp_dir / ("1" * 32)
        recent.mkdir()

        settings = Settings(temp_dir=temp_dir, build_max_age_seconds=3600)

        removed = cleanup_old_builds(settings)
        assert removed == 0
        assert recent.exists()


class TestFirmwareRoutes:
    """Tests for firmware builder API routes."""

    @pytest.fixture
    async def client(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("firmware_source")
    async def test_build_unknown_variant(self, client):
        resp = await client.post(
            "/api/v1/build-firmware",
            data={"variant": "nonexistent-xyz", "config_source": "current", "config_json": "{}"},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["msg"] == f"Variant not in {FIRMWARE_VERSION}"

    @pytest.mark.asyncio
    async def test_build_no_variant(self, client):
        resp = await client.post("/api/v1/build-firmware", data={})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_build_no_config(self, client):
        resp = await client.post(
            "/api/v1/build-firmware",
            data={"variant": "tbeam", "config_source": "current"},
        )
        assert resp.status_code == 400
        assert "No configuration" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_build_valid_request_returns_202_with_build_id_and_progress_url(self, client):
        config = json.dumps({"owner_long_name": "TestNode"})
        resp = await client.post(
            "/api/v1/build-firmware",
            data={"variant": "tbeam", "config_source": "current", "config_json": config},
        )
        assert resp.status_code == 202
        assert set(resp.json()) == {"build_id", "progress_url"}

    @pytest.mark.asyncio
    async def test_download_missing_build(self, client):
        resp = await client.get("/api/v1/download-firmware/build_nonexistent_123")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_system_info(self, client):
        resp = await client.get("/api/v1/system-info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "version" in data
