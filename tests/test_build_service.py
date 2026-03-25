"""Tests for build service and firmware routes."""

import json
import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.main import create_app
from mtfwbuilder.services.build_service import (
    BuildContext,
    BuildProgress,
    generate_build_id,
    _parse_progress,
    _scrub_firmware_tree,
)
from mtfwbuilder.services.cleanup_service import cleanup_old_builds, cleanup_build_directory
from mtfwbuilder.services.device_registry import DeviceRegistry, DeviceVariant


class TestBuildId:
    """Tests for build ID generation."""

    def test_build_id_format(self):
        bid = generate_build_id()
        assert bid.startswith("build_")
        parts = bid.split("_")
        assert len(parts) == 4  # build, timestamp, pid, counter
        assert parts[1].isdigit()

    def test_build_ids_unique(self):
        ids = {generate_build_id() for _ in range(10)}
        assert len(ids) == 10


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

    def test_success_detected(self):
        assert _parse_progress("========================= [SUCCESS] =========================") == "complete"

    def test_success_no_false_positive(self):
        """'Successfully installed' should NOT trigger complete."""
        assert _parse_progress("Successfully installed toolchain") is None

    def test_error_detected(self):
        assert _parse_progress("error: undefined reference to 'foo'") == "failed"

    def test_failed_banner_detected(self):
        assert _parse_progress("========================= [FAILED] =========================") == "failed"

    def test_warning_not_error(self):
        """Warnings should not trigger 'failed' status."""
        assert _parse_progress("warning: unused variable 'x'") is None

    def test_error_in_filename_not_failed(self):
        """A filename containing 'error' should not trigger failed — it's a compiling line."""
        result = _parse_progress("Compiling .pio/build/tbeam/src/error_handler.cpp.o")
        assert result != "failed"  # Should be "compiling", not "failed"

    def test_unrelated_line_returns_none(self):
        assert _parse_progress("Installing ESP32 platform") is None
        assert _parse_progress("") is None


class TestBuildContext:
    """Tests for BuildContext initialization."""

    def test_build_dir_created(self, temp_dir):
        from mtfwbuilder.config import Settings

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
        from mtfwbuilder.config import Settings

        settings = Settings(temp_dir=temp_dir)
        variant = DeviceVariant(id="tbeam", name="T-Beam", manufacturer="LILYGO", architecture="esp32")
        ctx = BuildContext(
            build_id="build_123_456",
            variant=variant,
            config_content='{"test": true}',
            settings=settings,
        )
        assert ctx.build_log == []


class TestScrubFirmwareTree:
    """Tests for sensitive file cleanup."""

    def test_removes_userprefs(self, temp_dir):
        from mtfwbuilder.config import Settings

        firmware_dir = temp_dir / "firmware"
        configs_dir = firmware_dir / "configs"
        configs_dir.mkdir(parents=True)
        (configs_dir / "userPrefs.jsonc").write_text('{"PSK": "secret"}')
        (firmware_dir / "userPrefs.jsonc").write_text('{"PSK": "secret"}')

        # Create PIO build dir
        build_dir = firmware_dir / ".pio" / "build" / "tbeam"
        build_dir.mkdir(parents=True)
        (build_dir / "firmware.bin").write_bytes(b"\x00" * 100)

        settings = Settings(firmware_dir=firmware_dir, temp_dir=temp_dir)
        variant = DeviceVariant(id="tbeam", name="T-Beam", manufacturer="LILYGO", architecture="esp32")
        ctx = BuildContext(
            build_id="build_test",
            variant=variant,
            config_content="{}",
            settings=settings,
        )

        _scrub_firmware_tree(ctx)

        assert not (configs_dir / "userPrefs.jsonc").exists()
        assert not (firmware_dir / "userPrefs.jsonc").exists()
        assert not (build_dir / "firmware.bin").exists()


class TestCleanupService:
    """Tests for build directory cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_build_directory(self, temp_dir):
        build_dir = temp_dir / "build_123"
        build_dir.mkdir()
        (build_dir / "firmware.bin").write_bytes(b"\x00")

        await cleanup_build_directory(build_dir)
        assert not build_dir.exists()

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent_is_noop(self, temp_dir):
        await cleanup_build_directory(temp_dir / "nonexistent")

    def test_cleanup_old_builds_removes_expired(self, temp_dir):
        from mtfwbuilder.config import Settings

        # Create an "old" build dir (timestamp 0)
        old_build = temp_dir / "build_1000000_123"
        old_build.mkdir()
        (old_build / "firmware.bin").write_bytes(b"\x00")

        settings = Settings(temp_dir=temp_dir, build_max_age_seconds=1)

        removed = cleanup_old_builds(settings)
        assert removed == 1
        assert not old_build.exists()

    def test_cleanup_old_builds_keeps_recent(self, temp_dir):
        import time

        from mtfwbuilder.config import Settings

        recent = temp_dir / f"build_{int(time.time())}_123"
        recent.mkdir()

        settings = Settings(temp_dir=temp_dir, build_max_age_seconds=3600)

        removed = cleanup_old_builds(settings)
        assert removed == 0
        assert recent.exists()


class TestFirmwareRoutes:
    """Tests for firmware builder API routes."""

    @pytest.fixture
    async def client(self):
        app = create_app()
        # Manually set up app state that lifespan would provide
        from mtfwbuilder.config import load_settings
        from mtfwbuilder.services.device_registry import DeviceRegistry
        from mtfwbuilder.services.build_service import init_build_system

        settings = load_settings()
        app.state.settings = settings
        app.state.device_registry = DeviceRegistry(settings.devices_file)
        init_build_system(settings)
        app.state.active_builds = {}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    @pytest.mark.asyncio
    async def test_build_unknown_variant(self, client):
        resp = await client.post(
            "/api/v1/build-firmware",
            data={"variant": "nonexistent-xyz", "config_source": "current", "config_json": "{}"},
        )
        assert resp.status_code == 400
        assert "Unknown device variant" in resp.json()["detail"]

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
    async def test_build_valid_request_returns_build_id(self, client):
        config = json.dumps({"device_name": "TestNode"})
        resp = await client.post(
            "/api/v1/build-firmware",
            data={"variant": "tbeam", "config_source": "current", "config_json": config},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "build_id" in data
        assert data["build_id"].startswith("build_")

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
