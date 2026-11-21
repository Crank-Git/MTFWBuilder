"""Tests for more than one build worker, each with its own firmware source (feature 06, issue #88)."""

import asyncio
import json
import time
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.auth import CSRF_HEADER
from mtfwbuilder.config import load_settings
from mtfwbuilder.database import record_firmware_source
from mtfwbuilder.services import firmware_updater
from mtfwbuilder.services.build_service import COPY_VERSION_FILE, FirmwareLock, worker_firmware_source
from mtfwbuilder.services.cleanup_service import scrub_after_restart, scrub_sources_after_restart
from tests.conftest import FIRMWARE_VERSION, fixture_variant, sign_in_admin

ENDED = {"complete", "failed", "cancelled"}
OWNER_KEY = "USERPREFS_CONFIG_OWNER_LONG_NAME"
OTHER_VERSION = "v2.8.2.d5f1619"


@pytest.fixture
def settings(settings_env, monkeypatch):
    """Return settings with two build workers."""
    monkeypatch.setenv("MTFW_BUILD_WORKERS", "2")
    return load_settings()


@pytest.fixture
def source(firmware_source):
    """Return the installed firmware source `<firmware_dir>/<tag>/`, with a `platformio.ini`."""
    (firmware_source / "platformio.ini").write_text("[platformio]\n")
    return firmware_source


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


def _record(settings, version, installed_at):
    """Return the directory of a new stable firmware source of `version`, after its record is written."""
    source_dir = settings.firmware_dir / version
    source_dir.mkdir(parents=True)
    (source_dir / "platformio.ini").write_text(f"[platformio]\nversion = {version}\n")
    record = {
        "version": version,
        "channel": "stable",
        "path": str(source_dir),
        "installed_at": installed_at,
        "size_bytes": 0,
        "status": "ready",
    }
    record_firmware_source(settings.database_path, record)
    return source_dir


def _fake_download(_tag, zip_path):
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("top/platformio.ini", "[platformio]\n")


