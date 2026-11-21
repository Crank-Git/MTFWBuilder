"""Tests for the build records, the cleanup task, the update lock and startup recovery (feature 06)."""

import asyncio
import hashlib
import hmac
import json
import os
import threading
import time

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.database import init_db
from mtfwbuilder.main import lifespan
from mtfwbuilder.routers import admin
from mtfwbuilder.services.cleanup_service import cleanup_old_builds
from tests.conftest import sign_in_admin

PSK = "deadbeef" * 8
CONFIG = json.dumps({"device_name": "TestNode", "channel_0_psk": PSK})
ENDED = {"complete", "failed", "cancelled"}


@pytest.fixture
def firmware_source(settings):
    """Return the firmware source directory, after it is created."""
    settings.firmware_dir.mkdir(parents=True)
    return settings.firmware_dir


@pytest.fixture
async def database(settings):
    """Return the database path, after the tables are created."""
    await init_db(settings)
    return settings.database_path


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def workers(app, database):
    """Start the build workers of the app, and stop them after the test."""
    app.state.build_queue.start()
    yield app.state.build_queue
    await app.state.build_queue.stop()


async def _rows(database_path, sql="SELECT * FROM builds", params=()):
    async with aiosqlite.connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(sql, params)
        return [dict(row) for row in await cursor.fetchall()]


async def _wait_for(ctx, statuses, timeout=10.0):
    deadline = time.monotonic() + timeout
    while ctx.status not in statuses:
        assert time.monotonic() < deadline, f"build stayed at {ctx.status}"
        await asyncio.sleep(0.02)


async def _wait_for_row(database_path, build_id, statuses, timeout=10.0):
    deadline = time.monotonic() + timeout
    while True:
        rows = await _rows(database_path, "SELECT * FROM builds WHERE id = ?", (build_id,))
        if rows and rows[0]["status"] in statuses:
            return rows[0]
        assert time.monotonic() < deadline, f"row is {rows}"
        await asyncio.sleep(0.02)


def _make_old(path, age_seconds):
    then = time.time() - age_seconds
    os.utime(path, (then, then))


# FR-build-pipeline-repair-14


