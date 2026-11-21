"""Tests for more than one build worker, each with its own firmware source (feature 06, issue #88)."""

import asyncio
import json
import time

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.auth import SESSION_COOKIE, create_session_token
from mtfwbuilder.config import load_settings
from mtfwbuilder.routers import admin
from mtfwbuilder.services.build_service import FirmwareLock, worker_firmware_source
from mtfwbuilder.services.cleanup_service import scrub_after_restart

ENDED = {"complete", "failed", "cancelled"}
OWNER_KEY = "USERPREFS_CONFIG_OWNER_LONG_NAME"


@pytest.fixture
def settings(settings_env, monkeypatch):
    """Return settings with two build workers and an installed firmware source."""
    monkeypatch.setenv("MTFW_BUILD_WORKERS", "2")
    settings = load_settings()
    settings.firmware_dir.mkdir(parents=True)
    (settings.firmware_dir / "platformio.ini").write_text("[platformio]\n")
    return settings


@pytest.fixture
async def workers(app):
    app.state.build_queue.start()
    yield app.state.build_queue
    await app.state.build_queue.stop()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _owner_config(name):
    return json.dumps({OWNER_KEY: name})


async def _wait_until(ready, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not ready():
        assert time.monotonic() < deadline, "the condition stayed false"
        await asyncio.sleep(0.02)


def _owner_flags(ctx):
    return [line for line in ctx.build_log if line.startswith(f"-D{OWNER_KEY}=")]


@pytest.mark.usefixtures("fake_pio", "workers")
async def test_two_builds_of_different_variants_run_at_the_same_time_with_two_build_workers(app, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "1")
    registry = app.state.device_registry
    first = app.state.build_queue.submit(registry.get("heltec-v3"), _owner_config("Alpha"))
    second = app.state.build_queue.submit(registry.get("rak4631"), _owner_config("Bravo"))

    await _wait_until(lambda: first.status == "compiling" and second.status == "compiling")

    await _wait_until(lambda: first.ended and second.ended)
    assert first.status == "complete", first.error
    assert second.status == "complete", second.error


@pytest.mark.usefixtures("fake_pio", "workers")
async def test_each_build_compiles_its_own_userprefs_file(app, settings, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "0.5")
    registry = app.state.device_registry
    first = app.state.build_queue.submit(registry.get("heltec-v3"), _owner_config("Alpha"))
    second = app.state.build_queue.submit(registry.get("rak4631"), _owner_config("Bravo"))

    await _wait_until(lambda: first.ended and second.ended)

    assert _owner_flags(first) == [f'-D{OWNER_KEY}="Alpha"']
    assert _owner_flags(second) == [f'-D{OWNER_KEY}="Bravo"']
    assert {first.source_dir, second.source_dir} == {settings.firmware_dir, settings.firmware_dir / ".worker-1"}


@pytest.mark.usefixtures("fake_pio", "workers")
async def test_each_build_scrubs_its_own_firmware_source(app, settings, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "0.5")
    registry = app.state.device_registry
    builds = [
        app.state.build_queue.submit(registry.get("heltec-v3"), _owner_config("Alpha")),
        app.state.build_queue.submit(registry.get("rak4631"), _owner_config("Bravo")),
    ]

    await _wait_until(lambda: all(ctx.ended for ctx in builds))

    for ctx in builds:
        assert ctx.status == "complete", ctx.error
        assert not (ctx.source_dir / "userPrefs.jsonc").exists()
        assert not (ctx.source_dir / ".pio" / "build" / ctx.variant.id).exists()
        assert (ctx.build_dir / f"firmware-{ctx.variant.id}-2.7.26.54e0d8d.mt.json").exists()


@pytest.mark.usefixtures("fake_pio", "workers")
async def test_firmware_update_waits_until_both_running_builds_end(app, client, settings, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "1")
    registry = app.state.device_registry
    builds = [
        app.state.build_queue.submit(registry.get("heltec-v3"), _owner_config("Alpha")),
        app.state.build_queue.submit(registry.get("rak4631"), _owner_config("Bravo")),
    ]
    statuses_at_update = []
    monkeypatch.setattr(
        admin, "update_firmware", lambda _settings: statuses_at_update.extend(c.status for c in builds) or True
    )
    await _wait_until(lambda: all(ctx.status == "compiling" for ctx in builds))
    client.cookies.set(SESSION_COOKIE, create_session_token(settings))

    resp = await client.post("/api/v1/update-firmware")

    assert resp.json()["success"] is True
    assert statuses_at_update == ["complete", "complete"]


class TestWorkerFirmwareSource:
    def test_build_worker_0_uses_the_firmware_source_itself(self, settings):
        assert worker_firmware_source(settings, 0) == settings.firmware_dir

    def test_copy_holds_the_packages_but_no_secret_of_a_running_build(self, settings):
        source = settings.firmware_dir
        (source / "userPrefs.jsonc").write_text('{"USERPREFS_CHANNEL_0_PSK": "secret"}')
        (source / ".pio" / "build" / "heltec-v3").mkdir(parents=True)
        (source / ".pio" / "build_cache").mkdir()
        (source / ".pio" / "libdeps" / "heltec-v3").mkdir(parents=True)
        (source / ".worker-2").mkdir()

        copy = worker_firmware_source(settings, 1)

        assert copy == source / ".worker-1"
        assert (copy / "platformio.ini").read_text() == "[platformio]\n"
        assert (copy / ".pio" / "libdeps" / "heltec-v3").is_dir()
        assert not (copy / "userPrefs.jsonc").exists()
        assert not (copy / ".pio" / "build").exists()
        assert not (copy / ".pio" / "build_cache").exists()
        assert not (copy / ".worker-2").exists()

    def test_copy_stays_while_the_firmware_version_stays(self, settings):
        copy = worker_firmware_source(settings, 1)
        (copy / "kept.txt").write_text("kept")

        assert worker_firmware_source(settings, 1) == copy
        assert (copy / "kept.txt").exists()

    def test_copy_is_made_again_after_an_install_of_another_firmware_version(self, settings):
        copy = worker_firmware_source(settings, 1)
        (settings.firmware_dir / "platformio.ini").write_text("[platformio]\nnew = 1\n")
        (settings.data_dir / "firmware_version.txt").write_text("Version: v2.8.0\nUpdated: 2026-11-21\n")

        worker_firmware_source(settings, 1)

        assert (copy / "platformio.ini").read_text() == "[platformio]\nnew = 1\n"


def test_scrub_after_restart_scrubs_each_build_worker_copy(tmp_path):
    copy = tmp_path / ".worker-1"
    (copy / ".pio" / "build" / "rak4631").mkdir(parents=True)
    (copy / ".pio" / "build_cache").mkdir()
    (copy / "userPrefs.jsonc").write_text("{}")

    scrub_after_restart(tmp_path)

    assert not (copy / "userPrefs.jsonc").exists()
    assert not (copy / ".pio" / "build" / "rak4631").exists()
    assert not (copy / ".pio" / "build_cache").exists()


class TestFirmwareLock:
    async def test_two_builds_hold_the_lock_together(self):
        lock = FirmwareLock()
        async with lock.shared(), lock.shared():
            assert not lock.locked()

    async def test_update_waits_until_each_build_releases_its_hold(self):
        lock = FirmwareLock()
        released = []
        async with lock.shared():
            update = asyncio.create_task(lock.acquire())
            await asyncio.sleep(0.05)
            released.append(update.done())
        await asyncio.wait_for(update, 1)
        assert released == [False]
        assert lock.locked()

    async def test_no_build_starts_while_an_update_waits_or_runs(self):
        lock = FirmwareLock()
        started = []

        async def build():
            async with lock.shared():
                started.append(lock.locked())

        async with lock.shared():
            update = asyncio.create_task(lock.acquire())
            await asyncio.sleep(0.05)
            late_build = asyncio.create_task(build())
            await asyncio.sleep(0.05)
            assert started == []
        await asyncio.wait_for(update, 1)
        await asyncio.sleep(0.05)
        assert started == []
        lock.release()
        await asyncio.wait_for(late_build, 1)
        assert started == [False]

    async def test_cancelled_update_lets_a_waiting_build_start(self):
        lock = FirmwareLock()
        async with lock.shared():
            update = asyncio.create_task(lock.acquire())
            await asyncio.sleep(0.05)
            build = asyncio.create_task(_hold(lock.shared()))
            await asyncio.sleep(0.05)
            update.cancel()
            await asyncio.gather(update, return_exceptions=True)
            await asyncio.wait_for(build, 1)


async def _hold(context):
    async with context:
        pass