async def _wait_until(ready, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not ready():
        assert time.monotonic() < deadline, "the condition stayed false"
        await asyncio.sleep(0.02)


def _owner_flags(ctx):
    return [line for line in ctx.build_log if line.startswith(f"-D{OWNER_KEY}=")]


@pytest.mark.usefixtures("fake_pio", "source", "workers")
async def test_two_builds_of_different_variants_run_at_the_same_time_with_two_build_workers(app, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "1")
    first = await app.state.build_queue.submit(fixture_variant("heltec-v3"), _owner_config("Alpha"))
    second = await app.state.build_queue.submit(fixture_variant("rak4631"), _owner_config("Bravo"))

    await _wait_until(lambda: first.status == "compiling" and second.status == "compiling")

    await _wait_until(lambda: first.ended and second.ended)
    assert first.status == "complete", first.error
    assert second.status == "complete", second.error


@pytest.mark.usefixtures("fake_pio", "workers")
async def test_each_build_compiles_its_own_userprefs_file(app, source, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "0.5")
    first = await app.state.build_queue.submit(fixture_variant("heltec-v3"), _owner_config("Alpha"))
    second = await app.state.build_queue.submit(fixture_variant("rak4631"), _owner_config("Bravo"))

    await _wait_until(lambda: first.ended and second.ended)

    assert _owner_flags(first) == [f'-D{OWNER_KEY}="Alpha"']
    assert _owner_flags(second) == [f'-D{OWNER_KEY}="Bravo"']
    assert {first.source_dir, second.source_dir} == {source, source / ".worker-1"}


@pytest.mark.usefixtures("fake_pio", "source", "workers")
async def test_each_build_scrubs_its_own_firmware_source(app, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "0.5")
    builds = [
        await app.state.build_queue.submit(fixture_variant("heltec-v3"), _owner_config("Alpha")),
        await app.state.build_queue.submit(fixture_variant("rak4631"), _owner_config("Bravo")),
    ]

    await _wait_until(lambda: all(ctx.ended for ctx in builds))

    for ctx in builds:
        assert ctx.status == "complete", ctx.error
        assert not (ctx.source_dir / "userPrefs.jsonc").exists()
        assert not (ctx.source_dir / ".pio" / "build" / ctx.variant.id).exists()
        assert (ctx.build_dir / f"firmware-{ctx.variant.id}-2.7.26.54e0d8d.mt.json").exists()


@pytest.mark.usefixtures("fake_pio", "source", "workers")
async def test_firmware_install_waits_until_both_running_builds_end(app, client, settings, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "1")
    monkeypatch.setattr(firmware_updater, "download_zipball", _fake_download)
    app.state.firmware_installs.resolver = {"alpha": OTHER_VERSION}.__getitem__
    builds = [
        await app.state.build_queue.submit(fixture_variant("heltec-v3"), _owner_config("Alpha")),
        await app.state.build_queue.submit(fixture_variant("rak4631"), _owner_config("Bravo")),
    ]
    statuses_at_move = []
    record = firmware_updater.record_firmware_source

    def record_after_status(*args):
        statuses_at_move.extend(c.status for c in builds)
        record(*args)

    monkeypatch.setattr(firmware_updater, "record_firmware_source", record_after_status)
    await _wait_until(lambda: all(ctx.status == "compiling" for ctx in builds))
    client.headers[CSRF_HEADER] = sign_in_admin(client, settings)

    resp = await client.post("/api/v1/firmware/install", json={"channel": "alpha"})
    install = app.state.firmware_installs.get(resp.json()["install_id"])
    await asyncio.wait_for(install.wait(), 10)

    assert install.status == "ready", install.error
    assert statuses_at_move == ["complete", "complete"]


# Rule: the copy of build worker <n> is `<firmware_dir>/<tag>/.worker-<n>/`, in the directory of its firmware version.


@pytest.mark.usefixtures("fake_pio", "source", "workers")
async def test_two_builds_of_different_firmware_versions_with_two_build_workers_do_not_share_a_copy(
    app, settings, monkeypatch
):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "1")
    first = await app.state.build_queue.submit(fixture_variant("heltec-v3"), _owner_config("Alpha"))
    await _wait_until(lambda: first.status == "compiling")
    # The newest stable firmware source is the default, so the second build reads the other firmware version.
    other = _record(settings, OTHER_VERSION, "2026-11-20T00:00:00+00:00")
    second = await app.state.build_queue.submit(fixture_variant("rak4631"), _owner_config("Bravo"))

    await _wait_until(lambda: first.ended and second.ended)

    assert first.status == "complete", first.error
    assert second.status == "complete", second.error
    assert (first.firmware_version, second.firmware_version) == (FIRMWARE_VERSION, OTHER_VERSION)
    assert first.source_dir != second.source_dir
    assert first.source_dir.is_relative_to(settings.firmware_dir / FIRMWARE_VERSION)
    assert second.source_dir.is_relative_to(other)