async def test_init_db_adds_config_hash_and_firmware_version_to_an_old_builds_table(settings):
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            "CREATE TABLE builds (id TEXT PRIMARY KEY, variant TEXT NOT NULL, architecture TEXT NOT NULL, "
            "firmware_format TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', firmware_path TEXT, "
            "build_log TEXT, error_message TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "completed_at TIMESTAMP)"
        )
        await db.commit()

    await init_db(settings)

    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute("PRAGMA table_info(builds)")
        columns = {row[1] for row in await cursor.fetchall()}
    assert {"config_hash", "firmware_version"} <= columns


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_build_record_has_the_end_status_and_no_log_and_no_config(app, database, settings):
    ctx = app.state.build_queue.submit(app.state.device_registry.get("heltec-v3"), CONFIG)
    await _wait_for(ctx, ENDED)

    row = await _wait_for_row(database, ctx.build_id, ENDED)

    assert row["status"] == "complete"
    assert row["variant"] == "heltec-v3"
    assert row["build_log"] is None
    assert row["completed_at"] is not None
    assert row["config_hash"] == hmac.new(settings.secret_key.encode(), CONFIG.encode(), hashlib.sha256).hexdigest()
    assert not any(PSK in str(value) or CONFIG in str(value) for value in row.values())


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_build_record_shows_compiling_while_pio_runs(app, database, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "1")
    ctx = app.state.build_queue.submit(app.state.device_registry.get("heltec-v3"), CONFIG)
    await _wait_for(ctx, {"compiling"})

    row = await _wait_for_row(database, ctx.build_id, {"compiling"})

    assert row["status"] == "compiling"
    await _wait_for(ctx, ENDED)


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_failed_build_record_holds_the_error(app, database, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_FAIL", "1")
    ctx = app.state.build_queue.submit(app.state.device_registry.get("heltec-v3"), CONFIG)
    await _wait_for(ctx, ENDED)

    row = await _wait_for_row(database, ctx.build_id, ENDED)

    assert row["status"] == "failed"
    assert row["error_message"] == ctx.error


# Startup recovery (edge case: process restart during a build)


async def test_after_restart_a_build_that_was_compiling_shows_failed(settings, database, firmware_source, app):
    async with aiosqlite.connect(database) as db:
        await db.execute(
            "INSERT INTO builds (id, variant, architecture, firmware_format, status) VALUES (?, ?, ?, ?, ?)",
            ("a" * 32, "heltec-v3", "esp32", "bin", "compiling"),
        )
        await db.execute(
            "INSERT INTO builds (id, variant, architecture, firmware_format, status) VALUES (?, ?, ?, ?, ?)",
            ("b" * 32, "heltec-v3", "esp32", "bin", "complete"),
        )
        await db.commit()

    async with lifespan(app):
        rows = {row["id"]: row for row in await _rows(database)}

    assert rows["a" * 32]["status"] == "failed"
    assert rows["a" * 32]["error_message"] == "Server restarted"
    assert rows["b" * 32]["status"] == "complete"


async def test_startup_scrubs_the_firmware_source(firmware_source, app):
    (firmware_source / "userPrefs.jsonc").write_text(CONFIG)
    env_build = firmware_source / ".pio" / "build" / "heltec-v3"
    env_build.mkdir(parents=True)
    (env_build / "main.o").write_bytes(PSK.encode())
    build_cache = firmware_source / ".pio" / "build_cache"
    build_cache.mkdir()

    async with lifespan(app):
        pass

    assert not (firmware_source / "userPrefs.jsonc").exists()
    assert not env_build.exists()
    assert not build_cache.exists()


# FR-build-pipeline-repair-15


def test_cleanup_removes_an_artifact_directory_older_than_the_max_age(settings):
    old = settings.temp_dir / ("c" * 32)
    old.mkdir(parents=True)
    (old / "firmware-heltec-v3-2.7.26.54e0d8d.bin").write_bytes(b"\x00")
    _make_old(old, settings.build_max_age_seconds + 60)
    recent = settings.temp_dir / ("d" * 32)
    recent.mkdir()

    removed = cleanup_old_builds(settings)

    assert removed == 1
    assert not old.exists()
    assert recent.exists()


def test_cleanup_keeps_the_directory_of_a_build_that_has_not_ended(settings):
    active = settings.temp_dir / ("e" * 32)
    active.mkdir(parents=True)
    _make_old(active, settings.build_max_age_seconds + 60)

    removed = cleanup_old_builds(settings, keep={"e" * 32})

    assert removed == 0
    assert active.exists()


def test_cleanup_keeps_an_old_directory_whose_name_is_not_a_build_id(settings):
    others = [settings.temp_dir / name for name in ("operator-files", "A" * 32, "a" * 31)]
    for other in others:
        other.mkdir(parents=True)
        _make_old(other, settings.build_max_age_seconds + 60)

    removed = cleanup_old_builds(settings)

    assert removed == 0
    assert all(other.exists() for other in others)


async def test_cleanup_task_runs_every_cleanup_interval(settings_env, monkeypatch, app):
    monkeypatch.setenv("MTFW_CLEANUP_INTERVAL_SECONDS", "1")
    # The settings_env directory is the data directory, and build directories default to `artifacts/` in it.
    temp_dir = settings_env / "artifacts"

    async with lifespan(app):
        old = temp_dir / ("f" * 32)
        old.mkdir(parents=True)
        _make_old(old, 7200)
        deadline = time.monotonic() + 5
        while old.exists():
            assert time.monotonic() < deadline, "the cleanup task did not remove the old artifact directory"
            await asyncio.sleep(0.05)


# FR-build-pipeline-repair-16


def _sign_in(client, settings):
    client.token = sign_in_admin(client, settings)


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_firmware_update_waits_until_no_build_runs(app, client, settings, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "1")
    statuses_at_update = []
    ctx = app.state.build_queue.submit(app.state.device_registry.get("heltec-v3"), CONFIG)
    monkeypatch.setattr(admin, "update_firmware", lambda _settings: statuses_at_update.append(ctx.status) or True)
    await _wait_for(ctx, {"compiling"})
    _sign_in(client, settings)

    resp = await client.post("/api/v1/update-firmware", data={"csrf_token": client.token})

    assert resp.json()["success"] is True
    assert statuses_at_update == ["complete"]


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_no_build_starts_while_a_firmware_update_runs(app, client, settings, monkeypatch):
    release = threading.Event()
    started = threading.Event()

    def slow_update(_settings):
        started.set()
        release.wait(5)
        return True

    monkeypatch.setattr(admin, "update_firmware", slow_update)
    _sign_in(client, settings)
    update = asyncio.create_task(client.post("/api/v1/update-firmware", data={"csrf_token": client.token}))
    await asyncio.to_thread(started.wait, 5)

    ctx = app.state.build_queue.submit(app.state.device_registry.get("heltec-v3"), CONFIG)
    await asyncio.sleep(0.3)
    status_during_update = ctx.status
    release.set()
    await update
    await _wait_for(ctx, ENDED)

    assert status_during_update == "queued"
    assert ctx.status == "complete", ctx.error


@pytest.mark.usefixtures("firmware_source")
async def test_cancelled_update_request_keeps_the_lock_until_the_update_thread_ends(app, client, settings, monkeypatch):
    release = threading.Event()
    started = threading.Event()

    def slow_update(_settings):
        started.set()
        release.wait(5)
        return True

    monkeypatch.setattr(admin, "update_firmware", slow_update)
    _sign_in(client, settings)
    lock = app.state.build_queue.firmware_lock
    update = asyncio.create_task(client.post("/api/v1/update-firmware", data={"csrf_token": client.token}))
    await asyncio.to_thread(started.wait, 5)

    update.cancel()
    await asyncio.gather(update, return_exceptions=True)
    locked_after_cancel = lock.locked()
    release.set()
    deadline = time.monotonic() + 5
    while lock.locked():
        assert time.monotonic() < deadline, "the lock stayed held after the update thread ended"
        await asyncio.sleep(0.02)

    assert locked_after_cancel is True