class TestWorkerFirmwareSource:
    def test_build_worker_0_uses_the_firmware_source_itself(self, source):
        assert worker_firmware_source(source, 0) == source

    def test_copy_is_in_the_directory_of_its_firmware_version(self, source):
        assert worker_firmware_source(source, 1) == source / ".worker-1"

    def test_copy_holds_the_packages_but_no_secret_of_a_running_build(self, source):
        (source / "userPrefs.jsonc").write_text('{"USERPREFS_CHANNEL_0_PSK": "secret"}')
        (source / ".pio" / "build" / "heltec-v3").mkdir(parents=True)
        (source / ".pio" / "build_cache").mkdir()
        (source / ".pio" / "libdeps" / "heltec-v3").mkdir(parents=True)
        (source / ".worker-2").mkdir()

        copy = worker_firmware_source(source, 1)

        assert (copy / "platformio.ini").read_text() == "[platformio]\n"
        assert (copy / ".pio" / "libdeps" / "heltec-v3").is_dir()
        assert not (copy / "userPrefs.jsonc").exists()
        assert not (copy / ".pio" / "build").exists()
        assert not (copy / ".pio" / "build_cache").exists()
        assert not (copy / ".worker-2").exists()

    def test_copy_stays_while_its_version_file_is_complete(self, source):
        copy = worker_firmware_source(source, 1)
        (copy / "kept.txt").write_text("kept")

        assert worker_firmware_source(source, 1) == copy
        assert (copy / "kept.txt").exists()

    def test_copy_without_its_version_file_is_made_again(self, source):
        copy = worker_firmware_source(source, 1)
        (copy / COPY_VERSION_FILE).unlink()
        (copy / "partial.txt").write_text("partial")

        worker_firmware_source(source, 1)

        assert not (copy / "partial.txt").exists()
        assert (copy / COPY_VERSION_FILE).read_text() == FIRMWARE_VERSION

    def test_one_build_worker_keeps_a_separate_copy_for_each_firmware_version(self, settings, source):
        other = _record(settings, OTHER_VERSION, "2026-11-20T00:00:00+00:00")

        first = worker_firmware_source(source, 1)
        second = worker_firmware_source(other, 1)

        assert (first, second) == (source / ".worker-1", other / ".worker-1")
        assert (first / "platformio.ini").read_text() == "[platformio]\n"
        assert (second / "platformio.ini").read_text() == f"[platformio]\nversion = {OTHER_VERSION}\n"

    def test_copy_that_the_startup_import_moved_is_made_again(self, source):
        # The old layout wrote the firmware version and the update time into the version file.
        old_copy = source / ".worker-1"
        old_copy.mkdir()
        (old_copy / COPY_VERSION_FILE).write_text(f"{FIRMWARE_VERSION} 2026-11-01")
        (old_copy / "old.txt").write_text("old")

        worker_firmware_source(source, 1)

        assert not (old_copy / "old.txt").exists()


def test_scrub_after_restart_scrubs_each_build_worker_copy(tmp_path):
    copy = tmp_path / ".worker-1"
    (copy / ".pio" / "build" / "rak4631").mkdir(parents=True)
    (copy / ".pio" / "build_cache").mkdir()
    (copy / "userPrefs.jsonc").write_text("{}")

    scrub_after_restart(tmp_path)

    assert not (copy / "userPrefs.jsonc").exists()
    assert not (copy / ".pio" / "build" / "rak4631").exists()
    assert not (copy / ".pio" / "build_cache").exists()


def test_startup_scrub_scrubs_each_build_worker_copy_in_each_firmware_version(tmp_path):
    copies = [tmp_path / FIRMWARE_VERSION / ".worker-1", tmp_path / OTHER_VERSION / ".worker-1"]
    for copy in copies:
        (copy / ".pio" / "build" / "rak4631").mkdir(parents=True)
        (copy / "userPrefs.jsonc").write_text("{}")

    scrub_sources_after_restart(tmp_path)

    for copy in copies:
        assert not (copy / "userPrefs.jsonc").exists()
        assert not (copy / ".pio" / "build" / "rak4631").exists()


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

    async def test_exclusive_hold_waits_for_each_build_and_ends_with_its_block(self):
        lock = FirmwareLock()
        held = []

        async def update():
            async with lock.exclusive():
                held.append(lock.locked())

        async with lock.shared():
            task = asyncio.create_task(update())
            await asyncio.sleep(0.05)
            assert held == []
        await asyncio.wait_for(task, 1)
        assert held == [True]
        assert not lock.locked()

    async def test_exclusive_hold_ends_after_an_error_in_its_block(self):
        lock = FirmwareLock()
        with pytest.raises(OSError):
            async with lock.exclusive():
                raise OSError
        assert not lock.locked()

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
